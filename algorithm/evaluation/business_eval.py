"""Business outcome metrics with safe handling of missing labels."""

from __future__ import annotations

from typing import Iterable


def binary_metrics(y_true: Iterable[int], y_pred: Iterable[int]) -> dict[str, float]:
    true = list(y_true)
    pred = list(y_pred)
    if len(true) != len(pred):
        raise ValueError("y_true and y_pred must have equal length")
    tp = sum(a == 1 and b == 1 for a, b in zip(true, pred))
    tn = sum(a == 0 and b == 0 for a, b in zip(true, pred))
    fp = sum(a == 0 and b == 1 for a, b in zip(true, pred))
    fn = sum(a == 1 and b == 0 for a, b in zip(true, pred))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "accuracy": (tp + tn) / len(true) if true else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "support": float(len(true)),
    }
