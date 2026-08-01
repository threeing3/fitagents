"""User- and scenario-disjoint deterministic dataset splitting."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any


def _bucket(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def assign_split(user_hash: str, scenario: str, train=0.8, validation=0.1) -> str:
    value = _bucket(f"{user_hash}:{scenario}")
    if value < train:
        return "train"
    if value < train + validation:
        return "validation"
    return "test"


def split_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    output: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["split"] = assign_split(
            str(item.get("user_hash", "unknown")),
            str(item.get("task_type") or item.get("intent_label") or "unknown"),
        )
        output.append(item)
    return output, dict(Counter(item["split"] for item in output))
