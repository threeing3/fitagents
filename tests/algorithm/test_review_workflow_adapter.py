import asyncio
import copy

import pytest

from algorithm.evaluation.review_workflow_adapter import existing_review_workflow


def packet():
    return {
        "review_window": {"start": "2026-09-21", "end": "2026-09-27"},
        "recent_training": [{"id": "workout-1", "performed_at": "2026-09-22T18:00:00", "rpe": 8}],
        "recent_recovery": [
            {
                "id": f"recovery-{i}",
                "date": f"2026-09-{22 + i}",
                "fatigue_score": 8,
                "sleep_hours": 5,
            }
            for i in range(3)
        ],
    }


def run(value):
    return asyncio.run(existing_review_workflow(value, "复盘", max_calls=15, timeout_seconds=90))


def test_existing_rule_builders_preserve_ids_and_never_modify_packet():
    value = packet()
    before = copy.deepcopy(value)
    outcome = run(value)
    assert value == before
    assert outcome["no_business_writes"] is True
    assert outcome["model_calls"] == 0 and outcome["model_called"] is False
    row = outcome["results"][0]
    assert row["adjustment_signal"]["eligible"] is True
    assert row["adjustment_signal"]["policy"] == "demo_fatigue_v1_not_clinical"
    assert "workouts=1" in row["summary"]
    assert "exercise_sets=unknown" in row["summary"]
    assert row["evidence_ids"] == ["recovery-0", "recovery-1", "recovery-2", "workout-1"]


def test_symptoms_block_rule_adjustment():
    value = packet()
    value["recent_symptoms"] = [{"id": "symptom-1", "date": "2026-09-24", "symptom_type": "pain"}]
    row = run(value)["results"][0]
    assert row["adjustment_signal"]["eligible"] is False
    assert row["adjustment_signal"]["reason"] == "symptoms_require_manual_review"


def test_invalid_snapshot_does_not_invent_source_ids_or_silently_change_window():
    value = packet()
    del value["recent_training"][0]["id"]
    with pytest.raises(ValueError, match="source IDs"):
        run(value)
    value = packet()
    value["recent_training"][0]["performed_at"] = "2026-08-01T18:00:00"
    with pytest.raises(ValueError, match="frozen review window"):
        run(value)


def test_empty_records_are_not_fabricated_or_scored_as_quality_success():
    row = run({"review_window": packet()["review_window"]})["results"][0]
    assert row["evidence_ids"] == []
    assert row["adjustment_signal"]["eligible"] is False
    assert row["recommendations"] == []
