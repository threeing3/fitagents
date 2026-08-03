"""Business outcome metrics with safe handling of missing labels."""

from __future__ import annotations

import math
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


def roc_auc(y_true: Iterable[int], scores: Iterable[float]) -> float:
    """Compute AUROC without an optional metrics dependency."""

    pairs = [(int(label), float(score)) for label, score in zip(y_true, scores)]
    positives = [score for label, score in pairs if label == 1]
    negatives = [score for label, score in pairs if label == 0]
    if not positives or not negatives:
        return 0.5
    wins = sum(1.0 if positive > negative else 0.5 if positive == negative else 0.0 for positive in positives for negative in negatives)
    return wins / (len(positives) * len(negatives))


def probabilistic_metrics(y_true: Iterable[int], probabilities: Iterable[float], threshold: float = 0.5) -> dict[str, float]:
    true = list(y_true)
    scores = [max(0.0, min(1.0, float(value))) for value in probabilities]
    if len(true) != len(scores):
        raise ValueError("y_true and probabilities must have equal length")
    predicted = [int(score >= threshold) for score in scores]
    classification = binary_metrics(true, predicted)
    brier = sum((score - label) ** 2 for label, score in zip(true, scores)) / len(true) if true else 0.0
    calibration_mae = abs((sum(scores) / len(scores) if scores else 0.0) - (sum(true) / len(true) if true else 0.0))
    return {
        **classification,
        "auroc": roc_auc(true, scores),
        "brier_score": brier,
        "calibration_mae": calibration_mae,
    }


def ndcg_at_k(relevances: Iterable[float], k: int = 5) -> float:
    raw_values = list(relevances)
    values = [max(0.0, float(value)) for value in raw_values[:k]]
    if not values:
        return 0.0

    def dcg(items: list[float]) -> float:
        return sum((2.0**value - 1.0) / math.log2(index + 2) for index, value in enumerate(items))

    ideal = sorted([max(0.0, float(value)) for value in raw_values], reverse=True)[:k]
    ideal_score = dcg(ideal)
    return dcg(values) / ideal_score if ideal_score else 0.0


def mean_ndcg(records: Iterable[dict], k: int = 5) -> float:
    scores = [ndcg_at_k(record.get("relevances") or record.get("labels") or [], k) for record in records]
    return sum(scores) / len(scores) if scores else 0.0
