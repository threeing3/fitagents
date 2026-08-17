import asyncio
from types import SimpleNamespace

from fast_api.app.services.intent_decision_engine import IntentDecisionEngine


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeChatModel:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return FakeMessage(self.content)


class FakeModelProvider:
    def __init__(self, content: str):
        self.model = FakeChatModel(content)
        self.settings = SimpleNamespace(llm_provider="deepseek", chat_model="deepseek-test")

    def chat_model(self, temperature: float = 0.0):
        return self.model


def test_engine_calls_model_once_and_preserves_rule_safety():
    provider = FakeModelProvider(
        """
        {
          "primary_intent": "training_plan",
          "secondary_intents": [],
          "confidence": 0.94,
          "risk_level": "low",
          "entities": {},
          "missing_slots": [],
          "needs_clarification": false,
          "reason": "User requests a training plan."
        }
        """
    )
    engine = IntentDecisionEngine(provider)

    result = asyncio.run(engine.decide("我胸闷但想继续冲刺训练"))
    payload = result.decision.to_dict()

    assert provider.model.calls == 1
    assert payload["schema_version"] == "intent_decision_v2"
    assert payload["primary_intent"] == "injury_or_risk"
    assert payload["risk"]["level"] == "high"
    assert payload["allowed_actions"]["generate_plan"] is False
    assert payload["provenance"]["deepseek_used"] is True
    assert payload["provenance"]["model_succeeded"] is True
    assert result.runtime_mode == "code_driven"


def test_engine_skips_model_when_rule_decision_is_confident_and_simple():
    provider = FakeModelProvider("{}")
    engine = IntentDecisionEngine(provider)

    result = asyncio.run(engine.decide("帮我制定一周训练计划"))

    assert provider.model.calls == 0
    assert result.decision.primary_intent == "training_plan"
    assert result.decision.provenance["final_source"] == "rule_fallback"
    assert result.decision.provenance["model_fallback_reason"] == "refinement_not_required"


def test_engine_reports_invalid_model_payload_without_claiming_model_success():
    provider = FakeModelProvider("not-json")
    engine = IntentDecisionEngine(provider)

    result = asyncio.run(engine.decide("这个情况我也说不清，你怎么看？"))

    assert provider.model.calls == 1
    assert result.decision.provenance["model_attempted"] is True
    assert result.decision.provenance["model_succeeded"] is False
    assert result.decision.provenance["deepseek_used"] is False
    assert result.decision.provenance["model_fallback_reason"] == "invalid_model_payload"
