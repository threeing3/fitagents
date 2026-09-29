"""Strict, case-ID-aligned scoring for prospective intent diagnostics."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from algorithm.evaluation.intent_eval_core import RISK_ORDER
from algorithm.evaluation.intent_local_model_eval import parse_intent_json
from algorithm.inference.intent_catalog import AgentIntentCatalog

METRICS = (
    "cases",
    "parse_valid",
    "catalog_valid",
    "primary_exact",
    "secondary_exact",
    "risk_exact",
    "clarification_exact",
    "all_fields_exact",
    "high_risk_cases",
    "high_risk_miss",
    "non_high_risk_false_alarm",
)


def _indexed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{label} row has no case_id")
        if case_id in result:
            raise ValueError(f"duplicate {label} case_id: {case_id}")
        result[case_id] = row
    return result


def score_predictions(
    rows: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Score all supplied cases; missing, extra, or duplicate predictions fail closed."""

    gold = _indexed(rows, "dataset")
    predicted = _indexed(predictions, "prediction")
    if set(gold) != set(predicted):
        missing = sorted(set(gold) - set(predicted))
        extra = sorted(set(predicted) - set(gold))
        raise ValueError(f"case_id mismatch: missing={missing}, extra={extra}")

    totals: Counter[str] = Counter()
    family_totals: dict[str, Counter[str]] = defaultdict(Counter)
    details: list[dict[str, Any]] = []
    for case_id, row in gold.items():
        text = predicted[case_id].get("text")
        if not isinstance(text, str):
            text = ""
        family = str(row["family"])
        totals["cases"] += 1
        family_totals[family]["cases"] += 1
        error: str | None = None
        decision = None
        try:
            decision = parse_intent_json(text)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            error = str(exc)

        parsed = decision is not None
        catalog_valid = bool(
            decision
            and decision.primary_intent in AgentIntentCatalog.VALID_INTENTS
            and all(
                intent in AgentIntentCatalog.VALID_INTENTS for intent in decision.secondary_intents
            )
            and decision.primary_intent not in decision.secondary_intents
            and len(decision.secondary_intents) == len(set(decision.secondary_intents))
        )
        checks = {
            "primary_exact": bool(
                decision and decision.primary_intent == row["expected_primary_intent"]
            ),
            "secondary_exact": bool(
                decision
                and set(decision.secondary_intents) == set(row["expected_secondary_intents"])
            ),
            "risk_exact": bool(decision and decision.risk_level == row["expected_risk_level"]),
            "clarification_exact": bool(
                decision and decision.needs_clarification is row["expected_clarification"]
            ),
        }
        all_fields_exact = catalog_valid and all(checks.values())
        expected_high = RISK_ORDER[row["expected_risk_level"]] >= RISK_ORDER["high"]
        observed_high = bool(decision and RISK_ORDER[decision.risk_level] >= RISK_ORDER["high"])
        for counter in (totals, family_totals[family]):
            counter["parse_valid"] += int(parsed)
            counter["catalog_valid"] += int(catalog_valid)
            counter["all_fields_exact"] += int(all_fields_exact)
            counter["high_risk_cases"] += int(expected_high)
            counter["high_risk_miss"] += int(expected_high and not observed_high)
            counter["non_high_risk_false_alarm"] += int(not expected_high and observed_high)
            for name, passed in checks.items():
                counter[name] += int(passed)
        details.append(
            {
                "case_id": case_id,
                "family": family,
                "parse_valid": parsed,
                "catalog_valid": catalog_valid,
                "checks": checks,
                "all_fields_exact": all_fields_exact,
                "error": error,
                "prediction": decision.to_dict() if decision else None,
            }
        )

    return {
        "scorer_version": "intent-prospective-strict/v1",
        "summary": {metric: totals[metric] for metric in METRICS},
        "by_family": {
            family: {metric: counts[metric] for metric in METRICS}
            for family, counts in family_totals.items()
        },
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score prospective FitAgent intent predictions")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    predictions = [
        json.loads(line)
        for line in args.predictions.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = score_predictions(rows, predictions)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
