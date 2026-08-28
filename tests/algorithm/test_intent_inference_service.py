from pathlib import Path

from fastapi.testclient import TestClient

from algorithm.inference.intent_catalog import AgentIntentCatalog
from algorithm.inference.intent_service import (
    IntentOutputError,
    IntentRequest,
    QwenIntentPredictor,
    create_app,
    field_token_confidence,
)

VALID_TEXT = (
    '{"primary_intent":"training_plan","secondary_intents":[],"risk_level":"low",'
    '"needs_clarification":false,"reason_codes":[]}'
)


class AttemptPredictor(QwenIntentPredictor):
    def __init__(self, outputs: list[str]):
        super().__init__("fixture", Path("adapter"))
        self.outputs = iter(outputs)
        self.calls: list[str | None] = []

    def _generate_attempt(self, request, repair_source=None):
        self.calls.append(repair_source)
        return next(self.outputs), {"primary_intent": 0.8, "secondary_intents": 0.7}, {
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }


class FakePredictor:
    model_version = "qwen3-4b-intent-fixture"

    def __init__(self):
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def predict(self, request: IntentRequest):
        assert request.rule_decision["primary_intent"] == "injury_or_risk"
        return {
            "primary_intent": "injury_or_risk",
            "secondary_intents": ["training_plan"],
            "risk_level": "high",
            "needs_clarification": True,
            "confidence": 0.9,
            "reason": "risk_context",
        }, {"prompt_tokens": 30, "completion_tokens": 20}


def test_dependency_light_catalog_contains_the_public_contract():
    assert "injury_or_risk" in AgentIntentCatalog.VALID_INTENTS
    assert "training_plan" in AgentIntentCatalog.VALID_INTENTS
    assert len(AgentIntentCatalog.VALID_INTENTS) == 16


def test_service_loads_once_and_serves_authenticated_contract():
    predictor = FakePredictor()
    app = create_app(predictor, inference_key="test-secret")
    with TestClient(app) as client:
        assert predictor.loaded is True
        assert client.get("/health/live").json() == {"status": "live"}
        ready = client.get("/health/ready")
        assert ready.json()["model_version"] == predictor.model_version
        assert client.post("/v1/intent/classify", json={"message": "膝盖疼"}).status_code == 401
        response = client.post(
            "/v1/intent/classify",
            headers={"Authorization": "Bearer test-secret"},
            json={
                "message": "我膝盖疼，但还能继续深蹲吗？",
                "rule_decision": {"primary_intent": "injury_or_risk"},
                "profile_summary": {"goal": "增肌"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "intent_decision_v2"
    assert payload["decision"]["primary_intent"] == "injury_or_risk"
    assert payload["model_version"] == predictor.model_version
    assert payload["usage"] == {"prompt_tokens": 30, "completion_tokens": 20}
    assert isinstance(payload["latency_ms"], int)


def test_service_refuses_start_without_inference_key(monkeypatch):
    monkeypatch.delenv("INTENT_INFERENCE_KEY", raising=False)
    app = create_app(FakePredictor(), inference_key="")

    try:
        with TestClient(app):
            raise AssertionError("service unexpectedly started")
    except RuntimeError as exc:
        assert "INTENT_INFERENCE_KEY" in str(exc)


def test_context_projection_drops_unapproved_fields_and_rejects_oversized_values():
    request = IntentRequest(
        message="测试",
        rule_decision={"primary_intent": "general_chat", "raw_message": "private"},
        profile_summary={"goal": "增肌", "email": "private@example.com"},
    )

    context = QwenIntentPredictor._bounded_context(request)

    assert context["profile"]["goal"] == "增肌"
    assert "email" not in context["profile"]
    assert "raw_message" not in context["rule_decision"]

    oversized = IntentRequest(
        message="测试",
        rule_decision={"primary_intent": "general_chat"},
        profile_summary={"injuries": "x" * 7000},
    )
    try:
        QwenIntentPredictor._bounded_context(oversized)
        raise AssertionError("oversized context unexpectedly passed")
    except ValueError as exc:
        assert "exceeds" in str(exc)


def test_field_token_confidence_is_calculated_for_each_json_field():
    text = '{"primary_intent":"training_plan","secondary_intents":["memory_query"]}'
    prefixes = [text[:30], text[:50], text]
    confidence = field_token_confidence(text, prefixes, [-0.1, -0.2, -0.3])

    assert 0 < confidence["primary_intent"] <= 1
    assert 0 < confidence["secondary_intents"] <= 1


def test_predictor_returns_without_retry_for_valid_first_attempt():
    predictor = AttemptPredictor([VALID_TEXT])

    decision, usage = predictor.predict(IntentRequest(message="制定计划"))

    assert decision["primary_intent"] == "training_plan"
    assert usage["retry_count"] == 0
    assert predictor.calls == [None]


def test_predictor_repairs_invalid_json_once_and_accounts_for_usage():
    predictor = AttemptPredictor(["not-json", VALID_TEXT])

    decision, usage = predictor.predict(IntentRequest(message="制定计划"))

    assert decision["primary_intent"] == "training_plan"
    assert usage == {"prompt_tokens": 20, "completion_tokens": 10, "retry_count": 1}
    assert predictor.calls == [None, "not-json"]


def test_predictor_stops_after_one_failed_repair():
    predictor = AttemptPredictor(["not-json", "still-not-json"])

    try:
        predictor.predict(IntentRequest(message="制定计划"))
        raise AssertionError("invalid repair unexpectedly passed")
    except IntentOutputError as exc:
        assert exc.code == "invalid_model_json_after_repair"
        assert exc.retry_count == 1
    assert len(predictor.calls) == 2
