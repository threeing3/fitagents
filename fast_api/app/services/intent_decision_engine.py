"""One-pass intent decision engine with deterministic safety authority."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from fast_api.app.services.intent_contract import IntentDecisionV2
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter
from fast_api.app.services.llm_intent_classifier import LLMIntentClassifier
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.runtime_router import RuntimeMode, RuntimeRouter

TOOLS_BY_INTENT: dict[str, list[str]] = {
    "injury_or_risk": ["context.build", "safety.check", "recovery.evaluate"],
    "training_plan": ["context.build", "memory.search", "knowledge.retrieve", "plan.generate"],
    "training_log": ["context.build", "training.log.write"],
    "progression_decision": ["context.build", "memory.search", "recovery.evaluate"],
    "nutrition_log": ["context.build", "nutrition.log.write"],
    "nutrition_advice": ["context.build", "nutrition.estimate", "knowledge.retrieve"],
    "recovery_check": ["context.build", "recovery.evaluate"],
    "memory_query": ["memory.search"],
    "profile_update": ["profile.update"],
    "profile_correction": ["profile.correct"],
    "weekly_review": ["context.build", "review.weekly"],
    "monthly_review": ["context.build", "review.monthly"],
    "general_chat": ["context.build"],
}


@dataclass
class IntentEngineResult:
    decision: IntentDecisionV2
    runtime_mode: RuntimeMode
    runtime_reason: str
    matched_rules: list[str]


class IntentDecisionEngine:
    """Produce exactly one reusable decision for a user turn."""

    def __init__(
        self,
        model_provider: ModelProvider | None = None,
        intent_router: IntentRouter | None = None,
    ):
        self.model_provider = model_provider or ModelProvider()
        self.intent_router = intent_router or IntentRouter()
        self.classifier = LLMIntentClassifier(self.model_provider, self.intent_router)
        self.runtime_router = RuntimeRouter(self.intent_router)

    async def decide(self, message: str, profile: Any | None = None) -> IntentEngineResult:
        started = time.perf_counter()
        rule_started = time.perf_counter()
        rule_decision = self.intent_router.analyze(message, profile=profile)
        rule_ms = round((time.perf_counter() - rule_started) * 1000)

        model_started = time.perf_counter()
        final_decision, model_trace = await self.classifier.refine_with_trace(
            message, rule_decision, profile=profile
        )
        model_ms = round((time.perf_counter() - model_started) * 1000)
        final_decision = self._enforce_rule_safety(rule_decision, final_decision)

        route = self.runtime_router.route_decision(final_decision, message=message)
        provider = self.model_provider.settings.llm_provider
        model_succeeded = bool(model_trace.get("succeeded"))
        provenance = {
            "rule_used": True,
            "rule_version": "intent_rules_v2",
            "rule_risk_detected": rule_decision.risk_level in {"medium", "high", "critical"},
            "local_model_used": False,
            "deepseek_used": model_succeeded and provider == "deepseek",
            "model_attempted": bool(model_trace.get("attempted")),
            "model_succeeded": model_succeeded,
            "model_provider": provider if model_trace.get("attempted") else None,
            "model_version": self.model_provider.settings.chat_model if model_succeeded else None,
            "model_fallback_reason": model_trace.get("fallback_reason"),
            "model_usage": model_trace.get("usage") or {},
            "final_source": "model_with_rule_override" if model_succeeded else "rule_fallback",
        }
        v2 = IntentDecisionV2.from_legacy(
            final_decision,
            provenance=provenance,
            latency_ms={
                "rule": rule_ms,
                "model": model_ms if model_trace.get("attempted") else 0,
                "total": round((time.perf_counter() - started) * 1000),
            },
            candidate_tools=self._candidate_tools(final_decision),
            risk_evidence=self._risk_evidence(message),
        )
        return IntentEngineResult(v2, route.mode, route.reason, route.matched_rules)

    def _enforce_rule_safety(
        self, rule: IntentDecision, candidate: IntentDecision
    ) -> IntentDecision:
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if risk_order.get(candidate.risk_level, 0) < risk_order.get(rule.risk_level, 0):
            candidate.risk_level = rule.risk_level
        if rule.primary_intent == "injury_or_risk" and candidate.primary_intent != "injury_or_risk":
            candidate.secondary_intents = self.intent_router._dedupe(
                [candidate.primary_intent] + candidate.secondary_intents
            )
            candidate.primary_intent = "injury_or_risk"
        candidate.allowed_actions = self.intent_router._allowed_actions(
            candidate.primary_intent,
            candidate.secondary_intents,
            candidate.risk_level,
            candidate.needs_clarification,
        )
        candidate.task_plan = self.intent_router._build_task_plan(
            candidate.primary_intent,
            candidate.secondary_intents,
            candidate.allowed_actions,
            candidate.missing_slots,
            candidate.risk_level,
        )
        return candidate

    def _candidate_tools(self, decision: IntentDecision) -> list[str]:
        tools: list[str] = []
        for intent in [decision.primary_intent] + decision.secondary_intents:
            for tool in TOOLS_BY_INTENT.get(intent, ["context.build"]):
                if tool not in tools:
                    tools.append(tool)
        return tools

    def _risk_evidence(self, message: str) -> list[str]:
        text = message.lower()
        return [term for term in self.intent_router.RISK_TERMS if term in text][:8]
