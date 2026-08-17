"""Versioned contract shared by intent inference, routing, context, and traces."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fast_api.app.services.intent_decision import IntentDecision

SCHEMA_VERSION = "intent_decision_v2"


@dataclass
class IntentDecisionV2:
    """Serializable single source of truth for one Agent turn."""

    primary_intent: str
    secondary_intents: list[str] = field(default_factory=list)
    risk_level: str = "low"
    risk_evidence: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    requested_actions: list[str] = field(default_factory=list)
    allowed_actions: dict[str, bool] = field(default_factory=dict)
    blocked_actions: list[str] = field(default_factory=list)
    candidate_tools: list[str] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)
    task_plan: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    latency_ms: dict[str, int] = field(default_factory=dict)
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_legacy(
        cls,
        decision: IntentDecision,
        *,
        provenance: dict[str, Any] | None = None,
        latency_ms: dict[str, int] | None = None,
        candidate_tools: list[str] | None = None,
        risk_evidence: list[str] | None = None,
    ) -> "IntentDecisionV2":
        requested_actions = [
            str(step.get("action"))
            for step in decision.task_plan
            if isinstance(step, dict) and step.get("action")
        ]
        blocked_actions = [
            str(step.get("action"))
            for step in decision.task_plan
            if isinstance(step, dict) and step.get("status") == "blocked" and step.get("action")
        ]
        return cls(
            primary_intent=decision.primary_intent,
            secondary_intents=list(decision.secondary_intents),
            risk_level=decision.risk_level,
            risk_evidence=list(risk_evidence or []),
            entities=dict(decision.entities),
            missing_slots=list(decision.missing_slots),
            needs_clarification=decision.needs_clarification,
            requested_actions=requested_actions,
            allowed_actions=dict(decision.allowed_actions),
            blocked_actions=blocked_actions,
            candidate_tools=list(candidate_tools or []),
            confidence={"overall": float(decision.confidence)},
            task_plan=list(decision.task_plan),
            reason=decision.reason,
            provenance=dict(provenance or {}),
            latency_ms=dict(latency_ms or {}),
        )

    def to_legacy(self) -> IntentDecision:
        return IntentDecision(
            primary_intent=self.primary_intent,
            secondary_intents=list(self.secondary_intents),
            confidence=float(self.confidence.get("overall", 0.7)),
            risk_level=self.risk_level,
            entities=dict(self.entities),
            missing_slots=list(self.missing_slots),
            needs_clarification=self.needs_clarification,
            allowed_actions=dict(self.allowed_actions),
            task_plan=list(self.task_plan),
            reason=self.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "primary_intent": self.primary_intent,
            "secondary_intents": self.secondary_intents,
            "risk": {
                "level": self.risk_level,
                "evidence": self.risk_evidence,
                "rule_detected": bool(self.provenance.get("rule_risk_detected")),
            },
            "entities": self.entities,
            "missing_slots": self.missing_slots,
            "needs_clarification": self.needs_clarification,
            "requested_actions": self.requested_actions,
            "allowed_actions": self.allowed_actions,
            "blocked_actions": self.blocked_actions,
            "candidate_tools": self.candidate_tools,
            "confidence": self.confidence,
            "task_plan": self.task_plan,
            "reason": self.reason,
            "provenance": self.provenance,
            "latency_ms": self.latency_ms,
        }
