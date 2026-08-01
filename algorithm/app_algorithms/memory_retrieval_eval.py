"""Small, dependency-free retrieval metrics for memory/eval fixtures."""

from __future__ import annotations

from typing import Iterable


def recall_at_k(retrieved: Iterable[str], expected: Iterable[str], k: int = 5) -> float:
    expected_set = {str(item).lower() for item in expected if item}
    if not expected_set:
        return 1.0
    returned = {str(item).lower() for item in list(retrieved)[:k]}
    return len(returned & expected_set) / len(expected_set)


def mean_recall_at_k(records: list[dict], k: int = 5) -> float:
    scores = [
        recall_at_k(record.get("retrieved") or record.get("relevant_memories") or [], record.get("expected") or record.get("expected_recalled_terms") or [], k)
        for record in records
    ]
    return sum(scores) / len(scores) if scores else 0.0
