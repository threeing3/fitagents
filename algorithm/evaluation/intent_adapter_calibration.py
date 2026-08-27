"""Calibrate field routing on the isolated intent development partition."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

from fast_api.app.services.intent_decision import IntentRouter

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


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
    row: dict[str, Any], rule: Any, latency_ms: float, detail: str
) -> dict[str, Any]:
    """Count an invalid structured generation without aborting the calibration run."""
    return {
        "case_id": row["case_id"],
        "category": row["category"],
        "confidence": {"primary_intent": 0.0, "secondary_intents": 0.0},
        "correct": {"primary_intent": False, "secondary_intents": False},
        "risk_floor_preserved": RISK_ORDER.get(rule.risk_level, 0)
        >= RISK_ORDER[row["minimum_risk_level"]],
        "latency_ms": round(latency_ms, 2),
        "model_version": None,
        "model_valid": False,
        "fallback_source": "deterministic_rule",
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
                    detail = str(response.json().get("detail", "unprocessable intent output"))
                except (ValueError, AttributeError):
                    detail = "unprocessable intent output"
                records.append(invalid_model_record(row, rule, latency_ms, detail))
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
                    },
                    "risk_floor_preserved": RISK_ORDER.get(decision.get("risk_level"), 0)
                    >= RISK_ORDER[row["minimum_risk_level"]],
                    "latency_ms": round(latency_ms, 2),
                    "model_version": payload.get("model_version"),
                    "model_valid": True,
                    "fallback_source": None,
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
        "schema_version": "fitagent-intent-field-calibration/v1",
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
