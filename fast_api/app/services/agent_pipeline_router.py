import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from fast_api.app.services.context_builder import IntentRouter
from fast_api.app.services.intent_decision import IntentDecision
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)

AgentPipeline = Literal["llm_driven", "code_driven"]


@dataclass
class PipelineRoutingDecision:
    pipeline: AgentPipeline
    intent: str
    confidence: float
    reason: str
    source: str
    llm_pipeline: str | None = None
    llm_intent: str | None = None
    override_applied: bool = False
    override_reason: str | None = None
    latency_ms: int = 0
    fallback_reason: str | None = None
    intent_decision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline,
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "source": self.source,
            "llm_pipeline": self.llm_pipeline,
            "llm_intent": self.llm_intent,
            "override_applied": self.override_applied,
            "override_reason": self.override_reason,
            "latency_ms": self.latency_ms,
            "fallback_reason": self.fallback_reason,
            "intent_decision": self.intent_decision,
        }


class AgentPipelineRouter:
    """Route each turn to the right agent pipeline.

    The router follows a proven production pattern: let the LLM classify the
    current turn, then apply deterministic safety and state-mutation overrides.
    Simple conversation can use the lighter LLM-driven agent; anything that
    updates profile, memory, plans, logs, recovery, nutrition, or risk state is
    forced through the code-driven pipeline.
    """

    CODE_DRIVEN_INTENTS = {
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
    LLM_DRIVEN_INTENTS = {"general_chat", "concept_explanation", "small_talk"}
    VALID_INTENTS = CODE_DRIVEN_INTENTS | LLM_DRIVEN_INTENTS
    VALID_PIPELINES = {"llm_driven", "code_driven"}

    def __init__(self, model_provider: ModelProvider | None = None, intent_router: IntentRouter | None = None):
        self.model_provider = model_provider or ModelProvider()
        self.intent_router = intent_router or IntentRouter()

    async def route(self, message: str, profile: Any | None = None) -> PipelineRoutingDecision:
        started = time.perf_counter()
        fallback = self._rule_decision(message, profile)
        llm_payload: dict[str, Any] | None = None
        fallback_reason: str | None = None

        model = self.model_provider.chat_model(temperature=0.0)
        if model is not None:
            try:
                response = await model.ainvoke(
                    [
                        SystemMessage(content=self._system_prompt()),
                        HumanMessage(content=self._user_prompt(message, profile, fallback)),
                    ]
                )
                llm_payload = self._parse_json(str(response.content))
            except Exception as exc:
                fallback_reason = f"llm_router_failed: {type(exc).__name__}: {exc}"
                logger.warning("Agent pipeline router LLM failed, using rule fallback: %s", exc)
        else:
            fallback_reason = "no_live_model_for_router"

        if llm_payload:
            decision = self._decision_from_llm(llm_payload, fallback)
            decision.source = "llm_with_rule_override"
        else:
            decision = fallback
            decision.source = "rule_fallback"
            decision.fallback_reason = fallback_reason

        decision.latency_ms = round((time.perf_counter() - started) * 1000)
        return decision

    def _decision_from_llm(
        self,
        payload: dict[str, Any],
        fallback: PipelineRoutingDecision,
    ) -> PipelineRoutingDecision:
        llm_pipeline = str(payload.get("pipeline") or "").strip()
        llm_intent = str(payload.get("intent") or "").strip()
        confidence = self._clamp_confidence(payload.get("confidence"), fallback.confidence)
        reason = str(payload.get("reason") or "").strip()[:500] or "LLM classified the current turn."

        if llm_pipeline not in self.VALID_PIPELINES:
            return self._fallback_with_reason(fallback, f"invalid_llm_pipeline:{llm_pipeline}")
        if llm_intent not in self.VALID_INTENTS:
            return self._fallback_with_reason(fallback, f"invalid_llm_intent:{llm_intent}")

        pipeline: AgentPipeline = "code_driven" if llm_pipeline == "code_driven" else "llm_driven"
        intent = llm_intent
        override_applied = False
        override_reason = None

        if fallback.pipeline == "code_driven" and pipeline == "llm_driven":
            pipeline = "code_driven"
            intent = fallback.intent
            override_applied = True
            override_reason = (
                "rule_guardrail_for_stateful_or_fitness_turn: profile/memory/plan/log/risk "
                "operations must use code-driven pipeline"
            )

        if intent in self.CODE_DRIVEN_INTENTS and pipeline != "code_driven":
            pipeline = "code_driven"
            override_applied = True
            override_reason = "intent_requires_code_driven_pipeline"

        return PipelineRoutingDecision(
            pipeline=pipeline,
            intent=intent,
            confidence=confidence,
            reason=reason,
            source="llm_with_rule_override",
            llm_pipeline=llm_pipeline,
            llm_intent=llm_intent,
            override_applied=override_applied,
            override_reason=override_reason,
            intent_decision=fallback.intent_decision,
        )

    def _rule_decision(self, message: str, profile: Any | None) -> PipelineRoutingDecision:
        structured = self.intent_router.analyze(message, profile=profile)
        intent = self._rule_intent(message, structured)
        pipeline: AgentPipeline = "code_driven" if intent in self.CODE_DRIVEN_INTENTS else "llm_driven"
        reason = "规则兜底：普通闲聊走 LLM-driven；建档、计划、日志、饮食、恢复、伤痛和记忆相关请求走 code-driven。"
        missing_slots = self._missing_profile_slots(profile)
        if missing_slots and self._looks_like_profile_message(message):
            intent = "onboarding"
            pipeline = "code_driven"
            reason = "规则兜底：当前消息像建档信息，需要结构化抽取并落库。"
        return PipelineRoutingDecision(
            pipeline=pipeline,
            intent=intent,
            confidence=0.72 if pipeline == "code_driven" else 0.62,
            reason=reason,
            source="rule",
            intent_decision=structured.to_dict(),
        )

    def _rule_intent(self, message: str, structured: IntentDecision | None = None) -> str:
        text = message.lower()
        if self._has_any(text, ["没有肩伤", "没肩伤", "没有伤", "不是肩伤", "档案错", "纠正", "不是我的", "remove", "correction"]):
            return "profile_correction"
        if self._has_any(text, ["胸闷", "头晕", "呼吸困难", "刺痛", "疼", "痛", "受伤", "伤病", "甲亢", "甲状腺", "用药", "pain", "injury", "dizzy"]):
            return "injury_or_risk"
        if self._has_any(text, ["周复盘", "本周复盘", "weekly review"]):
            return "weekly_review"
        if self._has_any(text, ["月度总结", "月复盘", "monthly review"]):
            return "monthly_review"
        if self._has_any(text, ["训练计划", "健身计划", "生成计划", "制定计划", "今天练什么", "今天应该干什么", "workout plan", "training plan"]):
            return "training_plan"
        if self._has_any(text, ["加重", "加重量", "下次", "progression", "progress"]):
            return "progression_decision"
        if self._has_any(text, ["kg", "公斤", "卧推", "深蹲", "硬拉", "rpe", "做组", "做了", "练完", "练胸", "练背", "bench", "squat", "deadlift"]):
            return "training_log"
        if self._has_any(text, ["热量", "蛋白", "碳水", "脂肪", "外卖", "外食", "吃什么", "饮食", "calorie", "protein"]):
            return "nutrition_advice"
        if self._has_any(text, ["睡眠", "疲劳", "酸痛", "恢复", "压力", "心率", "recovery", "sleep", "tired"]):
            return "recovery_check"
        if self._has_any(text, ["你记得", "我的档案", "长期记忆", "记忆", "profile", "memory"]):
            return "memory_query"

        context_intent = structured.primary_intent if structured is not None else self.intent_router.classify(message)
        if context_intent and context_intent != "general_chat":
            return context_intent
        if self._looks_like_profile_message(message):
            return "profile_update"
        if self._has_any(text, ["为什么", "原理", "解释", "是什么", "how does", "why"]):
            return "concept_explanation"
        return "general_chat"

    def _looks_like_profile_message(self, message: str) -> bool:
        text = message.lower()
        profile_terms = [
            "年龄", "身高", "体重", "目标", "男", "女", "岁", "cm", "kg",
            "训练经验", "健身房", "器械", "不自己做饭", "外食", "睡眠", "步数",
        ]
        return sum(1 for term in profile_terms if term in text) >= 2

    def _missing_profile_slots(self, profile: Any | None) -> list[str]:
        if profile is None:
            return []
        slots = ["age", "height_cm", "weight_kg", "goal", "experience_level", "equipment_available"]
        return [slot for slot in slots if not getattr(profile, slot, None)]

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

    def _fallback_with_reason(self, fallback: PipelineRoutingDecision, reason: str) -> PipelineRoutingDecision:
        fallback.source = "rule_fallback"
        fallback.fallback_reason = reason
        return fallback

    def _clamp_confidence(self, value: Any, default: float) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, confidence))

    def _has_any(self, text: str, terms: list[str]) -> bool:
        return any(term in text for term in terms)

    def _system_prompt(self) -> str:
        return (
            "你是 AI 私教 Agent 的管道路由器，只能输出 JSON，不要输出解释文字。\n"
            "任务：根据当前用户消息判断本轮应该走哪条执行管道。\n"
            "可选 pipeline：llm_driven, code_driven。\n"
            "llm_driven：适合普通闲聊、简单概念解释、不需要写数据库、不需要工具校验的回答。\n"
            "code_driven：适合建档、纠错、长期记忆、训练计划、训练日志、饮食建议、恢复、伤痛风险、周/月复盘等需要结构化状态或规则校验的请求。\n"
            "可选 intent：general_chat, concept_explanation, small_talk, onboarding, profile_update, "
            "profile_correction, training_plan, training_log, progression_decision, nutrition_advice, "
            "nutrition_log, recovery_check, injury_or_risk, weekly_review, monthly_review, memory_query。\n"
            "输出格式：{\"pipeline\":\"...\",\"intent\":\"...\",\"confidence\":0.0-1.0,\"reason\":\"一句话理由\"}"
        )

    def _user_prompt(
        self,
        message: str,
        profile: Any | None,
        fallback: PipelineRoutingDecision,
    ) -> str:
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
                "rule_fallback_hint": fallback.to_dict(),
                "routing_policy": "当前消息优先；历史上下文只能作为背景，不能把上一轮命令延续到本轮。",
            },
            ensure_ascii=False,
            default=str,
        )
