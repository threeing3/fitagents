"""LLM-assisted intent refinement for complex fitness-coach turns."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from fast_api.app.services.intent_decision import IntentDecision, IntentRouter
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)


class LLMIntentClassifier:
    """Use a live model to refine rule intent when semantics are ambiguous."""

    RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        intent_router: IntentRouter | None = None,
    ):
        self.model_provider = model_provider or ModelProvider()
        self.intent_router = intent_router or IntentRouter()

    def should_refine(self, decision: IntentDecision, message: str = "") -> bool:
        if decision.confidence < 0.7:
            return True
        if len(decision.secondary_intents) >= 2:
            return True
        if decision.primary_intent == "general_chat":
            return True
        if decision.primary_intent == "injury_or_risk":
            return True
        if decision.allowed_actions.get("requested_plan_but_blocked"):
            return True
        if self._looks_semantically_complex(message):
            return True
        return False

    async def refine(
        self,
        message: str,
        rule_decision: IntentDecision,
        profile: Any | None = None,
    ) -> IntentDecision:
        if not self.should_refine(rule_decision, message):
            return rule_decision

        model = self.model_provider.chat_model(temperature=0.0)
        if model is None:
            return rule_decision

        try:
            response = await model.ainvoke(
                [
                    SystemMessage(content=self._system_prompt()),
                    HumanMessage(content=self._user_prompt(message, rule_decision, profile)),
                ]
            )
            payload = self._parse_json(str(response.content))
        except Exception as exc:
            logger.warning("LLM intent classifier failed, using rule intent: %s", exc)
            return rule_decision

        if not payload:
            return rule_decision
        return self._merge_with_rule_decision(payload, rule_decision)

    def _merge_with_rule_decision(
        self,
        payload: dict[str, Any],
        rule_decision: IntentDecision,
    ) -> IntentDecision:
        primary = str(payload.get("primary_intent") or "").strip()
        if primary not in AgentIntentCatalog.VALID_INTENTS:
            primary = rule_decision.primary_intent

        secondary = [
            str(intent).strip()
            for intent in payload.get("secondary_intents", [])
            if str(intent).strip() in AgentIntentCatalog.VALID_INTENTS
        ]
        secondary = self.intent_router._dedupe(secondary + rule_decision.secondary_intents)
        secondary = [intent for intent in secondary if intent != primary]

        # Never let the LLM demote a rule-detected safety turn.
        if rule_decision.primary_intent == "injury_or_risk" or "injury_or_risk" in rule_decision.secondary_intents:
            if primary != "injury_or_risk":
                secondary = self.intent_router._dedupe([primary] + secondary)
                primary = "injury_or_risk"
                secondary = [intent for intent in secondary if intent != primary]

        risk_level = self._max_risk(rule_decision.risk_level, str(payload.get("risk_level") or "low"))
        entities = {**rule_decision.entities}
        if isinstance(payload.get("entities"), dict):
            entities.update(payload["entities"])

        missing_slots = self.intent_router._dedupe(
            rule_decision.missing_slots
            + [str(slot) for slot in payload.get("missing_slots", []) if str(slot).strip()]
        )
        needs_clarification = bool(payload.get("needs_clarification")) or rule_decision.needs_clarification
        allowed_actions = self.intent_router._allowed_actions(primary, secondary, risk_level, needs_clarification)
        task_plan = self.intent_router._build_task_plan(
            primary,
            secondary,
            allowed_actions,
            missing_slots,
            risk_level,
        )
        confidence = self._clamp_confidence(payload.get("confidence"), rule_decision.confidence)

        return IntentDecision(
            primary_intent=primary,
            secondary_intents=secondary,
            confidence=max(confidence, rule_decision.confidence),
            risk_level=risk_level,
            entities=entities,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            allowed_actions=allowed_actions,
            task_plan=task_plan,
            reason=(
                "llm_refined; "
                + str(payload.get("reason") or "").strip()[:300]
                + f"; rule_reason={rule_decision.reason}"
            ),
        )

    def _max_risk(self, rule_risk: str, llm_risk: str) -> str:
        rule_value = self.RISK_ORDER.get(rule_risk, 0)
        llm_value = self.RISK_ORDER.get(llm_risk, 0)
        value = max(rule_value, llm_value)
        for risk, index in self.RISK_ORDER.items():
            if index == value:
                return risk
        return rule_risk

    def _clamp_confidence(self, value: Any, default: float) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, confidence))

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, dict) else None

    def _looks_semantically_complex(self, message: str) -> bool:
        text = (message or "").lower()
        decision_terms = [
            "should i",
            "can i",
            "whether",
            "change",
            "adjust",
            "modify",
            "tomorrow",
            "but",
            "not sure",
            "felt off",
            "stall",
            "stalled",
            "plateau",
        ]
        return any(term in text for term in decision_terms)

    def _system_prompt(self) -> str:
        return (
            "You are an intent classifier for an AI fitness coach. Return only JSON. "
            "Classify the current user message, not older conversation. "
            "Preserve safety: pain, injury, medication, dizziness, chest tightness, or disease context must include injury_or_risk. "
            "Use valid intents only: "
            + ", ".join(sorted(AgentIntentCatalog.VALID_INTENTS))
            + ". JSON keys: primary_intent, secondary_intents, confidence, risk_level, entities, "
            "missing_slots, needs_clarification, reason."
        )

    def _user_prompt(self, message: str, rule_decision: IntentDecision, profile: Any | None) -> str:
        profile_summary = {
            "age": getattr(profile, "age", None),
            "height_cm": getattr(profile, "height_cm", None),
            "weight_kg": getattr(profile, "weight_kg", None),
            "goal": getattr(profile, "goal", None),
            "experience_level": getattr(profile, "experience_level", None),
            "equipment_available": getattr(profile, "equipment_available", None),
            "injuries": getattr(profile, "injuries", None),
        }
        return json.dumps(
            {
                "current_user_message": message,
                "profile_summary": profile_summary,
                "rule_decision": rule_decision.to_dict(),
                "instruction": (
                    "If the user has multiple requests, return one primary intent and all secondary intents. "
                    "If a plan request is unsafe or missing details, set needs_clarification=true."
                ),
            },
            ensure_ascii=False,
            default=str,
        )


class AgentIntentCatalog:
    VALID_INTENTS = {
        "general_chat",
        "concept_explanation",
        "small_talk",
        "onboarding",
        "profile_update",
        "profile_correction",
        "training_plan",
        "training_log",
        "progression_decision",
        "nutrition_advice",
        "nutrition_log",
        "recovery_check",
        "injury_or_risk",
        "weekly_review",
        "monthly_review",
        "memory_query",
    }
