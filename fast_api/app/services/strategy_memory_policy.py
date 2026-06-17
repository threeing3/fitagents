from __future__ import annotations

from typing import Any


def build_strategy_memory_response_note(context_packet: dict[str, Any] | None) -> str:
    guidance = (context_packet or {}).get("strategy_memory_guidance") or {}
    successful = guidance.get("successful_strategies") or []
    failed = guidance.get("failed_strategies") or []
    if not successful and not failed:
        return ""

    lines = [
        "Strategy memory guidance:",
        "- Active risk notes and decision rules override prior strategy memories.",
        "- Do not reuse any strategy that conflicts with active risk notes or decision rules.",
    ]
    for item in successful[:2]:
        summary = str(item.get("summary") or item.get("content") or "prior successful strategy")[:220]
        lines.append(f"- Reuse prior successful strategy only if similar: {summary}")
    for item in failed[:2]:
        summary = str(item.get("summary") or item.get("content") or "prior failed strategy")[:220]
        lines.append(f"- Avoid repeating prior failed strategy unless current state changed: {summary}")
    return "\n\n" + "\n".join(lines)


def strategy_memory_expectations(context_packet: dict[str, Any] | None) -> dict[str, bool]:
    guidance = (context_packet or {}).get("strategy_memory_guidance") or {}
    has_successful = bool(guidance.get("successful_strategies") or [])
    has_failed = bool(guidance.get("failed_strategies") or [])
    has_rule_or_risk = bool(
        (context_packet or {}).get("active_risk_notes")
        or ((context_packet or {}).get("knowledge_context") or {}).get("decision_rules")
    )
    return {
        "has_successful": has_successful,
        "has_failed": has_failed,
        "has_rule_or_risk": has_rule_or_risk,
        "requires_override": has_successful and has_rule_or_risk,
        "requires_strategy_guidance": has_successful or has_failed,
    }


def response_satisfies_strategy_memory_policy(
    response: str,
    context_packet: dict[str, Any] | None,
) -> dict[str, bool]:
    lowered = (response or "").lower()
    expectations = strategy_memory_expectations(context_packet)
    return {
        "successful_reuse": (
            not expectations["has_successful"]
            or ("reuse prior successful strategy" in lowered and "similar" in lowered)
        ),
        "failed_avoidance": (
            not expectations["has_failed"]
            or (
                "avoid repeating prior failed strategy" in lowered
                or "avoid repeating" in lowered
                or "do not repeat" in lowered
            )
        ),
        "rule_override": (
            not expectations["requires_override"]
            or (
                "active risk notes and decision rules override" in lowered
                or "do not reuse any strategy that conflicts" in lowered
            )
        ),
    }
