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
    for index, payload in enumerate(rows):
        try:
            example = TrainingExample.from_dict(payload)
            row_errors = example.validate()
            ids[example.example_id] += 1
            sources[example.source] += 1
            splits[example.split] += 1
            users.add(example.user_hash)
        except (TypeError, ValueError) as exc:
            row_errors = [str(exc)]
        if row_errors:
            errors.append({"row": index, "errors": row_errors})
    errors.extend(
        {"row": example_id, "errors": ["duplicate example_id"]}
        for example_id, count in ids.items()
        if count > 1
    )
    return {
        "rows": len(rows),
        "valid_rows": len(rows) - len(errors),
        "error_count": len(errors),
        "errors": errors[:100],
        "source_counts": dict(sources),
        "split_counts": dict(splits),
        "user_count": len(users),
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
