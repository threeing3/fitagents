"""Metrics for structured tool plans and host-executable traces."""

from __future__ import annotations

from typing import Any


def schema_valid(plan: Any) -> bool:
    return (
        isinstance(plan, dict)
        and isinstance(plan.get("selected_tools"), list)
        and all(isinstance(item, str) and item for item in plan["selected_tools"])
        and isinstance(plan.get("tool_sequence"), list)
        and all(isinstance(item, str) and item for item in plan["tool_sequence"])
    )


def tool_exact_match(predicted: list[str], expected: list[str]) -> bool:
    return predicted == expected


def tool_sequence_accuracy(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    correct = sum(
        tool_exact_match(record.get("predicted_sequence") or [], record.get("expected_sequence") or [])
        for record in records
    )
    return correct / len(records)


def unnecessary_tool_rate(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    unnecessary = sum(int(record.get("unnecessary_tools", 0)) for record in records)
    total = sum(max(1, int(record.get("tool_count", 0))) for record in records)
    return unnecessary / total
