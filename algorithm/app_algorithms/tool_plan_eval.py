"""Metrics for structured tool plans and host-executable traces."""

from __future__ import annotations

from typing import Any


def schema_valid(plan: Any) -> bool:
    if not isinstance(plan, dict):
        return False
    selected = plan.get("selected_tools")
    sequence = plan.get("tool_sequence")
    if not isinstance(selected, list) or not all(isinstance(item, str) and item for item in selected):
        return False
    if not isinstance(sequence, list) or not all(isinstance(item, str) and item for item in sequence):
        return False
    if len(set(selected)) != len(selected) or any(item not in selected for item in sequence):
        return False
    if "plan_valid" in plan and not isinstance(plan["plan_valid"], bool):
        return False
    if "risk_level" in plan and plan["risk_level"] not in {"low", "medium", "high", "critical", "unknown"}:
        return False
    return True


def tool_exact_match(predicted: list[str], expected: list[str]) -> bool:
    return predicted == expected


def tool_selection_exact_match(predicted: list[str], expected: list[str]) -> bool:
    return sorted(set(predicted)) == sorted(set(expected))


def schema_valid_rate(plans: list[Any]) -> float:
    return sum(schema_valid(plan) for plan in plans) / len(plans) if plans else 0.0


def tool_sequence_accuracy(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    correct = sum(
        tool_exact_match(record.get("predicted_sequence") or [], record.get("expected_sequence") or [])
        for record in records
    )
    return correct / len(records)


def tool_selection_accuracy(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    correct = sum(
        tool_selection_exact_match(record.get("predicted_tools") or [], record.get("expected_tools") or [])
        for record in records
    )
    return correct / len(records)


def unnecessary_tool_rate(records: list[dict[str, Any]]) -> float:
    if not records:
        return 0.0
    unnecessary = sum(int(record.get("unnecessary_tools", 0)) for record in records)
    total = sum(max(1, int(record.get("tool_count", 0))) for record in records)
    return unnecessary / total
