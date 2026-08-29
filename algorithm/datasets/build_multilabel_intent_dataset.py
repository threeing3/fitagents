"""Build the versioned multi-intent train and calibration dataset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.data.multilabel_intent_dataset_factory import build_multilabel_intent_examples
from algorithm.data.validate_dataset import validate_training_rows


def build_dataset(
    train_per_family: int = 12, calibration_per_family: int = 5
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [
        row.to_dict()
        for row in build_multilabel_intent_examples(
            train_per_family=train_per_family,
            calibration_per_family=calibration_per_family,
        )
    ]
    validation = validate_training_rows(rows)
    if validation["error_count"]:
        raise ValueError(f"dataset validation failed: {validation['errors'][:5]}")
    primary: Counter[str] = Counter()
    secondary: Counter[str] = Counter()
    for row in rows:
        decision = json.loads(row["assistant_response"])
        primary[decision["primary_intent"]] += 1
        secondary.update(decision["secondary_intents"])
    manifest = {
        "schema_version": "fitagent-intent-multilabel-manifest/v2",
        "dataset_name": "fitagent_intent_multilabel",
        "dataset_version": "intent-multilabel-v2.1-20260830",
        "row_count": len(rows),
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "primary_counts": dict(sorted(primary.items())),
        "secondary_counts": dict(sorted(secondary.items())),
        "template_family_count": len({row["template_family"] for row in rows}),
        "template_family_split_leaks": validation["template_family_split_leaks"],
        "user_split_leaks": validation["user_split_leaks"],
        "source_counts": dict(Counter(row["source"] for row in rows)),
        "human_approved": 0,
        "claims": {
            "real_user_data": False,
            "expert_labeled": False,
            "development_used_for_generation": False,
            "fixed_test_used": False,
            "sufficient_for_pipeline_validation": True,
            "sufficient_for_model_quality_claim": False,
        },
    }
    return rows, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--train-per-family", type=int, default=12)
    parser.add_argument("--calibration-per-family", type=int, default=5)
    args = parser.parse_args()
    rows, manifest = build_dataset(args.train_per_family, args.calibration_per_family)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
