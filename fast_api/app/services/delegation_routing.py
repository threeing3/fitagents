"""Cheap dispatch decisions, NOT proof of action readiness or execution permission."""

import re

from fast_api.app.services.intent_cascade import CascadeAssessment, IntentCascadePolicy
from fast_api.app.services.intent_decision import IntentDecision


class DelegationRoutingPolicy:
    """Leave composite/ambiguous language to one host understanding pass."""

    PATTERNS = {
        **IntentCascadePolicy.SIMPLE_PATTERNS,
        "concept_explanation": r"(?:请)?(?:什么是|解释一下|说明一下).{1,35}",
        "nutrition_advice": r"(?:请|帮我)?(?:推荐|建议)(?:一份|一个)?(?:运动后|训练后|减脂|增肌)?(?:的)?(?:早餐|午餐|晚餐|晚饭|饮食搭配)",
        "recovery_check": r"(?:请|帮我)?(?:评估|检查)(?:一下)?(?:今天|当前)?(?:的)?(?:恢复状态|疲劳状态|训练准备状态)",
    }
    COMPLEX = re.compile(
        r"[，,；;\n“”‘’\"'「」]|不要|不需要|不用|不是|取消|别|再|然后|同时|另外|并且|以及|"
        r"还有|顺便|既|又|记下|保存|记录|修改|更新|纠正|改成|换成|那个|第二个|刚才|照旧|确认"
    )

    def assess(self, message: str, decision: IntentDecision) -> CascadeAssessment:
        text = message.strip().lower().rstrip("。！？!? ")
        # Exact existing read-query allowlist is not a write merely because it says 'record'.
        query = self.PATTERNS["memory_query"]
        clear_query = re.fullmatch(query, text) is not None
        if (
            decision.risk_level != "low"
            or decision.secondary_intents
            or (not clear_query and self.COMPLEX.search(text))
        ):
            return CascadeAssessment("escalate", "dispatch", "host_understanding_required")
        for intent, pattern in self.PATTERNS.items():
            if re.fullmatch(pattern, text) and decision.primary_intent in {intent, "general_chat"}:
                return CascadeAssessment("accept", "rule", "full_message_allowlist", intent=intent)
        return CascadeAssessment("escalate", "dispatch", "host_understanding_required")
