"""Strict prospective scoring must not repeat the historical loose checks."""

import pytest

from algorithm.evaluation.intent_prospective_score import score_predictions


def _row(case_id: str = "p1") -> dict:
    return {
        "case_id": case_id,
        "family": "contrast",
        "expected_primary_intent": "training_log",
        "expected_secondary_intents": ["memory_query"],
        "expected_risk_level": "low",
        "expected_clarification": False,
    }


def _prediction(case_id: str, primary: str, secondary: list[str], risk: str = "low") -> dict:
    import json

    return {
        "case_id": case_id,
        "text": json.dumps(
            {
                "primary_intent": primary,
                "secondary_intents": secondary,
                "risk_level": risk,
                "needs_clarification": False,
                "reason_codes": [],
            }
        ),
    }


def test_exact_secondary_set_rejects_extra_intent() -> None:
    report = score_predictions(
        [_row()],
        [_prediction("p1", "training_log", ["memory_query", "training_plan"])],
    )
    detail = report["details"][0]
    assert detail["catalog_valid"] is True
    assert detail["checks"]["secondary_exact"] is False
    assert report["summary"]["all_fields_exact"] == 0


def test_exact_risk_and_catalog_are_separate_from_parse_success() -> None:
    report = score_predictions([_row()], [_prediction("p1", "sleep_tracking", [], "high")])
    detail = report["details"][0]
    assert detail["parse_valid"] is True
    assert detail["catalog_valid"] is False
    assert detail["checks"]["risk_exact"] is False
    assert report["summary"]["non_high_risk_false_alarm"] == 1
    assert report["summary"]["all_fields_exact"] == 0


def test_reordered_case_ids_score_by_id_not_position() -> None:
    rows = [_row("p1"), _row("p2")]
    predictions = [
        _prediction("p2", "training_log", ["memory_query"]),
        _prediction("p1", "training_log", ["memory_query"]),
    ]
    report = score_predictions(rows, predictions)
    assert report["summary"]["all_fields_exact"] == 2
    assert [detail["case_id"] for detail in report["details"]] == ["p1", "p2"]


@pytest.mark.parametrize(
    "predictions",
    [
        [],
        [_prediction("p2", "training_log", ["memory_query"])],
        [
            _prediction("p1", "training_log", ["memory_query"]),
            _prediction("p1", "training_log", ["memory_query"]),
        ],
    ],
)
def test_missing_extra_or_duplicate_case_id_is_rejected(predictions: list[dict]) -> None:
    with pytest.raises(ValueError):
        score_predictions([_row()], predictions)


def test_unparseable_output_remains_in_denominator_and_misses_high_risk() -> None:
    row = _row()
    row["expected_risk_level"] = "high"
    report = score_predictions([row], [{"case_id": "p1", "text": "not-json"}])
    assert report["summary"]["cases"] == 1
    assert report["summary"]["parse_valid"] == 0
    assert report["summary"]["high_risk_miss"] == 1
