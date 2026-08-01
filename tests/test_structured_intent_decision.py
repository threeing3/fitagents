from pathlib import Path
import json
from types import SimpleNamespace

from fast_api.app.services.intent_decision import IntentRouter

ROOT = Path(__file__).resolve().parents[1]


def complete_profile():
    return SimpleNamespace(
        age=28,
        height_cm=175,
        weight_kg=76,
        goal="fat_loss",
        experience_level="intermediate",
        equipment_available=["gym"],
    )


def test_multi_intent_risk_overrides_plan_generation():
    router = IntentRouter()

    decision = router.analyze(
        "I did bench 60kg for 4 sets today, my shoulder has pain, should I train chest tomorrow?"
    )

    assert decision.primary_intent == "injury_or_risk"
    assert "training_plan" in decision.secondary_intents
    assert "training_log" in decision.secondary_intents
    assert decision.risk_level == "medium"
    assert "shoulder" in decision.entities["body_parts"]
    assert decision.entities["weight_kg"] == 60
    assert decision.allowed_actions["generate_plan"] is False
    assert decision.needs_clarification is True


def test_negated_plan_request_blocks_plan_generation():
    router = IntentRouter()

    decision = router.analyze(
        "Do not generate a training plan yet, just explain why my bench progress stalled recently"
    )

    assert decision.primary_intent == "progression_decision"
    assert "training_plan" not in decision.secondary_intents
    assert decision.allowed_actions["generate_plan"] is False


def test_complete_profile_allows_clear_training_plan_request():
    router = IntentRouter()

    decision = router.analyze("Please create a one week training plan", profile=complete_profile())

    assert decision.primary_intent == "training_plan"
    assert decision.needs_clarification is False
    assert decision.allowed_actions["generate_plan"] is True


def test_incomplete_profile_requires_clarification_before_plan_generation():
    router = IntentRouter()

    decision = router.analyze("Please create a one week training plan", profile=SimpleNamespace(goal="fat_loss"))

    assert decision.primary_intent == "training_plan"
    assert decision.allowed_actions["generate_plan"] is False
    assert decision.needs_clarification is True
    assert "age" in decision.missing_slots


def test_context_builder_exposes_structured_intent_fields_static():
    content = (ROOT / "fast_api" / "app" / "services" / "context_builder.py").read_text(encoding="utf-8")

    assert '"intent_decision": intent_decision.to_dict()' in content
    assert '"secondary_intents": intent_decision.secondary_intents' in content
    assert '"intent_entities": intent_decision.entities' in content
    assert '"needs_clarification": intent_decision.needs_clarification' in content


def test_intent_eval_cases_json():
    cases = json.loads((ROOT / "tests" / "evals" / "intent_eval_cases.json").read_text(encoding="utf-8"))
    router = IntentRouter()

    for case in cases:
        profile = None
        if case.get("profile") == "complete":
            profile = complete_profile()
        elif case.get("profile") == "incomplete":
            profile = SimpleNamespace(goal="fat_loss")
        decision = router.analyze(case["input"], profile=profile)

        assert decision.primary_intent == case["expected_primary_intent"], case["name"]
        for expected in case["expected_secondary_intents"]:
            assert expected in decision.secondary_intents, case["name"]
        assert decision.risk_level == case["expected_risk_level"], case["name"]
        assert decision.allowed_actions["generate_plan"] is case["expected_generate_plan"], case["name"]
        assert decision.needs_clarification is case["expected_needs_clarification"], case["name"]
