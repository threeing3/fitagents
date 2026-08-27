from types import SimpleNamespace

from algorithm.evaluation.intent_adapter_calibration import invalid_model_record, threshold_curve


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
        "minimum_risk_level": "low",
    }
    rule = SimpleNamespace(risk_level="medium")

    record = invalid_model_record(row, rule, 123.456, "Model returned invalid intent JSON.")

    assert record["model_valid"] is False
    assert record["confidence"] == {"primary_intent": 0.0, "secondary_intents": 0.0}
    assert record["correct"] == {"primary_intent": False, "secondary_intents": False}
    assert record["risk_floor_preserved"] is True
    assert record["fallback_source"] == "deterministic_rule"
