"""Audit whether the intent dataset can support held-out multi-label evaluation."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.inference.intent_catalog import AgentIntentCatalog


def _decision(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("assistant_response", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("assistant_response must contain an intent-decision object")
    return payload


def select_eligible_train_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the frozen training boundary before any audit or fitting."""

    return [
        row for row in rows if row.get("split") == "train" and bool(row.get("training_eligible"))
    ]


def audit_label_coverage(
    train_rows: list[dict[str, Any]], development_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    train_primary = Counter(str(_decision(row).get("primary_intent")) for row in train_rows)
    train_secondary: Counter[str] = Counter()
    for row in train_rows:
        train_secondary.update(str(label) for label in _decision(row).get("secondary_intents", []))

    development_primary = Counter(str(row["expected_primary_intent"]) for row in development_rows)
    development_secondary: Counter[str] = Counter()
    for row in development_rows:
        development_secondary.update(str(label) for label in row["required_secondary_intents"])

    valid = AgentIntentCatalog.VALID_INTENTS
    unseen_primary = sorted(set(development_primary) - set(train_primary))
    unseen_secondary = sorted(set(development_secondary) - set(train_secondary))
    invalid_train_primary = sorted(set(train_primary) - valid)
    invalid_train_secondary = sorted(set(train_secondary) - valid)
    development_multi_count = sum(
        bool(row.get("required_secondary_intents")) for row in development_rows
    )
    learnable_secondary_support = sum(
        count for label, count in development_secondary.items() if label in train_secondary
    )
    total_secondary_support = sum(development_secondary.values())

    return {
        "schema_version": "fitagent-multilabel-data-audit/v1",
        "train_rows": len(train_rows),
        "development_rows": len(development_rows),
        "development_multi_intent_rows": development_multi_count,
        "train_primary_counts": dict(sorted(train_primary.items())),
        "train_secondary_counts": dict(sorted(train_secondary.items())),
        "development_primary_counts": dict(sorted(development_primary.items())),
        "development_secondary_counts": dict(sorted(development_secondary.items())),
        "unseen_development_primary_labels": unseen_primary,
        "unseen_development_secondary_labels": unseen_secondary,
        "invalid_train_primary_labels": invalid_train_primary,
        "invalid_train_secondary_labels": invalid_train_secondary,
        "secondary_label_coverage": {
            "seen_labels": len(set(development_secondary) & set(train_secondary)),
            "required_labels": len(development_secondary),
            "label_rate": round(
                len(set(development_secondary) & set(train_secondary)) / len(development_secondary),
                4,
            )
            if development_secondary
            else 1.0,
            "support_rate": round(learnable_secondary_support / total_secondary_support, 4)
            if total_secondary_support
            else 1.0,
        },
        "training_ready": not (
            unseen_primary or unseen_secondary or invalid_train_primary or invalid_train_secondary
        ),
        "claims": {
            "development_used_for_training": False,
            "fixed_test_used": False,
            "sufficient_for_quality_claim": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    train_rows = [
        json.loads(line) for line in args.train.read_text(encoding="utf-8").splitlines() if line
    ]
    train_rows = select_eligible_train_rows(train_rows)
    if not train_rows:
        raise ValueError("no eligible train rows were provided")
    development_rows = json.loads(args.development.read_text(encoding="utf-8"))
    report = audit_label_coverage(train_rows, development_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
