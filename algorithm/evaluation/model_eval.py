"""Dependency-free metrics for algorithm experiments."""

from __future__ import annotations

from typing import Any

from algorithm.app_algorithms.intent_baseline import macro_f1
from algorithm.app_algorithms.tool_plan_eval import schema_valid, schema_valid_rate, tool_selection_accuracy, tool_sequence_accuracy


def classification_report(y_true: list[str], y_pred: list[str]) -> dict[str, float]:
    return {
        "accuracy": sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true) if y_true else 0.0,
        "macro_f1": macro_f1(y_true, y_pred),
    }


def schema_valid_rate(plans: list[Any]) -> float:
    return sum(schema_valid(plan) for plan in plans) / len(plans) if plans else 0.0


def build_model_report(
    y_true: list[str],
    y_pred: list[str],
    plans: list[Any] | None = None,
    tool_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {"classification": classification_report(y_true, y_pred)}
    if plans is not None:
        report["schema_valid_rate"] = schema_valid_rate(plans)
    if tool_records is not None:
        report["tool_selection_accuracy"] = tool_selection_accuracy(tool_records)
        report["tool_sequence_accuracy"] = tool_sequence_accuracy(tool_records)
    return report
