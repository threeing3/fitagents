from algorithm.evaluation.intent_adapter_calibration import threshold_curve


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
