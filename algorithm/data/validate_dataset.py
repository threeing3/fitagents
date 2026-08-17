"""CLI and library validation for algorithm datasets."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .schemas import TrainingExample


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def validate_training_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    ids: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    users: set[str] = set()
    user_splits: dict[str, set[str]] = {}
    family_splits: dict[str, set[str]] = {}
    for index, payload in enumerate(rows):
        try:
            example = TrainingExample.from_dict(payload)
            row_errors = example.validate()
            ids[example.example_id] += 1
            sources[example.source] += 1
            splits[example.split] += 1
            users.add(example.user_hash)
            user_splits.setdefault(example.user_hash, set()).add(example.split)
            if example.template_family:
                family_splits.setdefault(example.template_family, set()).add(example.split)
        except (TypeError, ValueError) as exc:
            row_errors = [str(exc)]
        if row_errors:
            errors.append({"row": index, "errors": row_errors})
    errors.extend(
        {"row": example_id, "errors": ["duplicate example_id"]}
        for example_id, count in ids.items()
        if count > 1
    )
    family_split_leaks = {
        family: sorted(values) for family, values in family_splits.items() if len(values) > 1
    }
    errors.extend(
        {"row": family, "errors": [f"template_family crosses splits: {', '.join(values)}"]}
        for family, values in family_split_leaks.items()
    )
    split_leaks = {
        user_hash: sorted(values)
        for user_hash, values in user_splits.items()
        if len(values) > 1 and user_hash not in {"", "unknown"}
    }
    errors.extend(
        {"row": user_hash, "errors": [f"user_hash crosses splits: {', '.join(values)}"]}
        for user_hash, values in split_leaks.items()
    )
    return {
        "rows": len(rows),
        "valid_rows": len(rows) - len(errors),
        "error_count": len(errors),
        "errors": errors[:100],
        "source_counts": dict(sources),
        "split_counts": dict(splits),
        "user_count": len(users),
        "user_split_leaks": split_leaks,
        "template_family_split_leaks": family_split_leaks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a TrainingExample JSONL file")
    parser.add_argument("input", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate_training_rows(read_jsonl(args.input))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
