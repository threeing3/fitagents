"""Interpretable acceptance baseline with optional scikit-learn upgrade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MajorityAcceptanceModel:
    positive_rate: float = 0.5
    feature_names: list[str] = field(default_factory=list)

    def fit(self, rows: list[dict[str, Any]], labels: list[int | None]) -> "MajorityAcceptanceModel":
        observed = [int(label) for label in labels if label is not None]
        self.positive_rate = sum(observed) / len(observed) if observed else 0.5
        self.feature_names = sorted({key for row in rows for key in row})
        return self

    def predict_proba(self, rows: list[dict[str, Any]]) -> list[float]:
        return [self.positive_rate for _ in rows]

    def predict(self, rows: list[dict[str, Any]], threshold: float = 0.5) -> list[int]:
        return [int(value >= threshold) for value in self.predict_proba(rows)]


def fit_acceptance_model(rows: list[dict[str, Any]], labels: list[int | None]):
    """Use sklearn when installed, while retaining a dependency-free baseline."""

    observed = [(row, label) for row, label in zip(rows, labels) if label is not None]
    if len(observed) < 10:
        return MajorityAcceptanceModel().fit(rows, labels)
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return MajorityAcceptanceModel().fit(rows, labels)
    names = sorted({key for row, _ in observed for key in row})
    matrix = [[float(row.get(name, 0.0)) for name in names] for row, _ in observed]
    target = [int(label) for _, label in observed]
    model = LogisticRegression(max_iter=500, random_state=42)
    model.fit(matrix, target)
    model.feature_names_ = names
    return model
