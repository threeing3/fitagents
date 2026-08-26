"""One-pass intent decision engine with deterministic safety authority."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from fast_api.app.services.clarification_protocol import ClarificationProtocolValidator
from fast_api.app.services.field_confidence_router import FieldConfidenceRouter
from fast_api.app.services.intent_contract import IntentDecisionV2
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter
from fast_api.app.services.intent_inference_client import IntentInferenceClient
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
        inference_client: IntentInferenceClient | None = None,
        field_router: FieldConfidenceRouter | None = None,
        clarification_validator: ClarificationProtocolValidator | None = None,
    ):
        self.model_provider = model_provider or ModelProvider()
        self.intent_router = intent_router or IntentRouter()
        self.classifier = LLMIntentClassifier(self.model_provider, self.intent_router)
        self.inference_client = inference_client or IntentInferenceClient(
            self.model_provider.settings
        )
        self.field_router = field_router or FieldConfidenceRouter()
        self.clarification_validator = clarification_validator or ClarificationProtocolValidator()
        self.runtime_router = RuntimeRouter(self.intent_router)

    async def decide(self, message: str, profile: Any | None = None) -> IntentEngineResult:
        started = time.perf_counter()
        rule_started = time.perf_counter()
        rule_decision = self.intent_router.analyze(message, profile=profile)
        rule_ms = round((time.perf_counter() - rule_started) * 1000)

        model_started = time.perf_counter()
        should_refine = self.classifier.should_refine(rule_decision, message)
        local_trace = (
            await self.inference_client.classify(
                message,
                rule_decision.to_dict(),
                self._profile_summary(profile),
            )
            if should_refine
            else None
        )
        safety_override_reasons: list[str] = []
        field_route_plan = None
        field_sources: dict[str, str] = {
            "primary_intent": "deterministic_rule",
            "secondary_intents": "deterministic_rule",
            "risk_level": "deterministic_rule_floor",
            "needs_clarification": "clarification_protocol",
        }
        if local_trace and local_trace.succeeded and local_trace.payload:
            safety_override_reasons = self._adapter_safety_override_reasons(
                rule_decision, local_trace.payload
            )
            adapter_decision = self.classifier._merge_with_rule_decision(
                local_trace.payload, rule_decision
            )
            field_route_plan = self.field_router.plan(local_trace.payload)
            deepseek_decision = None
            review_trace: dict[str, Any] = {
                "attempted": False,
                "succeeded": False,
                "fallback_reason": "field_review_not_required",
            }
            if field_route_plan.requires_deepseek:
                deepseek_decision, review_trace = await self.classifier.refine_with_trace(
                    message, rule_decision, profile=profile, force_refine=True
                )
                if not review_trace.get("succeeded"):
                    deepseek_decision = None
            final_decision, field_sources = self.field_router.merge(
                adapter_decision,
                deepseek_decision,
                rule_decision,
                field_route_plan,
                self.intent_router,
            )
            model_trace = {
                "attempted": bool(review_trace.get("attempted")),
                "succeeded": bool(review_trace.get("succeeded")),
                "fallback_reason": review_trace.get("fallback_reason"),
                "usage": review_trace.get("usage") or {},
            }
        else:
            final_decision, model_trace = await self.classifier.refine_with_trace(
                message, rule_decision, profile=profile
            )
            if model_trace.get("succeeded"):
                field_sources["primary_intent"] = "deepseek"
                field_sources["secondary_intents"] = "deepseek"
        model_ms = round((time.perf_counter() - model_started) * 1000)
        final_decision = self._enforce_rule_safety(rule_decision, final_decision)
        clarification = self.clarification_validator.validate(message, final_decision, profile)
        final_decision = self.clarification_validator.apply(
            final_decision, clarification, self.intent_router
        )

        route = self.runtime_router.route_decision(final_decision, message=message)
        provider = self.model_provider.settings.llm_provider
        model_succeeded = bool(model_trace.get("succeeded"))
        provenance = {
            "rule_used": True,
            "rule_version": "intent_rules_v2",
            "rule_risk_detected": rule_decision.risk_level in {"medium", "high", "critical"},
            "local_model_attempted": bool(local_trace and local_trace.attempted),
            "local_model_used": bool(local_trace and local_trace.succeeded),
            "local_model_status": local_trace.status if local_trace else "refinement_not_required",
            "local_model_version": local_trace.model_version if local_trace else None,
            "local_model_usage": local_trace.usage if local_trace else {},
            "adapter_fallback_reason": (
                local_trace.status
                if local_trace and local_trace.attempted and not local_trace.succeeded
                else None
            ),
            "adapter_http_status": local_trace.http_status if local_trace else None,
            "deepseek_used": model_succeeded and provider == "deepseek",
            "model_attempted": bool(model_trace.get("attempted")),
            "model_succeeded": model_succeeded,
            "model_provider": (
                "intent_adapter"
                if local_trace and local_trace.succeeded
                else provider
                if model_trace.get("attempted")
                else None
            ),
            "model_version": (
                local_trace.model_version
                if local_trace and local_trace.succeeded
                else self.model_provider.settings.chat_model
                if model_succeeded
                else None
            ),
            "model_fallback_reason": model_trace.get("fallback_reason"),
            "model_usage": model_trace.get("usage") or {},
            "safety_override_applied": bool(safety_override_reasons),
            "safety_override_reasons": safety_override_reasons,
            "field_sources": field_sources,
            "field_confidence": (field_route_plan.field_confidence if field_route_plan else {}),
            "low_confidence_fields": (
                field_route_plan.low_confidence_fields if field_route_plan else []
            ),
            "clarification_reason_codes": clarification.reason_codes,
            "clarification_blocked_actions": clarification.blocked_actions,
            "final_source": (
                "field_fusion_with_rule_override"
                if local_trace and local_trace.succeeded and model_succeeded
                else "adapter_with_rule_override"
                if local_trace and local_trace.succeeded
                else "model_with_rule_override"
                if model_succeeded
                else "rule_fallback"
            ),
        }
        v2 = IntentDecisionV2.from_legacy(
            final_decision,
            provenance=provenance,
            latency_ms={
                "rule": rule_ms,
                "model": model_ms if model_trace.get("attempted") else 0,
                "local_model": local_trace.latency_ms if local_trace else 0,
                "total": round((time.perf_counter() - started) * 1000),
            },
            candidate_tools=self._candidate_tools(final_decision),
            risk_evidence=self._risk_evidence(message),
        )
        if field_route_plan:
            v2.confidence.update(field_route_plan.field_confidence)
        return IntentEngineResult(v2, route.mode, route.reason, route.matched_rules)

    @staticmethod
    def _adapter_safety_override_reasons(
        rule: IntentDecision, payload: dict[str, Any]
    ) -> list[str]:
        """Describe only real rule-over-model safety corrections."""

        reasons: list[str] = []
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        candidate_risk = str(payload.get("risk_level") or "low")
        if risk_order.get(candidate_risk, 0) < risk_order.get(rule.risk_level, 0):
            reasons.append("rule_risk_floor")
        candidate_primary = str(payload.get("primary_intent") or "")
        if rule.primary_intent == "injury_or_risk" and candidate_primary != "injury_or_risk":
            reasons.append("rule_injury_primary_intent")
        return reasons

    @staticmethod
    def _profile_summary(profile: Any | None) -> dict[str, Any]:
        fields = ("age", "height_cm", "weight_kg", "goal", "experience_level", "injuries")
        return {name: getattr(profile, name, None) for name in fields}

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
