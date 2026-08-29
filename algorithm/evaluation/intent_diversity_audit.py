"""Measure lexical diversity and cross-split similarity in intent datasets."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.data.deduplicate import normalize_text
from algorithm.data.validate_dataset import read_jsonl


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    compact = normalize_text(text).replace(" ", "")
    return {compact[index : index + n] for index in range(max(0, len(compact) - n + 1))}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def audit_diversity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    messages = [str(row["user_message"]) for row in rows]
    normalized = [normalize_text(message) for message in messages]
    split_rows = {
        split: [row for row in rows if (row.get("split") or row.get("split_target")) == split]
        for split in ("train", "validation")
    }
    train_grams = [_char_ngrams(str(row["user_message"])) for row in split_rows["train"]]
    validation_grams = [_char_ngrams(str(row["user_message"])) for row in split_rows["validation"]]
    cross_scores = [
        _jaccard(train_item, validation_item)
        for train_item in train_grams
        for validation_item in validation_grams
    ]
    all_trigrams = [gram for message in messages for gram in _char_ngrams(message)]
    family_counts = Counter(
        str(row.get("template_family") or row.get("request_id") or "unknown") for row in rows
    )
    return {
        "schema_version": "fitagent-intent-diversity-audit/v1",
        "rows": len(rows),
        "exact_duplicate_rate": round(1 - len(set(normalized)) / len(normalized), 4)
        if normalized
        else 0.0,
        "distinct_char_trigram_rate": round(len(set(all_trigrams)) / len(all_trigrams), 4)
        if all_trigrams
        else 0.0,
        "max_cross_split_trigram_jaccard": round(max(cross_scores), 4) if cross_scores else 0.0,
        "mean_cross_split_trigram_jaccard": round(sum(cross_scores) / len(cross_scores), 4)
        if cross_scores
        else 0.0,
        "largest_template_family_share": round(max(family_counts.values()) / len(rows), 4)
        if rows
        else 0.0,
        "split_counts": {key: len(value) for key, value in split_rows.items()},
        "claims": {"semantic_diversity_proven": False, "lexical_diagnostic_only": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_diversity([row for path in args.input for row in read_jsonl(path)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
