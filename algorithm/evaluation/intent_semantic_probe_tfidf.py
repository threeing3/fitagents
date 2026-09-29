"""Compare the frozen v3.1 TF-IDF intent baseline on the synthetic probe."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Literal

from algorithm.app_algorithms.multilabel_intent_baseline import (
    TfidfIntentBaseline,
    calibrate_secondary_thresholds,
)
from algorithm.evaluation.intent_semantic_probe import DEFAULT_INPUT, load_cases
from algorithm.evaluation.multilabel_data_audit import (
    select_eligible_calibration_rows,
    select_eligible_train_rows,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRAIN_DATA = ROOT / "algorithm/datasets/generated/intent_multilabel_v3_1_20260922.jsonl"


def intent_checks(row: dict[str, Any], prediction: dict[str, Any]) -> dict[str, bool]:
    predicted = {prediction["primary_intent"], *prediction["secondary_intents"]}
    return {
        "required_intents": set(row["required_intents"]).issubset(predicted),
        "forbidden_intents": not set(row["forbidden_intents"]).intersection(predicted),
    }


def run_probe(
    cases: list[dict[str, Any]],
    dataset_rows: list[dict[str, Any]],
    *,
    head_target: Literal["secondary_only", "any_intent"] = "secondary_only",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    train_rows = select_eligible_train_rows(dataset_rows)
    calibration_rows = select_eligible_calibration_rows(dataset_rows)
    if not train_rows or not calibration_rows:
        raise ValueError("both training and isolated calibration rows are required")
    decisions = [json.loads(row["assistant_response"]) for row in train_rows]
    labels = sorted(
        {
            label
            for decision in decisions
            for label in (
                decision["secondary_intents"]
                + ([decision["primary_intent"]] if head_target == "any_intent" else [])
            )
        }
    )
    started = time.perf_counter()
    model = TfidfIntentBaseline(seed=42, head_target=head_target)
    model.fit(train_rows, labels)
    thresholds = calibrate_secondary_thresholds(model, calibration_rows, labels)
    predictions = model.predict(cases, thresholds=thresholds)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    details: list[dict[str, Any]] = []
    for row, prediction in zip(cases, predictions):
        checks = intent_checks(row, prediction)
        details.append(
            {
                "case_id": row["case_id"],
                "family": row["family"],
                "user_message": row["user_message"],
                "required_intents": row["required_intents"],
                "forbidden_intents": row["forbidden_intents"],
                "predicted_primary_intent": prediction["primary_intent"],
                "predicted_secondary_intents": prediction["secondary_intents"],
                "secondary_probabilities": prediction["secondary_probabilities"],
                "checks": checks,
                "intent_checks_pass": all(checks.values()),
            }
        )
    summary = {
        "schema_version": "fitagent-intent-semantic-tfidf-probe/v1",
        "partition": "development_diagnostic",
        "source": "assistant_authored_synthetic",
        "human_review_status": "not_reviewed",
        "training_eligible": False,
        "model_training_data": "intent-multilabel-v3.1-20260922",
        "train_rows": len(train_rows),
        "calibration_rows": len(calibration_rows),
        "cases": len(cases),
        "head_target": head_target,
        "secondary_labels": labels,
        "head_positive_counts": {
            label: sum(
                label in decision["secondary_intents"]
                or (head_target == "any_intent" and label == decision["primary_intent"])
                for decision in decisions
            )
            for label in labels
        },
        "threshold_source": "isolated_v3.1_validation",
        "thresholds": thresholds,
        "intent_checks_passed": sum(item["intent_checks_pass"] for item in details),
        "required_intents_passed": sum(item["checks"]["required_intents"] for item in details),
        "forbidden_intents_passed": sum(item["checks"]["forbidden_intents"] for item in details),
        "failure_case_ids": [item["case_id"] for item in details if not item["intent_checks_pass"]],
        "fit_calibrate_predict_ms": elapsed_ms,
        "claim_boundary": "Intent-only synthetic diagnostic; no risk, action, or production claim.",
    }
    return details, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_TRAIN_DATA)
    parser.add_argument(
        "--head-target", choices=("secondary_only", "any_intent"), default="secondary_only"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    _, cases = load_cases(args.input)
    dataset_rows = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    details, summary = run_probe(cases, dataset_rows, head_target=args.head_target)
    details_path = args.output_dir / "cases.jsonl"
    summary_path = args.output_dir / "summary.json"
    for path in (details_path, summary_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite prior diagnostic: {path}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    details_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in details),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
