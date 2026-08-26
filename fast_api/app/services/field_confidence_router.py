"""Field-level source selection for structured intent decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fast_api.app.services.intent_decision import IntentDecision, IntentRouter


@dataclass
class FieldRoutePlan:
    field_confidence: dict[str, float]
    requested_sources: dict[str, str]
    low_confidence_fields: list[str] = field(default_factory=list)

    @property
    def requires_deepseek(self) -> bool:
        return bool(self.low_confidence_fields)


class FieldConfidenceRouter:
    """Route semantic fields independently while reserving safety for rules."""

    DEFAULT_THRESHOLDS = {
        "primary_intent": 0.80,
        "secondary_intents": 0.75,
    }

    def __init__(self, thresholds: dict[str, float] | None = None):
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}

    def plan(self, adapter_payload: dict[str, Any]) -> FieldRoutePlan:
        confidence = self._confidence_map(adapter_payload.get("confidence"))
        requested_sources: dict[str, str] = {
            "risk_level": "deterministic_rule_floor",
            "needs_clarification": "clarification_protocol",
        }
        low: list[str] = []
        for field_name, threshold in self.thresholds.items():
            score = confidence[field_name]
            source = "intent_adapter" if score >= threshold else "deepseek_review"
            requested_sources[field_name] = source
            if source == "deepseek_review":
                low.append(field_name)
        return FieldRoutePlan(confidence, requested_sources, low)

    def merge(
        self,
        adapter: IntentDecision,
        deepseek: IntentDecision | None,
        rule: IntentDecision,
        plan: FieldRoutePlan,
        intent_router: IntentRouter,
    ) -> tuple[IntentDecision, dict[str, str]]:
        deepseek_available = deepseek is not None
        sources: dict[str, str] = {}

        def choose(field_name: str, adapter_value: Any, deepseek_value: Any) -> Any:
            if plan.requested_sources[field_name] == "deepseek_review" and deepseek_available:
                sources[field_name] = "deepseek"
                return deepseek_value
            sources[field_name] = "intent_adapter"
            return adapter_value

        primary = choose(
            "primary_intent", adapter.primary_intent, getattr(deepseek, "primary_intent", None)
        )
        secondary = choose(
            "secondary_intents",
            list(adapter.secondary_intents),
            list(deepseek.secondary_intents) if deepseek else [],
        )
        secondary = intent_router._dedupe(list(secondary) + list(rule.secondary_intents))
        secondary = [intent for intent in secondary if intent != primary]
        merged = IntentDecision(
            primary_intent=str(primary),
            secondary_intents=secondary,
            confidence=adapter.confidence,
            risk_level=adapter.risk_level,
            entities={**adapter.entities, **(deepseek.entities if deepseek else {})},
            missing_slots=intent_router._dedupe(
                list(adapter.missing_slots) + (list(deepseek.missing_slots) if deepseek else [])
            ),
            needs_clarification=adapter.needs_clarification
            or bool(deepseek and deepseek.needs_clarification),
            allowed_actions=dict(adapter.allowed_actions),
            task_plan=list(adapter.task_plan),
            reason="field_level_route; " + adapter.reason,
        )
        sources["risk_level"] = "deterministic_rule_floor"
        sources["needs_clarification"] = "clarification_protocol"
        return merged, sources

    @staticmethod
    def _confidence_map(value: Any) -> dict[str, float]:
        if isinstance(value, dict):
            overall = FieldConfidenceRouter._clamp(value.get("overall"), 0.0)
            return {
                "primary_intent": FieldConfidenceRouter._clamp(
                    value.get("primary_intent"), overall
                ),
                "secondary_intents": FieldConfidenceRouter._clamp(
                    value.get("secondary_intents"), overall
                ),
            }
        overall = FieldConfidenceRouter._clamp(value, 0.0)
        return {"primary_intent": overall, "secondary_intents": overall}

    @staticmethod
    def _clamp(value: Any, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default
