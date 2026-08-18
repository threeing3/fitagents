"""Dependency-light scoring primitives shared by live and local intent evaluation."""

from __future__ import annotations

from typing import Any, Protocol


class IntentDecisionLike(Protocol):
    primary_intent: str
    secondary_intents: list[str]
    risk_level: str
    needs_clarification: bool


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def intent_checks(row: dict[str, Any], decision: IntentDecisionLike) -> dict[str, bool]:
    """Score the four stable intent-contract components without runtime services."""

    expected_primary = row.get("expected_primary_intent") or row.get("expected_intent")
    expected_secondary = set(
        row.get("expected_secondary_intents") or row.get("required_secondary_intents") or []
    )
    expected_risk = row.get("expected_risk_level") or row.get("minimum_risk_level") or "low"
    expected_clarification = row.get("expected_needs_clarification")
    if expected_clarification is None:
        expected_clarification = row.get("expected_clarification")
    return {
        "primary_intent": decision.primary_intent == expected_primary,
        "secondary_intents": expected_secondary.issubset(set(decision.secondary_intents)),
        "risk_level": RISK_ORDER.get(decision.risk_level, -1)
        >= RISK_ORDER.get(str(expected_risk), 0),
        "clarification": decision.needs_clarification is bool(expected_clarification),
    }
