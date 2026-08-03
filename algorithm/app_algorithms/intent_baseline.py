"""Evaluate the existing deterministic intent router as an algorithm baseline."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from fast_api.app.services.intent_decision import IntentRouter


def macro_f1(y_true: list[str], y_pred: list[str]) -> float:
    labels = sorted(set(y_true) | set(y_pred))
    scores: list[float] = []
    for label in labels:
        tp = sum(a == label and b == label for a, b in zip(y_true, y_pred))
        fp = sum(a != label and b == label for a, b in zip(y_true, y_pred))
        fn = sum(a == label and b != label for a, b in zip(y_true, y_pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def recall_for_label(y_true: list[str], y_pred: list[str], label: str) -> float:
    positives = sum(actual == label for actual in y_true)
    true_positives = sum(actual == label and predicted == label for actual, predicted in zip(y_true, y_pred))
    return true_positives / positives if positives else 0.0


def evaluate_cases(cases: list[dict], router: IntentRouter | None = None) -> dict:
    router = router or IntentRouter()
    y_true = [str(case.get("expected_primary_intent") or case.get("expected_intent")) for case in cases]
    y_pred = [router.classify(str(case.get("input") or case.get("user_message") or "")) for case in cases]
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for actual, predicted in zip(y_true, y_pred):
        confusion[actual][predicted] += 1
    return {
        "cases": len(cases),
        "accuracy": sum(a == b for a, b in zip(y_true, y_pred)) / len(cases) if cases else 0.0,
        "macro_f1": macro_f1(y_true, y_pred),
        "risk_recall": recall_for_label(y_true, y_pred, "injury_or_risk"),
        "predictions": [
            {"input": case.get("input") or case.get("user_message"), "expected": actual, "predicted": predicted, "correct": actual == predicted}
            for case, actual, predicted in zip(cases, y_true, y_pred)
        ],
        "confusion": {key: dict(value) for key, value in confusion.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the intent baseline")
    parser.add_argument("cases", type=Path)
    args = parser.parse_args()
    report = evaluate_cases(json.loads(args.cases.read_text(encoding="utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
