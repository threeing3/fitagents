"""Check saved-prediction admission before isolated business replay."""

import json

import pytest

from algorithm.evaluation.intent_recorded_business_replay import (
    BUSINESS_CASES,
    DATASET,
    PREDICTION_ROOT,
    load_inputs,
    run_recorded_replay,
)


def test_saved_predictions_match_frozen_cases_and_prompt():
    cases, predictions = load_inputs(
        DATASET,
        BUSINESS_CASES,
        {
            "4b": PREDICTION_ROOT / "4b_full.jsonl",
            "14b": PREDICTION_ROOT / "14b_full.jsonl",
        },
    )
    assert len(cases) == 8
    assert set(predictions) == {"4b", "14b"}
    assert set(predictions["4b"]) == set(predictions["14b"])


def test_prediction_id_mismatch_fails_closed(tmp_path):
    original = (PREDICTION_ROOT / "4b_full.jsonl").read_text(encoding="utf-8").splitlines()
    truncated = tmp_path / "missing.jsonl"
    truncated.write_text("\n".join(original[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must match the complete diagnostic set"):
        load_inputs(DATASET, BUSINESS_CASES, {"4b": truncated})


def test_model_source_swap_fails_closed():
    with pytest.raises(ValueError, match="model ID mismatch"):
        load_inputs(
            DATASET,
            BUSINESS_CASES,
            {"4b": PREDICTION_ROOT / "14b_full.jsonl"},
        )


def test_replay_refuses_existing_output_before_execution(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        run_recorded_replay(output)
    assert list(output.iterdir()) == []


def test_fixture_contains_no_training_admission():
    fixture = json.loads(BUSINESS_CASES.read_text(encoding="utf-8"))
    assert fixture["training_eligible"] is False
    assert fixture["source"] == "synthetic_self_authored_posthoc_diagnostic"
    tomorrow_plan = next(case for case in fixture["cases"] if case["case_id"] == "intent-prosp-011")
    assert tomorrow_plan["expected"]["reply_contains"] == ["明天", "慢跑"]
