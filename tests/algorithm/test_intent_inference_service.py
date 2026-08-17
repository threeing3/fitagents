from fastapi.testclient import TestClient

from algorithm.inference.intent_service import IntentRequest, create_app


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
