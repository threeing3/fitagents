import pytest

from algorithm.evaluation.intent_local_model_eval import (
    evaluate_predictions,
    merge_deterministic_safety,
    parse_intent_json,
)
from fast_api.app.services.intent_decision import IntentDecision


def test_parse_intent_json_accepts_fenced_strict_payload():
    decision = parse_intent_json(
        '```json\n{"primary_intent":"training_plan","secondary_intents":[],"risk_level":"low",'
        '"needs_clarification":false,"reason_codes":["plan"]}\n```'
    )
    assert decision.primary_intent == "training_plan"
    assert decision.needs_clarification is False


def test_parse_intent_json_rejects_wrong_schema():
    with pytest.raises(ValueError, match="secondary_intents"):
        parse_intent_json(
            '{"primary_intent":"training_plan","risk_level":"low","needs_clarification":false}'
        )


def test_safety_merge_raises_risk_floor_without_hiding_model_choice():
    model = IntentDecision(primary_intent="training_plan", risk_level="low")
    rule = IntentDecision(primary_intent="injury_or_risk", risk_level="critical")
    merged = merge_deterministic_safety(model, rule)
    assert merged.primary_intent == "injury_or_risk"
    assert merged.risk_level == "critical"
    assert "training_plan" in merged.secondary_intents


def test_evaluation_reports_raw_and_safety_merged_metrics_separately():
    rows = [
        {
            "case_id": "risk-1",
            "user_message": "我胸痛并且呼吸困难",
            "expected_primary_intent": "injury_or_risk",
            "required_secondary_intents": [],
            "minimum_risk_level": "high",
            "expected_clarification": False,
        }
    ]
    predictions = [
        {
            "text": '{"primary_intent":"training_plan","secondary_intents":[],"risk_level":"low",'
            '"needs_clarification":false,"reason_codes":[]}',
            "latency_ms": 12,
        }
    ]
    report = evaluate_predictions(rows, predictions)
    assert report["raw_model"]["exact_match"] == 0.0
    assert report["with_deterministic_safety"]["component_scores"]["risk_level"] == 1.0


def test_invalid_model_output_is_visible_and_uses_rule_fallback():
    rows = [
        {
            "case_id": "invalid-risk",
            "user_message": "我胸痛并且呼吸困难",
            "expected_primary_intent": "injury_or_risk",
            "required_secondary_intents": [],
            "minimum_risk_level": "high",
            "expected_clarification": True,
        }
    ]
    report = evaluate_predictions(rows, [{"text": "not-json", "latency_ms": 1}])
    assert report["raw_model"]["schema_valid_rate"] == 0.0
    assert report["invalid_output_rule_fallback_count"] == 1
    assert report["with_deterministic_safety"]["component_scores"]["risk_level"] == 1.0
