from types import SimpleNamespace

from algorithm.evaluation.intent_adapter_calibration import (
    diagnostic_metrics,
    invalid_model_record,
    threshold_curve,
)


def test_threshold_curve_prefers_accurate_covered_region():
    rows = [
        {
            "confidence": {"primary_intent": confidence},
            "correct": {"primary_intent": confidence >= 0.8},
        }
        for confidence in [0.55, 0.6, 0.7, 0.79, 0.8, 0.82, 0.85, 0.9, 0.92, 0.95] * 2
    ]

    result = threshold_curve(rows, "primary_intent", target_accuracy=0.85)

    assert result["target_met"] is True
    assert result["selected_threshold"] == 0.75
    assert result["selected_accuracy"] == 0.8571


def test_invalid_model_output_is_recorded_for_rule_fallback():
    row = {
        "case_id": "dev-17",
        "category": "multi_intent",
        "expected_primary_intent": "training_plan",
        "required_secondary_intents": ["recovery_check"],
        "minimum_risk_level": "low",
        "expected_clarification": True,
    }
    rule = SimpleNamespace(risk_level="medium")

    record = invalid_model_record(row, rule, 123.456, "Model returned invalid intent JSON.")

    assert record["model_valid"] is False
    assert record["confidence"] == {"primary_intent": 0.0, "secondary_intents": 0.0}
    assert record["correct"] == {
        "primary_intent": False,
        "secondary_intents": False,
        "risk_level": False,
        "clarification": False,
    }
    assert record["risk_floor_preserved"] is False
    assert record["post_fallback_risk_floor_preserved"] is True
    assert record["predicted_primary_intent"] is None
    assert record["parse_error_code"] == "invalid_model_json"
    assert record["fallback_applied"] is True
    assert record["retry_count"] == 0
    assert record["fallback_source"] == "deterministic_rule"


def test_diagnostic_metrics_include_confusions_and_multilabel_scores():
    records = [
        {
            "expected_primary_intent": "training_plan",
            "predicted_primary_intent": "training_plan",
            "expected_secondary_intents": ["recovery_check"],
            "predicted_secondary_intents": ["recovery_check"],
            "expected_minimum_risk_level": "low",
            "predicted_risk_level": "low",
            "correct": {"clarification": True},
            "parse_error_code": None,
            "fallback_applied": False,
            "retry_count": 0,
        },
        {
            "expected_primary_intent": "injury_or_risk",
            "predicted_primary_intent": None,
            "expected_secondary_intents": [],
            "predicted_secondary_intents": None,
            "expected_minimum_risk_level": "high",
            "predicted_risk_level": None,
            "correct": {"clarification": False},
            "parse_error_code": "invalid_model_json",
            "fallback_applied": True,
            "retry_count": 0,
        },
    ]

    metrics = diagnostic_metrics(records)

    assert metrics["primary_confusion_matrix"]["rows"]["injury_or_risk"]["__invalid__"] == 1
    assert metrics["risk_confusion_matrix"]["rows"]["high"]["__invalid__"] == 1
    assert metrics["secondary_intents"]["per_label"]["recovery_check"]["f1"] == 1.0
    assert metrics["clarification_accuracy"] == 0.5
    assert metrics["parse_error_counts"] == {"invalid_model_json": 1}
    assert metrics["fallback_count"] == 1
