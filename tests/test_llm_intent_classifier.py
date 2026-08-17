import asyncio
from types import SimpleNamespace

from fast_api.app.services.agent_pipeline_router import AgentPipelineRouter
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.llm_intent_classifier import LLMIntentClassifier


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

    def chat_model(self, temperature: float = 0.0):
        return self.model


class FakeIntentModelProvider(FakeModelProvider):
    def intent_model(self):
        return self.model

    def chat_model(self, temperature: float = 0.0):
        raise AssertionError("intent classification must prefer the bounded intent client")


def test_llm_intent_classifier_refines_complex_semantics_without_demoting_risk():
    payload = """
    {
      "primary_intent": "progression_decision",
      "secondary_intents": ["training_plan", "training_log"],
      "confidence": 0.91,
      "risk_level": "low",
      "entities": {"time_scope": "tomorrow"},
      "missing_slots": [],
      "needs_clarification": false,
      "reason": "User asks whether to change tomorrow after reporting training."
    }
    """
    rule_decision = IntentRouter().analyze(
        "I did bench 60kg today and my shoulder has pain. Should I change tomorrow?"
    )
    classifier = LLMIntentClassifier(FakeModelProvider(payload), IntentRouter())

    refined = asyncio.run(classifier.refine("message", rule_decision, profile=SimpleNamespace()))

    assert refined.primary_intent == "injury_or_risk"
    assert "progression_decision" in refined.secondary_intents
    assert "training_log" in refined.secondary_intents
    assert refined.risk_level == "medium"
    assert refined.task_plan[0]["intent"] == "injury_or_risk"
    assert any(step["status"] == "needs_clarification" for step in refined.task_plan)


def test_llm_intent_classifier_prefers_dedicated_intent_model():
    provider = FakeIntentModelProvider(
        '{"primary_intent":"general_chat","secondary_intents":[],"confidence":0.9,'
        '"risk_level":"low","entities":{},"missing_slots":[],'
        '"needs_clarification":false,"reason":"chat"}'
    )
    classifier = LLMIntentClassifier(provider, IntentRouter())

    decision, trace = asyncio.run(
        classifier.refine_with_trace(
            "这个事情我也说不清，你怎么看？",
            IntentRouter().analyze("这个事情我也说不清，你怎么看？"),
            force_refine=True,
        )
    )

    assert provider.model.calls == 1
    assert decision.primary_intent == "general_chat"
    assert trace["succeeded"] is True


def test_agent_pipeline_router_uses_llm_intent_refinement_for_ambiguous_turn():
    payload = """
    {
      "primary_intent": "progression_decision",
      "secondary_intents": ["training_log"],
      "confidence": 0.9,
      "risk_level": "low",
      "entities": {"time_scope": "tomorrow"},
      "missing_slots": [],
      "needs_clarification": false,
      "reason": "User wants a decision about changing tomorrow's training."
    }
    """
    router = AgentPipelineRouter(
        model_provider=FakeModelProvider(payload), intent_router=IntentRouter()
    )

    decision = asyncio.run(
        router.route("Yesterday bench felt off; should I change tomorrow's session?")
    )

    assert decision.intent == "progression_decision"
    assert decision.pipeline == "code_driven"
    assert decision.intent_decision["primary_intent"] == "progression_decision"
    assert decision.intent_decision["task_plan"][0]["action"] == "record_training_log"
    assert decision.intent_decision["task_plan"][1]["action"] == "decide_progression"
