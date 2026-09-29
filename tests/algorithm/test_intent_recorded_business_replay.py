"""Check saved-prediction admission before isolated business replay."""

import json

import pytest

from algorithm.evaluation.intent_recorded_business_replay import (
    BUSINESS_CASES,
    DATASET,
    load_inputs,
    run_recorded_replay,
)


@pytest.fixture
def synthetic_predictions(tmp_path):
    """Build contract-valid records without relying on ignored local model logs."""

    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    predictions = {}
    for source, model_id in (
        ("4b", "Qwen3-4B-Q4_K_M"),
        ("14b", "Qwen3-14B-Q4_K_M"),
    ):
        path = tmp_path / f"{source}_full.jsonl"
        rows = [
            {
                "case_id": case["case_id"],
                "model_id": model_id,
                "prompt_id": "intent-size-compare-v1",
                "text": json.dumps(
                    {
                        "primary_intent": "training_log",
                        "secondary_intents": [],
                        "risk_level": "low",
                        "needs_clarification": False,
                        "reason_codes": [],
                    }
                ),
            }
            for case in cases
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        predictions[source] = path
    return predictions


def test_synthetic_predictions_match_frozen_cases_and_prompt(synthetic_predictions):
    cases, predictions = load_inputs(
        DATASET,
        BUSINESS_CASES,
        synthetic_predictions,
    )
    assert len(cases) == 8
    assert set(predictions) == {"4b", "14b"}
    assert set(predictions["4b"]) == set(predictions["14b"])


def test_prediction_id_mismatch_fails_closed(tmp_path, synthetic_predictions):
    original = synthetic_predictions["4b"].read_text(encoding="utf-8").splitlines()
    truncated = tmp_path / "missing.jsonl"
    truncated.write_text("\n".join(original[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must match the complete diagnostic set"):
        load_inputs(DATASET, BUSINESS_CASES, {"4b": truncated})


def test_model_source_swap_fails_closed(synthetic_predictions):
    with pytest.raises(ValueError, match="model ID mismatch"):
        load_inputs(
            DATASET,
            BUSINESS_CASES,
            {"4b": synthetic_predictions["14b"]},
        )


def test_replay_refuses_existing_output_before_execution(tmp_path, synthetic_predictions):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        run_recorded_replay(output, predictions=synthetic_predictions)
    assert list(output.iterdir()) == []


def test_fixture_contains_no_training_admission():
    fixture = json.loads(BUSINESS_CASES.read_text(encoding="utf-8"))
    assert fixture["training_eligible"] is False
    assert fixture["source"] == "synthetic_self_authored_posthoc_diagnostic"
    tomorrow_plan = next(case for case in fixture["cases"] if case["case_id"] == "intent-prosp-011")
    assert tomorrow_plan["expected"]["reply_contains"] == ["明天", "慢跑"]
