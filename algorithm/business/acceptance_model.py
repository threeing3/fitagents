"""Interpretable acceptance baselines with an optional scikit-learn adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
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


@dataclass
class LogisticAcceptanceModel:
    """Small dependency-free logistic regression for learning experiments."""

    feature_names: list[str] = field(default_factory=list)
    means: dict[str, float] = field(default_factory=dict)
    scales: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    bias: float = 0.0
    learning_rate: float = 0.08
    epochs: int = 300
    l2: float = 0.001

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-35.0, min(35.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def _vector(self, row: dict[str, Any]) -> dict[str, float]:
        return {
            name: (float(row.get(name, 0.0) or 0.0) - self.means.get(name, 0.0)) / self.scales.get(name, 1.0)
            for name in self.feature_names
        }

    def fit(
        self,
        rows: list[dict[str, Any]],
        labels: list[int | None],
        learning_rate: float = 0.08,
        epochs: int = 300,
        l2: float = 0.001,
    ) -> "LogisticAcceptanceModel":
        observed = [(row, int(label)) for row, label in zip(rows, labels) if label is not None]
        if not observed:
            return self
        self.feature_names = sorted({key for row, _ in observed for key in row})
        self.means = {
            name: sum(float(row.get(name, 0.0) or 0.0) for row, _ in observed) / len(observed)
            for name in self.feature_names
        }
        self.scales = {}
        for name in self.feature_names:
            variance = sum(
                (float(row.get(name, 0.0) or 0.0) - self.means[name]) ** 2 for row, _ in observed
            ) / len(observed)
            self.scales[name] = math.sqrt(variance) or 1.0
        self.weights = {name: 0.0 for name in self.feature_names}
        self.bias = 0.0
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.l2 = l2
        for _ in range(epochs):
            gradients = {name: 0.0 for name in self.feature_names}
            bias_gradient = 0.0
            for row, target in observed:
                vector = self._vector(row)
                score = self.bias + sum(self.weights[name] * vector[name] for name in self.feature_names)
                error = self._sigmoid(score) - target
                bias_gradient += error
                for name in self.feature_names:
                    gradients[name] += error * vector[name]
            scale = 1.0 / len(observed)
            self.bias -= self.learning_rate * bias_gradient * scale
            for name in self.feature_names:
                gradient = gradients[name] * scale + self.l2 * self.weights[name]
                self.weights[name] -= self.learning_rate * gradient
        return self

    def predict_proba(self, rows: list[dict[str, Any]]) -> list[float]:
        return [
            self._sigmoid(self.bias + sum(self.weights[name] * self._vector(row)[name] for name in self.feature_names))
            for row in rows
        ]

    def predict(self, rows: list[dict[str, Any]], threshold: float = 0.5) -> list[int]:
        return [int(value >= threshold) for value in self.predict_proba(rows)]


@dataclass
class SklearnAcceptanceModel:
    """Dictionary-row adapter around sklearn's LogisticRegression."""

    estimator: Any
    feature_names: list[str]

    def _matrix(self, rows: list[dict[str, Any]]) -> list[list[float]]:
        return [[float(row.get(name, 0.0) or 0.0) for name in self.feature_names] for row in rows]

    def predict_proba(self, rows: list[dict[str, Any]]) -> list[float]:
        return [float(value[1]) for value in self.estimator.predict_proba(self._matrix(rows))]

    def predict(self, rows: list[dict[str, Any]], threshold: float = 0.5) -> list[int]:
        return [int(value >= threshold) for value in self.predict_proba(rows)]


def fit_acceptance_model(rows: list[dict[str, Any]], labels: list[int | None]):
    """Use sklearn when installed, while retaining a dependency-free baseline."""

    observed = [(row, label) for row, label in zip(rows, labels) if label is not None]
    if len(observed) < 10 or len({int(label) for _, label in observed}) < 2:
        return MajorityAcceptanceModel().fit(rows, labels)
    names = sorted({key for row, _ in observed for key in row})
    matrix = [[float(row.get(name, 0.0) or 0.0) for name in names] for row, _ in observed]
    target = [int(label) for _, label in observed]
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return LogisticAcceptanceModel().fit(rows, labels)
    model = LogisticRegression(max_iter=500, random_state=42)
    model.fit(matrix, target)
    return SklearnAcceptanceModel(model, names)
