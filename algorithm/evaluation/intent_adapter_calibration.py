"""Calibrate field routing on the isolated intent development partition."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

from fast_api.app.services.intent_decision import IntentRouter

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _confusion_matrix(
    records: list[dict[str, Any]], expected_field: str, predicted_field: str
) -> dict[str, Any]:
    """Build a JSON-serializable confusion matrix without external ML dependencies."""
    labels = sorted(
        {
            str(value)
            for record in records
            for value in (record.get(expected_field), record.get(predicted_field))
            if value is not None
        }
    )
    if any(record.get(predicted_field) is None for record in records):
        labels.append("__invalid__")
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        expected = str(record[expected_field])
        predicted = (
            str(record[predicted_field])
            if record.get(predicted_field) is not None
            else "__invalid__"
        )
        counts[expected][predicted] += 1
    return {
        "labels": labels,
        "rows": {
            expected: {predicted: counts[expected][predicted] for predicted in labels}
            for expected in labels
            if expected != "__invalid__"
        },
    }


def _secondary_label_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    labels = sorted(
        {
            label
            for record in records
            for field in ("expected_secondary_intents", "predicted_secondary_intents")
            for label in (record.get(field) or [])
        }
    )
    per_label: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for label in labels:
        true_positive = false_positive = false_negative = 0
        for record in records:
            expected = label in set(record.get("expected_secondary_intents") or [])
            predicted = label in set(record.get("predicted_secondary_intents") or [])
            true_positive += int(expected and predicted)
            false_positive += int(not expected and predicted)
            false_negative += int(expected and not predicted)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_label[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": true_positive + false_negative,
        }
    return {
        "macro_f1": round(sum(f1_values) / len(f1_values), 4) if f1_values else 0.0,
        "per_label": per_label,
    }


def diagnostic_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate predicted fields while keeping user messages out of artifacts."""
    parse_errors = Counter(
        record["parse_error_code"] for record in records if record.get("parse_error_code")
    )
    return {
        "primary_confusion_matrix": _confusion_matrix(
            records, "expected_primary_intent", "predicted_primary_intent"
        ),
        "secondary_intents": _secondary_label_metrics(records),
        "risk_confusion_matrix": _confusion_matrix(
            records, "expected_minimum_risk_level", "predicted_risk_level"
        ),
        "clarification_accuracy": round(
            sum(record["correct"]["clarification"] for record in records) / len(records), 4
        ),
        "parse_error_counts": dict(sorted(parse_errors.items())),
        "fallback_count": sum(record["fallback_applied"] for record in records),
        "retry_count": sum(record["retry_count"] for record in records),
    }


def threshold_curve(
    rows: list[dict[str, Any]], field_name: str, target_accuracy: float = 0.85
) -> dict[str, Any]:
    curve: list[dict[str, Any]] = []
    for integer_threshold in range(50, 100, 5):
        threshold = integer_threshold / 100
        accepted = [row for row in rows if row["confidence"][field_name] >= threshold]
        accuracy = (
            sum(row["correct"][field_name] for row in accepted) / len(accepted) if accepted else 0.0
        )
        curve.append(
            {
                "threshold": threshold,
                "coverage": round(len(accepted) / len(rows), 4),
                "accepted_accuracy": round(accuracy, 4),
                "accepted_cases": len(accepted),
            }
        )
    eligible = [
        point
        for point in curve
        if point["accepted_cases"] >= 10 and point["accepted_accuracy"] >= target_accuracy
    ]
    selected = (
        max(eligible, key=lambda point: point["coverage"])
        if eligible
        else max(curve, key=lambda point: (point["accepted_accuracy"], point["coverage"]))
    )
    return {
        "target_accuracy": target_accuracy,
        "target_met": bool(eligible),
        "selected_threshold": selected["threshold"],
        "selected_coverage": selected["coverage"],
        "selected_accuracy": selected["accepted_accuracy"],
        "curve": curve,
    }


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 4),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 4),
    }


def invalid_model_record(
    row: dict[str, Any],
    rule: Any,
    latency_ms: float,
    detail: str,
    *,
    parse_error_code: str = "invalid_model_json",
    retry_count: int = 0,
) -> dict[str, Any]:
    """Count an invalid structured generation without aborting the calibration run."""
    return {
        "case_id": row["case_id"],
        "category": row["category"],
        "confidence": {"primary_intent": 0.0, "secondary_intents": 0.0},
        "correct": {
            "primary_intent": False,
            "secondary_intents": False,
            "risk_level": False,
            "clarification": False,
        },
        "expected_primary_intent": row["expected_primary_intent"],
        "expected_secondary_intents": row["required_secondary_intents"],
        "expected_minimum_risk_level": row["minimum_risk_level"],
        "expected_needs_clarification": bool(row["expected_clarification"]),
        "predicted_primary_intent": None,
        "predicted_secondary_intents": None,
        "predicted_risk_level": None,
        "predicted_needs_clarification": None,
        "risk_floor_preserved": False,
        "post_fallback_risk_floor_preserved": RISK_ORDER.get(rule.risk_level, 0)
        >= RISK_ORDER[row["minimum_risk_level"]],
        "latency_ms": round(latency_ms, 2),
        "model_version": None,
        "model_valid": False,
        "fallback_applied": True,
        "fallback_source": "deterministic_rule",
        "retry_count": retry_count,
        "parse_error_code": parse_error_code,
        "error_type": "invalid_model_json",
        "error_detail": detail,
    }


def calibrate(base_url: str, dataset_path: Path, output_dir: Path, timeout: float) -> dict:
    key = os.getenv("INTENT_INFERENCE_KEY")
    if not key:
        raise RuntimeError("INTENT_INFERENCE_KEY is required through the environment")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if any(row.get("partition") != "development" for row in dataset):
        raise RuntimeError("Calibration accepts only the development partition")
    output_dir.mkdir(parents=True, exist_ok=False)
    router = IntentRouter()
    records: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for index, row in enumerate(dataset, start=1):
            rule = router.analyze(row["user_message"])
            started = time.perf_counter()
            response = client.post(
                f"{base_url.rstrip('/')}/v1/intent/classify",
                headers=headers,
                json={"message": row["user_message"], "rule_decision": rule.to_dict()},
            )
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 422:
                try:
                    response_detail = response.json().get("detail", {})
                    if isinstance(response_detail, dict):
                        error_code = str(response_detail.get("code", "invalid_model_json"))
                        retry_count = int(response_detail.get("retry_count", 0))
                        detail = error_code
                    else:
                        error_code = "invalid_model_json"
                        retry_count = 0
                        detail = str(response_detail)
                except (ValueError, AttributeError):
                    error_code = "invalid_model_json"
                    retry_count = 0
                    detail = "unprocessable intent output"
                records.append(
                    invalid_model_record(
                        row,
                        rule,
                        latency_ms,
                        detail,
                        parse_error_code=error_code,
                        retry_count=retry_count,
                    )
                )
                print(
                    json.dumps(
                        {"completed": index, "total": len(dataset), "model_valid": False}
                    ),
                    flush=True,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            decision = payload["decision"]
            confidence = decision.get("confidence") or {}
            if not isinstance(confidence, dict):
                raise RuntimeError("Service did not return field-level confidence")
            records.append(
                {
                    "case_id": row["case_id"],
                    "category": row["category"],
                    "confidence": {
                        "primary_intent": float(confidence.get("primary_intent", 0)),
                        "secondary_intents": float(confidence.get("secondary_intents", 0)),
                    },
                    "correct": {
                        "primary_intent": decision.get("primary_intent")
                        == row["expected_primary_intent"],
                        "secondary_intents": set(row["required_secondary_intents"]).issubset(
                            set(decision.get("secondary_intents") or [])
                        ),
                        "risk_level": RISK_ORDER.get(decision.get("risk_level"), -1)
                        >= RISK_ORDER[row["minimum_risk_level"]],
                        "clarification": decision.get("needs_clarification")
                        is bool(row["expected_clarification"]),
                    },
                    "expected_primary_intent": row["expected_primary_intent"],
                    "expected_secondary_intents": row["required_secondary_intents"],
                    "expected_minimum_risk_level": row["minimum_risk_level"],
                    "expected_needs_clarification": bool(row["expected_clarification"]),
                    "predicted_primary_intent": decision.get("primary_intent"),
                    "predicted_secondary_intents": decision.get("secondary_intents") or [],
                    "predicted_risk_level": decision.get("risk_level"),
                    "predicted_needs_clarification": decision.get("needs_clarification"),
                    "risk_floor_preserved": RISK_ORDER.get(decision.get("risk_level"), 0)
                    >= RISK_ORDER[row["minimum_risk_level"]],
                    "post_fallback_risk_floor_preserved": RISK_ORDER.get(
                        decision.get("risk_level"), 0
                    )
                    >= RISK_ORDER[row["minimum_risk_level"]],
                    "latency_ms": round(latency_ms, 2),
                    "model_version": payload.get("model_version"),
                    "model_valid": True,
                    "fallback_applied": False,
                    "fallback_source": None,
                    "retry_count": int((payload.get("usage") or {}).get("retry_count", 0)),
                    "parse_error_code": None,
                    "error_type": None,
                    "error_detail": None,
                }
            )
            print(json.dumps({"completed": index, "total": len(dataset)}), flush=True)

    (output_dir / "calibration_records.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    report = {
        "schema_version": "fitagent-intent-field-calibration/v2",
        "status": "development_diagnostic",
        "dataset": {
            "name": "intent_dev_v1",
            "cases": len(records),
            "partition": "development",
            "training_eligible": False,
            "contains_user_messages": False,
        },
        "confidence_method": "generated_token_probability_v1",
        "fields": {
            field_name: threshold_curve(records, field_name)
            for field_name in ("primary_intent", "secondary_intents")
        },
        "risk_floor_rate": round(
            sum(record["risk_floor_preserved"] for record in records) / len(records), 4
        ),
        "model_valid_rate": round(
            sum(record["model_valid"] for record in records) / len(records), 4
        ),
        "diagnostics": diagnostic_metrics(records),
        "latency_ms": _percentiles([record["latency_ms"] for record in records]),
        "claims": {
            "frozen_test_used_for_tuning": False,
            "production_uplift": False,
            "release_thresholds": False,
        },
        "limitations": [
            "Thresholds are development diagnostics until one frozen release evaluation passes.",
            "The curated development set has not completed independent human review.",
        ],
    }
    (output_dir / "calibration_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    print(
        json.dumps(
            calibrate(args.base_url, args.dataset, args.output_dir, args.timeout),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
