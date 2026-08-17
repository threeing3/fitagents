"""Build a governed dataset bundle for learning and offline experiments."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.data.deduplicate import deduplicate_records
from algorithm.data.sample_factory import build_synthetic_examples, build_synthetic_preference_pairs
from algorithm.data.sanitize import sanitize_value
from algorithm.data.schemas import DatasetManifest
from algorithm.data.split_dataset import split_records
from algorithm.data.validate_dataset import read_jsonl, validate_training_rows
from algorithm.datasets.build_preference_dataset import build_preference_pairs
from algorithm.datasets.build_safety_dataset import build_safety_rows
from algorithm.datasets.build_sft_dataset import build_sft_rows
from algorithm.datasets.build_tool_decision_dataset import build_tool_decision_rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(sanitize_value(row), ensure_ascii=False) + "\n")


def build_bundle(
    input_path: Path | None,
    output_dir: Path,
    synthetic_count: int = 0,
    seed: int = 42,
    include_synthetic_preferences: bool = False,
) -> dict[str, Any]:
    """Build all task datasets and return a machine-readable bundle report."""

    canonical_rows = read_jsonl(input_path) if input_path and input_path.exists() else []
    synthetic_rows = [row.to_dict() for row in build_synthetic_examples(synthetic_count, seed)]
    rows, duplicate_count = deduplicate_records(
        [*canonical_rows, *synthetic_rows],
        ("user_hash", "user_message", "assistant_response"),
    )
    rows, split_counts = split_records(rows)
    validation = validate_training_rows(rows)
    output_dir.mkdir(parents=True, exist_ok=True)

    sft_rows = build_sft_rows(rows)
    tool_rows = build_tool_decision_rows(rows)
    preference_rows = build_preference_pairs(rows)
    if include_synthetic_preferences:
        preference_rows.extend(build_synthetic_preference_pairs(rows))
    safety_rows = build_safety_rows(rows)
    _write_jsonl(output_dir / "training_examples.jsonl", rows)
    _write_jsonl(output_dir / "sft_train.jsonl", sft_rows)
    _write_jsonl(output_dir / "tool_decisions.jsonl", tool_rows)
    _write_jsonl(output_dir / "safety_train.jsonl", safety_rows)
    _write_jsonl(output_dir / "preference_pairs.jsonl", preference_rows)
    (output_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    source_counts = Counter(str(row.get("source") or "unknown") for row in rows)
    source_split_counts: dict[str, dict[str, int]] = {}
    scenario_split_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        scenario = str(row.get("task_type") or "unknown")
        split = str(row.get("split") or "quarantine")
        source_split_counts.setdefault(source, {})[split] = (
            source_split_counts.setdefault(source, {}).get(split, 0) + 1
        )
        scenario_split_counts.setdefault(scenario, {})[split] = (
            scenario_split_counts.setdefault(scenario, {}).get(split, 0) + 1
        )
    manifest = DatasetManifest(
        dataset_name="fitness_training_bundle",
        dataset_version=f"v1-seed-{seed}",
        source_files=[str(input_path)] if input_path else [],
        row_counts={
            "training_examples": len(rows),
            "sft": len(sft_rows),
            "tool_decisions": len(tool_rows),
            "safety": len(safety_rows),
            "preference_pairs": len(preference_rows),
        },
        split_counts=split_counts,
        source_counts=dict(source_counts),
        source_split_counts=source_split_counts,
        scenario_split_counts=scenario_split_counts,
        eligibility_counts={
            "sft_train": len(sft_rows),
            "dpo_human_reviewed": sum(
                row.get("review_status") == "approved"
                and row.get("source") in {"agent_trace", "expert_labeled"}
                for row in preference_rows
            ),
            "seed_eval_training_rows": sum(row.get("source") == "seed_eval" for row in rows),
        },
        user_count=len({row.get("user_hash") for row in rows}),
        scenario_count=len({row.get("task_type") for row in rows}),
        validation_errors=int(validation["error_count"]),
        notes=[
            f"synthetic_count={len(synthetic_rows)}",
            f"deduplicated={duplicate_count}",
            "synthetic rows are not production business evidence",
            f"synthetic_preferences_included={include_synthetic_preferences}",
            "seed_eval fixtures are maintained outside the training bundle",
            "split_strategy=deterministic_whole_user_nearest_80_10_10",
        ],
    )
    manifest.write_json(str(output_dir / "bundle.manifest.json"))
    return {"manifest": manifest.to_dict(), "validation": validation}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build governed SFT/tool/preference datasets")
    parser.add_argument(
        "--input",
        type=Path,
        help="canonical TrainingExample JSONL; may be omitted for synthetic-only learning",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--synthetic-count", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--include-synthetic-preferences", action="store_true")
    args = parser.parse_args()
    report = build_bundle(
        args.input,
        args.output_dir,
        args.synthetic_count,
        args.seed,
        args.include_synthetic_preferences,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["validation"]["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
