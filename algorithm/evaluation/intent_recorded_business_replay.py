"""Replay saved real-model intent outputs through isolated offline business state."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from algorithm.evaluation.intent_full_chat_replay import replay_case
from fast_api.app.services.intent_inference_client import IntentInferenceClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "algorithm/datasets/fixtures/intent_prospective_diagnostic_v1.json"
BUSINESS_CASES = (
    PROJECT_ROOT / "algorithm/datasets/fixtures/intent_recorded_business_replay_v1.json"
)
PREDICTION_ROOT = PROJECT_ROOT / "logs/experiments/intent_size_compare_20260928"


def load_inputs(
    dataset_path: Path, business_path: Path, predictions: dict[str, Path]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    """Reject mismatched IDs, duplicate outputs, and invalid saved decisions."""

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset_by_id = {row["case_id"]: row for row in dataset}
    if len(dataset_by_id) != len(dataset):
        raise ValueError("Diagnostic dataset has duplicate case IDs")
    fixture = json.loads(business_path.read_text(encoding="utf-8"))
    if (
        fixture.get("schema_version") != "intent-recorded-business-replay/v1"
        or fixture.get("source") != "synthetic_self_authored_posthoc_diagnostic"
        or fixture.get("training_eligible") is not False
    ):
        raise ValueError("Business fixture must be post-hoc synthetic and non-training")
    cases = fixture["cases"]
    ids = [case["case_id"] for case in cases]
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= set(dataset_by_id):
        raise ValueError("Business case IDs must be unique and present in diagnostic dataset")

    indexed_predictions: dict[str, dict[str, dict[str, Any]]] = {}
    prompt_ids = set()
    expected_model_ids = {"4b": "Qwen3-4B-Q4_K_M", "14b": "Qwen3-14B-Q4_K_M"}
    for name, path in predictions.items():
        if name not in expected_model_ids:
            raise ValueError(f"Unknown recorded model source: {name}")
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        index = {row["case_id"]: row for row in rows}
        if len(index) != len(rows) or set(index) != set(dataset_by_id):
            raise ValueError(f"{name} prediction IDs must match the complete diagnostic set")
        payloads = {}
        for case_id, row in index.items():
            if row.get("model_id") != expected_model_ids[name]:
                raise ValueError(f"{name} model ID mismatch for {case_id}")
            payload = json.loads(row["text"])
            if not IntentInferenceClient._valid_decision(payload):
                raise ValueError(f"{name} invalid intent contract for {case_id}")
            payloads[case_id] = payload
            prompt_ids.add(row.get("prompt_id"))
        indexed_predictions[name] = payloads
    if prompt_ids != {"intent-size-compare-v1"}:
        raise ValueError("Saved predictions must use the same frozen prompt")

    replay_cases = [
        {
            "case_id": case["case_id"],
            "message": dataset_by_id[case["case_id"]]["user_message"],
            "expected": case["expected"],
            "expectation_basis": case["expectation_basis"],
        }
        for case in cases
    ]
    return replay_cases, indexed_predictions


def run_recorded_replay(
    output_dir: Path,
    dataset_path: Path = DATASET,
    business_path: Path = BUSINESS_CASES,
    predictions: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Write each case immediately and keep failed attempts in the result set."""

    if predictions is None:
        predictions = {
            "4b": PREDICTION_ROOT / "4b_full.jsonl",
            "14b": PREDICTION_ROOT / "14b_full.jsonl",
        }
    cases, indexed = load_inputs(dataset_path, business_path, predictions)
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite replay output: {output_dir}")
    output_dir.mkdir(parents=True)
    results: list[dict[str, Any]] = []
    with (output_dir / "run.log").open("x", encoding="utf-8") as log:
        for model_name in ("rule", *indexed):
            for case in cases:
                case_id = case["case_id"]
                log_dir = output_dir / "agent_logs" / model_name / case_id
                payload = None if model_name == "rule" else indexed[model_name][case_id]
                try:
                    outcome = replay_case(case, log_dir, recorded_model_payload=payload)
                except Exception as exc:
                    outcome = {"case_id": case_id, "passed": False, "error": repr(exc)}
                outcome["model_source"] = model_name
                outcome["expectation_basis"] = case["expectation_basis"]
                results.append(outcome)
                (output_dir / f"{model_name}_{case_id}.json").write_text(
                    json.dumps(outcome, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
                line = (
                    f"{datetime.now().isoformat(timespec='seconds')} "
                    f"{model_name} {case_id} passed={outcome['passed']} "
                    f"prediction_consumed={outcome.get('recorded_prediction_consumed')} "
                    f"error={outcome.get('error')}\n"
                )
                log.write(line)
                log.flush()
                print(line, end="", flush=True)

    summary = {
        "schema_version": "intent-recorded-business-replay-result/v1",
        "evidence_scope": "saved_real_model_predictions_injected_into_synthetic_offline_business_replay",
        "case_count_per_source": len(cases),
        "sources": {
            name: {
                "passed": sum(row["passed"] for row in results if row["model_source"] == name),
                "recorded_prediction_consumed": sum(
                    bool(row.get("recorded_prediction_consumed"))
                    for row in results
                    if row["model_source"] == name
                ),
                "failed_case_ids": [
                    row["case_id"]
                    for row in results
                    if row["model_source"] == name and not row["passed"]
                ],
            }
            for name in ("rule", *indexed)
        },
        "response_review_status": "not_reviewed",
        "output_dir": str(output_dir),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_recorded_replay(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
