from dataclasses import dataclass, field
from typing import Any, Literal

from fast_api.app.services.intent_decision import IntentRouter


RuntimeMode = Literal["llm_driven", "code_driven"]


@dataclass
class RuntimeRoute:
    mode: RuntimeMode
    reason: str
    matched_rules: list[str] = field(default_factory=list)
    confidence: float = 0.5
    intent_decision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "matched_rules": self.matched_rules,
            "confidence": self.confidence,
            "intent_decision": self.intent_decision,
        }


class RuntimeRouter:
    """Lightweight rule router for choosing LLM-driven or code-driven runtime.

    Routing is intentionally deterministic and dependency-free. The first
    version does not call an LLM: fitness requests that may need tools,
    database writes, long-term memory, plan generation, or safety guardrails
    go to code-driven; lightweight explanation and small talk can go to
    LLM-driven. Unknown cases default to code-driven.
    """

    EXPLANATION_TERMS = [
        "什么是",
        "是什么意思",
        "解释一下",
        "通俗讲",
        "原理",
        "区别",
        "why",
        "what is",
        "explain",
    ]
    CHAT_TERMS = ["鼓励我", "没动力", "聊聊", "你好", "谢谢", "早上好", "晚上好", "hi", "hello", "thanks"]

    PLAN_TERMS = [
        "训练计划",
        "制定计划",
        "生成计划",
        "周计划",
        "一周几练",
        "分化",
        "push pull legs",
        "ppl",
        "增肌",
        "减脂",
        "有氧",
    ]
    TRAINING_BODY_TERMS = ["胸", "背", "腿", "肩", "手臂", "核心"]
    TRAINING_ACTION_TERMS = ["练", "训练", "动作", "组", "次数", "卧推", "深蹲", "硬拉", "划船", "推举"]
    NUTRITION_RECORD_TERMS = [
        "今天吃了",
        "早餐",
        "午餐",
        "晚餐",
        "加餐",
        "热量",
        "卡路里",
        "克",
        "米饭",
        "鸡胸",
        "鸡蛋",
        "蛋白粉",
        "记录饮食",
        "计算",
    ]
    NUTRITION_CONCEPT_TERMS = ["蛋白质", "蛋白", "碳水", "脂肪"]
    STATE_WRITE_TERMS = ["记录", "保存", "更新", "修改", "体重", "身高", "年龄", "目标", "经验", "器械"]
    RISK_TERMS = [
        "疼",
        "痛",
        "受伤",
        "拉伤",
        "扭伤",
        "闷",
        "胸闷",
        "心脏",
        "心率",
        "甲亢",
        "药",
        "头晕",
        "恶心",
        "尿频",
        "骨盆",
        "盆底",
    ]
    PLAN_EDIT_TERMS = [
        "调整计划", "改计划", "计划改", "改成", "换成", "替换动作", "减少训练", "增加训练",
        "change my plan", "modify my plan", "adjust my plan",
    ]

    CODE_DRIVEN_INTENTS = {
        "profile_correction",
        "profile_update",
        "training_plan",
        "training_log",
        "progression_decision",
        "nutrition_log",
        "recovery_check",
        "injury_or_risk",
        "weekly_review",
        "monthly_review",
        "memory_query",
    }

    def __init__(self, intent_router: IntentRouter | None = None):
        self.intent_router = intent_router or IntentRouter()

    def route(self, message: str) -> RuntimeRoute:
        text = (message or "").strip().lower()
        matched: list[str] = []

        if not text:
            return RuntimeRoute(
                mode="code_driven",
                reason="空消息无法可靠判断，默认走 Code-driven。",
                matched_rules=["fallback.empty_message"],
                confidence=0.55,
            )

        if self._is_pure_explanation(text):
            concept_matches = self._matches(text, self.EXPLANATION_TERMS, "explanation")
            nutrition_concepts = self._matches(text, self.NUTRITION_CONCEPT_TERMS, "nutrition_concept")
            return RuntimeRoute(
                mode="llm_driven",
                reason="当前是概念解释类问题，不涉及记录、计划生成或安全风险。",
                matched_rules=concept_matches + nutrition_concepts,
                confidence=0.82,
            )

        intent_decision = self.intent_router.analyze(message)
        if intent_decision.primary_intent in self.CODE_DRIVEN_INTENTS:
            return RuntimeRoute(
                mode="code_driven",
                reason=(
                    "Structured intent decision requires code-driven execution for state, "
                    "plan, memory, recovery, risk, or log handling."
                ),
                matched_rules=[f"intent:{intent_decision.primary_intent}"]
                + [f"secondary_intent:{intent}" for intent in intent_decision.secondary_intents],
                confidence=max(0.8, intent_decision.confidence),
                intent_decision=intent_decision.to_dict(),
            )

        matched.extend(self._matches(text, self.RISK_TERMS, "risk"))
        if matched:
            return RuntimeRoute(
                mode="code_driven",
                reason="命中安全/医疗风险关键词，需要安全护栏、档案和长期记忆参与。",
                matched_rules=matched,
                confidence=0.96,
            )

        matched.extend(self._matches(text, self.PLAN_EDIT_TERMS, "plan_edit"))
        if matched:
            return RuntimeRoute(
                mode="code_driven",
                reason="用户明确要求修改已有计划，需要读取和更新计划状态。",
                matched_rules=matched,
                confidence=0.95,
            )

        matched.extend(self._matches(text, self.PLAN_TERMS, "training_plan"))
        if "计划" in text and any(term in text for term in ["训练", "健身", "一周", "周", "练"]):
            matched.append("training_plan:计划")
        if matched:
            return RuntimeRoute(
                mode="code_driven",
                reason="命中训练计划类请求，需要计划生成、规则校验或数据库状态。",
                matched_rules=matched,
                confidence=0.93,
            )

        nutrition_record_matches = self._matches(text, self.NUTRITION_RECORD_TERMS, "nutrition_record")
        if nutrition_record_matches:
            return RuntimeRoute(
                mode="code_driven",
                reason="命中饮食记录或营养计算关键词，需要工具、数据库或结构化计算。",
                matched_rules=nutrition_record_matches,
                confidence=0.9,
            )

        state_matches = self._matches(text, self.STATE_WRITE_TERMS, "state_write")
        if state_matches and not self._is_pure_explanation(text):
            return RuntimeRoute(
                mode="code_driven",
                reason="命中状态写入/用户档案关键词，需要结构化抽取并保持长期状态一致。",
                matched_rules=state_matches,
                confidence=0.9,
            )

        training_matches = self._matches(text, self.TRAINING_ACTION_TERMS, "training_action")
        body_matches = self._matches(text, self.TRAINING_BODY_TERMS, "training_body")
        if training_matches or ("今天" in text and body_matches):
            return RuntimeRoute(
                mode="code_driven",
                reason="命中训练执行/训练日志相关信息，需要写入训练表现或调用训练规则。",
                matched_rules=training_matches + body_matches,
                confidence=0.88,
            )

        chat_matches = self._matches(text, self.CHAT_TERMS, "chat")
        if chat_matches:
            return RuntimeRoute(
                mode="llm_driven",
                reason="当前是普通闲聊/鼓励，不需要工具、数据库或长期状态。",
                matched_rules=chat_matches,
                confidence=0.78,
            )

        return RuntimeRoute(
            mode="code_driven",
            reason="未命中轻量 LLM-driven 规则；健身 Agent 默认走 Code-driven，避免漏掉计划、记忆和安全状态。",
            matched_rules=["fallback.unknown_defaults_to_code_driven"],
            confidence=0.6,
        )

    def _is_pure_explanation(self, text: str) -> bool:
        if not any(term in text for term in self.EXPLANATION_TERMS):
            return False
        blocking_terms = (
            self.RISK_TERMS
            + self.PLAN_EDIT_TERMS
            + self.PLAN_TERMS
            + self.NUTRITION_RECORD_TERMS
            + ["记录", "保存", "更新", "修改", "制定", "生成", "今天吃了"]
        )
        return not any(term in text for term in blocking_terms)

    def _matches(self, text: str, terms: list[str], prefix: str) -> list[str]:
        return [f"{prefix}:{term}" for term in terms if self._term_matches(text, term)]

    def _term_matches(self, text: str, term: str) -> bool:
        if term.isascii() and len(term) <= 3:
            import re

            return re.search(rf"\b{re.escape(term)}\b", text) is not None
        return term in text
