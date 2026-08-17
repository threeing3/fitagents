"""User-disjoint deterministic dataset splitting.

The scenario argument is kept for API compatibility and for reporting, but it
must not influence the split: two rows from the same user must stay in one
partition even when their task types differ.  Otherwise multi-turn Agent
traces leak user-specific preferences from training into evaluation.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any


def _bucket(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def assign_split(user_hash: str, scenario: str, train=0.8, validation=0.1) -> str:
    del scenario  # Scenario remains metadata; the grouping key is the user.
    value = _bucket(str(user_hash or "unknown"))
    if value < train:
        return "train"
    if value < train + validation:
        return "validation"
    return "test"


def split_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups = {str(record.get("user_hash") or "unknown") for record in records}
    assignments: dict[str, str]
    if len(groups) >= 10:
        # Allocate whole users by deterministic hash rank. This is the closest
        # feasible 80/10/10 allocation without leaking one user across splits.
        ordered = [group for _, group in sorted((_bucket(group), group) for group in groups)]
        train_count = round(len(ordered) * 0.8)
        validation_count = round(len(ordered) * 0.1)
        train_count = min(max(train_count, 1), len(ordered) - 2)
        validation_count = min(max(validation_count, 1), len(ordered) - train_count - 1)
        assignments = {}
        for index, group in enumerate(ordered):
            if index < train_count:
                assignments[group] = "train"
            elif index < train_count + validation_count:
                assignments[group] = "validation"
            else:
                assignments[group] = "test"
    else:
        assignments = {group: assign_split(group, "") for group in groups}
    if 3 <= len(groups) < 10:
        # A small real dataset can randomly miss the 10% validation bucket.
        # Move the nearest deterministic train group only when a partition is
        # empty; this keeps user disjointness and makes the experiment usable.
        buckets = sorted(((_bucket(group), group) for group in groups))
        for required in ("validation", "test", "train"):
            if required in assignments.values():
                continue
            if required == "train":
                candidates = [group for _, group in buckets if assignments[group] != "train"]
            else:
                candidates = [group for _, group in buckets if assignments[group] == "train"]
            if not candidates:
                continue
            chosen = candidates[-1] if required == "test" else candidates[0]
            assignments[chosen] = required
    output: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["split"] = assignments.get(str(item.get("user_hash") or "unknown"), "quarantine")
        output.append(item)
    return output, dict(Counter(item["split"] for item in output))
