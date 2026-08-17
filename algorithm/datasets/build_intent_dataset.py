"""Build the isolated IntentDecisionV2 dataset and its reproducibility manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.data.deduplicate import normalize_text
from algorithm.data.intent_dataset_factory import build_intent_examples
from algorithm.data.validate_dataset import validate_training_rows


def _load_eval_messages(paths: list[Path]) -> set[str]:
    messages: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload:
            if isinstance(row, dict) and row.get("user_message"):
                messages.add(normalize_text(str(row["user_message"])))
    return messages


def build_dataset(
    per_family: int, eval_paths: list[Path]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [example.to_dict() for example in build_intent_examples(per_family)]
    protected = _load_eval_messages(eval_paths)
    collisions = sorted(
        row["example_id"] for row in rows if normalize_text(row["user_message"]) in protected
    )
    report = validate_training_rows(rows)
    if collisions:
        raise ValueError(f"fixed-evaluation collision detected: {collisions[:5]}")
    if report["error_count"]:
        raise ValueError(f"dataset validation failed: {report['errors'][:5]}")
    manifest = {
        "dataset_name": "fitagent_intent_decision_v2",
        "dataset_version": "intent-v1-20260817",
        "row_count": len(rows),
        "split_counts": dict(Counter(str(row["split"]) for row in rows)),
        "weakness_counts": dict(Counter(str(row["quality_labels"]["weakness"]) for row in rows)),
        "source_counts": dict(Counter(str(row["source"]) for row in rows)),
        "label_source_counts": dict(Counter(str(row["label_source"]) for row in rows)),
        "training_eligible": sum(bool(row["training_eligible"]) for row in rows),
        "human_approved": sum(row["human_review_status"] == "approved" for row in rows),
        "template_family_count": len({str(row["template_family"]) for row in rows}),
        "template_family_split_leaks": report["template_family_split_leaks"],
        "user_split_leaks": report["user_split_leaks"],
        "exact_eval_collisions": len(collisions),
        "protected_eval_files": [
            {"path": path.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in eval_paths
        ],
        "claims": {
            "real_user_data": False,
            "expert_labeled": False,
            "teacher_generated": False,
            "suitable_for_pipeline_validation": True,
            "sufficient_for_model_quality_claim": False,
        },
    }
    return rows, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build isolated intent-decision data")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--eval", action="append", type=Path, required=True)
    parser.add_argument("--per-family", type=int, default=50)
    args = parser.parse_args()
    rows, manifest = build_dataset(args.per_family, args.eval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(rows)} rows; training eligible={manifest['training_eligible']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
