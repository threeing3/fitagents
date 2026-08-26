from fast_api.app.services.field_confidence_router import FieldConfidenceRouter
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter


def test_field_router_requests_review_only_for_low_confidence_fields():
    router = FieldConfidenceRouter()
    plan = router.plan(
        {"confidence": {"overall": 0.8, "primary_intent": 0.92, "secondary_intents": 0.41}}
    )

    assert plan.low_confidence_fields == ["secondary_intents"]
    assert plan.requested_sources["primary_intent"] == "intent_adapter"
    assert plan.requested_sources["risk_level"] == "deterministic_rule_floor"
    assert plan.requested_sources["needs_clarification"] == "clarification_protocol"


def test_field_router_merges_only_reviewed_semantic_field():
    router = FieldConfidenceRouter()
    plan = router.plan({"confidence": {"primary_intent": 0.9, "secondary_intents": 0.4}})
    adapter = IntentDecision("training_plan", [], confidence=0.9)
    deepseek = IntentDecision("general_chat", ["recovery_check"], confidence=0.8)
    rule = IntentDecision("training_plan", [])

    merged, sources = router.merge(adapter, deepseek, rule, plan, IntentRouter())

    assert merged.primary_intent == "training_plan"
    assert merged.secondary_intents == ["recovery_check"]
    assert sources["primary_intent"] == "intent_adapter"
    assert sources["secondary_intents"] == "deepseek"
