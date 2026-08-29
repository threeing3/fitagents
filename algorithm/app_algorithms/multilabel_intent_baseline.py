"""Discriminative TF-IDF baselines for primary and secondary intent labels."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from algorithm.evaluation.multilabel_data_audit import (
    audit_label_coverage,
    select_eligible_calibration_rows,
    select_eligible_train_rows,
)


def _require_sklearn() -> tuple[Any, Any]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:  # pragma: no cover - exercised only in a missing optional env
        raise RuntimeError(
            "scikit-learn is required; install algorithm/training/requirements-training.txt"
        ) from exc
    return TfidfVectorizer, LogisticRegression


def _decision(row: dict[str, Any]) -> dict[str, Any]:
    value = row["assistant_response"]
    return json.loads(value) if isinstance(value, str) else value


def _text(row: dict[str, Any]) -> str:
    message = str(row.get("user_message") or "")
    context = row.get("retrieved_context") or {}
    memory = str(context.get("memory_summary") or "") if isinstance(context, dict) else ""
    return f"{message} [MEMORY] {memory}" if memory else message


def _safe_div(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def multilabel_metrics(
    expected: list[set[str]], predicted: list[set[str]], labels: list[str]
) -> dict[str, Any]:
    per_label: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    tp_total = fp_total = fn_total = 0
    for label in labels:
        tp = sum(label in truth and label in guess for truth, guess in zip(expected, predicted))
        fp = sum(label not in truth and label in guess for truth, guess in zip(expected, predicted))
        fn = sum(label in truth and label not in guess for truth, guess in zip(expected, predicted))
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * tp, 2 * tp + fp + fn)
        per_label[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }
        f1_values.append(f1)
        tp_total += tp
        fp_total += fp
        fn_total += fn
    exact_match = _safe_div(
        sum(truth == guess for truth, guess in zip(expected, predicted)), len(expected)
    )
    return {
        "exact_match": round(exact_match, 4),
        "macro_f1": round(sum(f1_values) / len(f1_values), 4) if f1_values else 0.0,
        "micro_f1": round(_safe_div(2 * tp_total, 2 * tp_total + fp_total + fn_total), 4),
        "per_label": per_label,
    }


@dataclass
class _BinaryHead:
    constant: int | None
    model: Any | None


class TfidfIntentBaseline:
    """One primary softmax model plus independent secondary binary heads."""

    def __init__(self, *, threshold: float = 0.5, seed: int = 42) -> None:
        if not 0 < threshold < 1:
            raise ValueError("threshold must be between zero and one")
        self.threshold = threshold
        self.seed = seed
        self.vectorizer: Any | None = None
        self.primary_model: Any | None = None
        self.secondary_heads: dict[str, _BinaryHead] = {}

    def fit(self, rows: list[dict[str, Any]], secondary_labels: list[str]) -> None:
        TfidfVectorizer, LogisticRegression = _require_sklearn()
        texts = [_text(row) for row in rows]
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2)
        features = self.vectorizer.fit_transform(texts)
        primary = [str(_decision(row)["primary_intent"]) for row in rows]
        self.primary_model = LogisticRegression(
            max_iter=1000, class_weight="balanced", random_state=self.seed
        ).fit(features, primary)
        self.secondary_heads = {}
        decisions = [_decision(row) for row in rows]
        for label in secondary_labels:
            target = [int(label in decision.get("secondary_intents", [])) for decision in decisions]
            if len(set(target)) == 1:
                self.secondary_heads[label] = _BinaryHead(constant=target[0], model=None)
            else:
                model = LogisticRegression(
                    max_iter=1000, class_weight="balanced", random_state=self.seed
                ).fit(features, target)
                self.secondary_heads[label] = _BinaryHead(constant=None, model=model)

    def predict(
        self, rows: list[dict[str, Any]], *, thresholds: dict[str, float] | None = None
    ) -> list[dict[str, Any]]:
        if self.vectorizer is None or self.primary_model is None:
            raise RuntimeError("baseline must be fitted before prediction")
        features = self.vectorizer.transform([_text(row) for row in rows])
        primary = self.primary_model.predict(features)
        secondary_by_row: list[list[str]] = [[] for _ in rows]
        probabilities: list[dict[str, float]] = [dict() for _ in rows]
        for label, head in self.secondary_heads.items():
            if head.constant is not None:
                scores = [float(head.constant)] * len(rows)
            else:
                scores = [float(value) for value in head.model.predict_proba(features)[:, 1]]
            for index, score in enumerate(scores):
                probabilities[index][label] = score
                if score >= (thresholds or {}).get(label, self.threshold):
                    secondary_by_row[index].append(label)
        return [
            {
                "primary_intent": str(primary[index]),
                "secondary_intents": secondary_by_row[index],
                "secondary_probabilities": probabilities[index],
            }
            for index in range(len(rows))
        ]


def calibrate_secondary_thresholds(
    model: TfidfIntentBaseline,
    calibration_rows: list[dict[str, Any]],
    labels: list[str],
    *,
    candidates: tuple[float, ...] | None = None,
) -> dict[str, float]:
    """Choose each label threshold on calibration data only, never development data."""

    if not calibration_rows:
        raise ValueError("calibration rows are required")
    predictions = model.predict(calibration_rows)
    expected = [set(_decision(row).get("secondary_intents", [])) for row in calibration_rows]
    calibrated: dict[str, float] = {}
    for label in labels:
        label_candidates = candidates or tuple(
            sorted(
                {
                    float(prediction["secondary_probabilities"][label])
                    for prediction in predictions
                    if 0 < float(prediction["secondary_probabilities"][label]) < 1
                }
            )
        )
        if not label_candidates:
            calibrated[label] = 0.5
            continue
        best = (float("-inf"), float("-inf"), 0.5)
        for threshold in label_candidates:
            guessed = [
                {label} if prediction["secondary_probabilities"][label] >= threshold else set()
                for prediction in predictions
            ]
            f1 = float(multilabel_metrics(expected, guessed, [label])["macro_f1"])
            candidate = (f1, -abs(threshold - 0.5), threshold)
            if candidate > best:
                best = candidate
        calibrated[label] = best[2]
    return calibrated


def evaluate_baseline(
    train_rows: list[dict[str, Any]],
    development_rows: list[dict[str, Any]],
    threshold: float,
    calibration_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    train_rows = select_eligible_train_rows(train_rows)
    if not train_rows:
        raise ValueError("no eligible train rows were provided")
    audit = audit_label_coverage(
        train_rows, development_rows, minimum_primary_support=20, minimum_secondary_support=20
    )
    labels = sorted(audit["development_secondary_counts"])
    model = TfidfIntentBaseline(threshold=threshold)
    started = time.perf_counter()
    model.fit(train_rows, labels)
    selected_calibration = (
        select_eligible_calibration_rows(calibration_rows) if calibration_rows else []
    )
    calibrated_thresholds = (
        calibrate_secondary_thresholds(model, selected_calibration, labels)
        if selected_calibration
        else {}
    )
    predictions = model.predict(development_rows, thresholds=calibrated_thresholds)
    calibration_metrics = None
    if selected_calibration:
        calibration_predictions = model.predict(
            selected_calibration, thresholds=calibrated_thresholds
        )
        calibration_expected = [
            set(_decision(row).get("secondary_intents", [])) for row in selected_calibration
        ]
        calibration_guessed = [
            set(prediction["secondary_intents"]) for prediction in calibration_predictions
        ]
        calibration_metrics = multilabel_metrics(calibration_expected, calibration_guessed, labels)
    elapsed_ms = (time.perf_counter() - started) * 1000
    expected_secondary = [set(row["required_secondary_intents"]) for row in development_rows]
    predicted_secondary = [set(row["secondary_intents"]) for row in predictions]
    expected_primary = [str(row["expected_primary_intent"]) for row in development_rows]
    primary_accuracy = _safe_div(
        sum(
            expected == prediction["primary_intent"]
            for expected, prediction in zip(expected_primary, predictions)
        ),
        len(development_rows),
    )
    seen_labels = sorted(set(labels) - set(audit["unseen_development_secondary_labels"]))
    return {
        "schema_version": "fitagent-multilabel-tfidf-baseline/v2",
        "dataset": {
            "train_rows": len(train_rows),
            "development_rows": len(development_rows),
            "calibration_rows": len(selected_calibration),
            "development_used_for_training": False,
            "fixed_test_used": False,
        },
        "configuration": {
            "features": "character_tfidf_2_5gram",
            "classifier": "independent_logistic_regression",
            "threshold": threshold,
            "threshold_source": "isolated_calibration" if calibrated_thresholds else "fixed",
            "per_label_thresholds": calibrated_thresholds,
            "seed": 42,
        },
        "primary_accuracy": round(primary_accuracy, 4),
        "secondary_all_labels": multilabel_metrics(expected_secondary, predicted_secondary, labels),
        "secondary_seen_labels": multilabel_metrics(
            expected_secondary, predicted_secondary, seen_labels
        ),
        "calibration_secondary": calibration_metrics,
        "fit_and_predict_ms": round(elapsed_ms, 3),
        "audit": audit,
        "predictions": [
            {
                "case_id": row["case_id"],
                "expected_primary_intent": row["expected_primary_intent"],
                "predicted_primary_intent": prediction["primary_intent"],
                "expected_secondary_intents": row["required_secondary_intents"],
                "predicted_secondary_intents": prediction["secondary_intents"],
                "secondary_probabilities": prediction["secondary_probabilities"],
            }
            for row, prediction in zip(development_rows, predictions)
        ],
        "claims": {
            "production_uplift": False,
            "model_quality": False,
            "diagnostic_only": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()
    train_rows = [
        json.loads(line) for line in args.train.read_text(encoding="utf-8").splitlines() if line
    ]
    development_rows = json.loads(args.development.read_text(encoding="utf-8"))
    calibration_rows = (
        [
            json.loads(line)
            for line in args.calibration.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if args.calibration
        else None
    )
    report = evaluate_baseline(train_rows, development_rows, args.threshold, calibration_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "predictions"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
