"""Structured intent understanding for the fitness coach domain."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


@dataclass
class IntentDecision:
    """Structured result used by routing, retrieval, and action policy."""

    primary_intent: str
    secondary_intents: list[str] = field(default_factory=list)
    confidence: float = 0.7
    risk_level: str = "low"
    entities: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    allowed_actions: dict[str, bool] = field(default_factory=dict)
    reason: str = ""

    @property
    def intent(self) -> str:
        return self.primary_intent

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_intent": self.primary_intent,
            "secondary_intents": self.secondary_intents,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "entities": self.entities,
            "missing_slots": self.missing_slots,
            "needs_clarification": self.needs_clarification,
            "allowed_actions": self.allowed_actions,
            "reason": self.reason,
        }


class IntentRouter:
    """Rule-first, multi-intent classifier for the fitness coach."""

    RISK_TERMS = [
        "疼", "疼痛", "痛", "刺痛", "胸闷", "胸口闷", "头晕", "呼吸困难", "麻木", "受伤", "拉伤",
        "扭伤", "甲亢", "甲状腺", "吃药", "服药", "用药", "injury", "pain", "dizzy", "chest tightness",
        "medication",
        "鑳搁椃", "澶存檿", "鍛煎惛鍥伴毦", "鍒虹棝", "鍙椾激", "鐢蹭孩", "鐢茬姸鑵",
    ]
    HARD_RISK_TERMS = ["胸闷", "胸口闷", "头晕", "呼吸困难", "麻木", "chest tightness", "dizzy"]
    RECOVERY_TERMS = ["睡", "睡眠", "疲劳", "累", "酸痛", "恢复", "压力", "心率", "recovery", "sleep", "tired"]
    PLAN_TERMS = [
        "训练计划", "健身计划", "生成计划", "制定计划", "做个计划", "出个计划", "给我计划", "帮我计划",
        "安排训练", "安排一个", "今天练什么", "今天应该练什么", "今天应该干什么", "今天干什么",
        "今日训练", "练什么", "明天练什么", "一周计划", "workout plan", "training plan",
        "what should i do today", "what should i train", "should i train", "train chest tomorrow",
        "璁粌璁", "鍋ヨ韩璁", "鐢熸垚璁", "鍒跺畾璁", "浠婂ぉ缁冧粈涔", "浠婂ぉ搴旇",
    ]
    NEGATED_PLAN_TERMS = ["不要", "不需要", "不用", "别", "别给", "不要生成", "先不要", "先别", "do not", "don't", "dont"]
    TRAINING_LOG_TERMS = [
        "完成", "做完", "练了", "练完", "训练了", "kg", "公斤", "rpe", "组", "次数",
        "卧推", "深蹲", "硬拉", "bench", "squat", "deadlift",
        "trained", "did bench", "did squat", "did deadlift",
        "瀹屾垚", "鍋氬畬", "缁冧簡", "鍏枻", "鍗ф帹", "娣辫共", "纭媺",
    ]
    PROGRESSION_TERMS = ["加重", "重量", "进步", "下次", "平台期", "突破", "progress", "increase", "deload", "降载", "stall", "stalled", "plateau"]
    NUTRITION_TERMS = [
        "吃", "热量", "蛋白", "蛋白质", "碳水", "脂肪", "外卖", "外食", "饮食", "calorie", "protein", "diet",
        "鐑噺", "铔嬬櫧", "纰虫按", "鑴傝偑", "澶栧崠", "澶栭",
    ]
    NUTRITION_LOG_TERMS = [
        "记录饮食", "帮我记录", "早餐", "午餐", "晚餐", "加餐", "吃了",
        "record my meal", "record", "breakfast", "lunch", "dinner", "ate",
    ]
    REVIEW_TERMS = ["周复盘", "本周", "weekly", "月复盘", "monthly", "总结"]
    MEMORY_TERMS = ["你记得", "还记得", "我的档案", "记忆", "memory", "profile", "浣犺寰", "鎴戠殑妗ｆ"]
    PROFILE_TERMS = ["年龄", "身高", "体重", "目标", "男", "女", "岁", "cm", "训练经验", "健身房", "器械"]

    BODY_PART_TERMS = {
        "shoulder": ["肩", "肩膀", "肩袖", "shoulder"],
        "knee": ["膝", "膝盖", "knee"],
        "lower_back": ["腰", "下背", "lower back"],
        "chest": ["胸", "胸口", "chest"],
        "leg": ["腿", "大腿", "小腿", "leg"],
        "wrist": ["手腕", "wrist"],
        "elbow": ["手肘", "肘", "elbow"],
    }
    EXERCISE_TERMS = {
        "bench_press": ["卧推", "bench"],
        "squat": ["深蹲", "squat"],
        "deadlift": ["硬拉", "deadlift"],
        "run": ["跑步", "跑", "run"],
    }

    def classify(self, message: str) -> str:
        return self.analyze(message).primary_intent

    def analyze(self, message: str, profile: Any | None = None) -> IntentDecision:
        text = message.lower()
        matched: list[str] = []

        if self._is_profile_correction(text):
            matched.append("profile_correction")
        if _has_any(text, self.RISK_TERMS):
            matched.append("injury_or_risk")
        if _has_any(text, self.REVIEW_TERMS):
            matched.append("monthly_review" if "月" in text or "monthly" in text else "weekly_review")
        if self.is_plan_request(message):
            matched.append("training_plan")
        if _has_any(text, self.PROGRESSION_TERMS):
            matched.append("progression_decision")
        if self._is_nutrition_log(text):
            matched.append("nutrition_log")
        elif _has_any(text, self.NUTRITION_TERMS):
            matched.append("nutrition_advice")
        if _has_any(text, self.TRAINING_LOG_TERMS):
            matched.append("training_log")
        if _has_any(text, self.RECOVERY_TERMS):
            matched.append("recovery_check")
        if _has_any(text, self.MEMORY_TERMS):
            matched.append("memory_query")
        if self._looks_like_profile_message(message):
            matched.append("profile_update")

        matched = self._dedupe(matched)
        if not matched:
            matched = ["general_chat"]

        primary = self._choose_primary(matched)
        secondary = [intent for intent in matched if intent != primary]
        entities = self._extract_entities(text)
        risk_level = self._risk_level(text, primary, secondary)
        missing_slots = self._missing_slots(primary, entities, profile)
        needs_clarification = bool(missing_slots) or (
            primary == "injury_or_risk" and any(intent in secondary for intent in {"training_plan", "progression_decision"})
        )
        allowed_actions = self._allowed_actions(primary, secondary, risk_level, needs_clarification)

        return IntentDecision(
            primary_intent=primary,
            secondary_intents=secondary,
            confidence=self._confidence(primary, secondary),
            risk_level=risk_level,
            entities=entities,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            allowed_actions=allowed_actions,
            reason=self._reason(primary, secondary, risk_level, needs_clarification),
        )

    def from_intent(self, intent: str) -> IntentDecision:
        return IntentDecision(
            primary_intent=intent,
            confidence=0.8,
            risk_level="high" if intent == "injury_or_risk" else "low",
            allowed_actions=self._allowed_actions(intent, [], "high" if intent == "injury_or_risk" else "low", False),
            reason="Intent was supplied by caller.",
        )

    def is_plan_request(self, message: str) -> bool:
        text = message.lower()
        if _has_any(text, self.NEGATED_PLAN_TERMS) and _has_any(text, ["计划", "training plan", "workout plan", "generate"]):
            return False
        return _has_any(text, self.PLAN_TERMS)

    def _choose_primary(self, intents: list[str]) -> str:
        priority = [
            "profile_correction",
            "injury_or_risk",
            "monthly_review",
            "weekly_review",
            "training_plan",
            "progression_decision",
            "nutrition_log",
            "training_log",
            "nutrition_advice",
            "recovery_check",
            "memory_query",
            "profile_update",
            "general_chat",
        ]
        for intent in priority:
            if intent in intents:
                return intent
        return intents[0]

    def _allowed_actions(
        self,
        primary: str,
        secondary: list[str],
        risk_level: str,
        needs_clarification: bool,
    ) -> dict[str, bool]:
        plan_requested = primary == "training_plan" or "training_plan" in secondary
        return {
            "generate_plan": primary == "training_plan" and risk_level != "high" and not needs_clarification,
            "allow_plan_content": primary in {
                "training_plan",
                "training_log",
                "progression_decision",
                "recovery_check",
                "weekly_review",
                "monthly_review",
            },
            "write_memory": primary not in {"general_chat", "concept_explanation", "small_talk"},
            "ask_clarifying_question": needs_clarification,
            "requested_plan_but_blocked": plan_requested and primary != "training_plan",
        }

    def _extract_entities(self, text: str) -> dict[str, Any]:
        entities: dict[str, Any] = {}
        body_parts = [name for name, terms in self.BODY_PART_TERMS.items() if _has_any(text, terms)]
        exercises = [name for name, terms in self.EXERCISE_TERMS.items() if _has_any(text, terms)]
        if body_parts:
            entities["body_parts"] = body_parts
        if exercises:
            entities["exercises"] = exercises
        weight = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|公斤)", text)
        if weight:
            entities["weight_kg"] = float(weight.group(1))
        sets = re.search(r"(\d+)\s*组", text)
        if sets:
            entities["sets"] = int(sets.group(1))
        reps = re.search(r"(\d+)\s*(?:次|reps?)", text)
        if reps:
            entities["reps"] = int(reps.group(1))
        if "今天" in text or "today" in text:
            entities["time_scope"] = "today"
        if "明天" in text or "tomorrow" in text:
            entities["time_scope"] = "tomorrow"
        if "本周" in text or "这周" in text or "weekly" in text:
            entities["time_scope"] = "this_week"
        return entities

    def _risk_level(self, text: str, primary: str, secondary: list[str]) -> str:
        if primary != "injury_or_risk" and "injury_or_risk" not in secondary:
            return "low"
        if _has_any(text, self.HARD_RISK_TERMS):
            return "high"
        return "medium"

    def _missing_slots(self, primary: str, entities: dict[str, Any], profile: Any | None) -> list[str]:
        missing: list[str] = []
        if primary == "training_plan":
            profile_missing = self._missing_profile_slots(profile)
            missing.extend(profile_missing)
        if primary == "injury_or_risk":
            if not entities.get("body_parts"):
                missing.append("symptom_body_part")
            missing.append("symptom_severity")
            missing.append("symptom_duration")
        return self._dedupe(missing)

    def _missing_profile_slots(self, profile: Any | None) -> list[str]:
        if profile is None:
            return []
        slots = ["age", "height_cm", "weight_kg", "goal", "experience_level", "equipment_available"]
        return [slot for slot in slots if not getattr(profile, slot, None)]

    def _looks_like_profile_message(self, message: str) -> bool:
        text = message.lower()
        return sum(1 for term in self.PROFILE_TERMS if term in text) >= 2

    def _is_profile_correction(self, text: str) -> bool:
        return _has_any(text, ["不是我的", "档案错", "纠正", "没有肩伤", "不是肩伤", "remove", "correction"])

    def _is_nutrition_log(self, text: str) -> bool:
        return _has_any(text, self.NUTRITION_LOG_TERMS) and _has_any(
            text,
            ["记录", "吃了", "早餐", "午餐", "晚餐", "record", "ate", "breakfast", "lunch", "dinner"],
        )

    def _confidence(self, primary: str, secondary: list[str]) -> float:
        if primary == "general_chat":
            return 0.55
        return 0.78 if secondary else 0.84

    def _reason(self, primary: str, secondary: list[str], risk_level: str, needs_clarification: bool) -> str:
        parts = [f"primary={primary}"]
        if secondary:
            parts.append("secondary=" + ",".join(secondary))
        if risk_level != "low":
            parts.append(f"risk={risk_level}")
        if needs_clarification:
            parts.append("needs_clarification=true")
        return "; ".join(parts)

    def _dedupe(self, values: list[str]) -> list[str]:
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result
