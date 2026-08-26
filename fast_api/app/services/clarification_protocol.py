"""Deterministic protocol for missing information and action blocking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fast_api.app.services.intent_decision import IntentDecision, IntentRouter


@dataclass
class ClarificationResult:
    needs_clarification: bool
    missing_slots: list[str] = field(default_factory=list)
    blocked_actions: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)


class ClarificationProtocolValidator:
    """Add required clarification without relaxing an existing safety block."""

    AMBIGUOUS_REFERENCES = ("那个", "刚才", "它", "第二个", "照旧", "上回那套", "之前那个")
    RED_FLAGS = ("胸闷", "胸口闷", "头晕", "呼吸困难", "麻木", "心悸")

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    def validate(
        self, message: str, decision: IntentDecision, profile: Any | None = None
    ) -> ClarificationResult:
        text = (message or "").lower()
        missing = list(decision.missing_slots)
        reasons: list[str] = []
        blocked: list[str] = []

        if any(term in text for term in self.AMBIGUOUS_REFERENCES):
            missing.append("referent")
            reasons.append("AMBIGUOUS_REFERENCE")

        intents = {decision.primary_intent, *decision.secondary_intents}
        red_flag = any(term in text for term in self.RED_FLAGS)
        if "injury_or_risk" in intents:
            blocked.append("generate_plan")
            if red_flag:
                reasons.append("RED_FLAG_IMMEDIATE_BLOCK")
            else:
                if "symptom_severity" not in decision.entities:
                    missing.append("symptom_severity")
                if "symptom_duration" not in decision.entities:
                    missing.append("symptom_duration")
                reasons.append("INJURY_DETAILS_REQUIRED")

        if decision.primary_intent == "progression_decision":
            if not any(key in decision.entities for key in ("weight_kg", "rpe", "reps")):
                missing.append("recent_performance")
                reasons.append("PROGRESSION_EVIDENCE_REQUIRED")

        if decision.primary_intent == "training_log":
            if not decision.entities.get("exercises"):
                missing.append("exercise")
            if not any(key in decision.entities for key in ("weight_kg", "reps", "sets")):
                missing.append("set_details")
            if "exercise" in missing or "set_details" in missing:
                reasons.append("TRAINING_LOG_DETAILS_REQUIRED")

        missing = self._dedupe(missing)
        blocked = self._dedupe(blocked)
        requires_question = bool(missing) and not red_flag
        needs_clarification = decision.needs_clarification or requires_question
        allowed = ["ask_clarifying_question"] if needs_clarification else ["provide_safe_guidance"]
        if red_flag:
            allowed = ["provide_safety_guidance"]
        return ClarificationResult(
            needs_clarification=needs_clarification,
            missing_slots=missing,
            blocked_actions=blocked,
            allowed_actions=allowed,
            reason_codes=self._dedupe(reasons),
        )

    def apply(
        self,
        decision: IntentDecision,
        result: ClarificationResult,
        intent_router: IntentRouter,
    ) -> IntentDecision:
        decision.missing_slots = intent_router._dedupe(
            list(decision.missing_slots) + result.missing_slots
        )
        decision.needs_clarification = decision.needs_clarification or result.needs_clarification
        decision.allowed_actions = intent_router._allowed_actions(
            decision.primary_intent,
            decision.secondary_intents,
            decision.risk_level,
            decision.needs_clarification,
        )
        for action in result.blocked_actions:
            decision.allowed_actions[action] = False
        decision.task_plan = intent_router._build_task_plan(
            decision.primary_intent,
            decision.secondary_intents,
            decision.allowed_actions,
            decision.missing_slots,
            decision.risk_level,
        )
        return decision
