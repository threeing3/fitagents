"""Structured intent understanding for the fitness coach domain."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def _has_any(text: str, terms: list[str]) -> bool:
    for term in terms:
        if term.isascii() and term.isalpha() and len(term) <= 4 and term not in {"kg", "cm", "rpe"}:
            if re.search(rf"\b{re.escape(term)}\b", text):
                return True
            continue
        if term in text:
            return True
    return False


@dataclass
class IntentTaskStep:
    """One ordered action derived from a multi-intent decision."""

    order: int
    intent: str
    action: str
    status: str
    reason: str
    required_slots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "intent": self.intent,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "required_slots": self.required_slots,
        }


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
    task_plan: list[dict[str, Any]] = field(default_factory=list)
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
            "task_plan": self.task_plan,
            "reason": self.reason,
        }


class IntentRouter:
    """Rule-first, multi-intent classifier for the fitness coach."""

    TASK_PRIORITY = [
        "profile_correction",
        "injury_or_risk",
        "training_log",
        "nutrition_log",
        "profile_update",
        "memory_query",
        "recovery_check",
        "progression_decision",
        "nutrition_advice",
        "weekly_review",
        "monthly_review",
        "training_plan",
        "concept_explanation",
        "general_chat",
    ]

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
        if self._is_recovery_soreness(text):
            matched.append("recovery_check")
        if self._has_risk_signal(text):
            matched.append("injury_or_risk")
        if self._has_risk_signal(text, ["呼吸困难", "呼吸有点困难", "胸口闷", "手麻"]):
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
        if self._is_recovery_soreness(text) and "recovery_check" in matched:
            matched = ["recovery_check"] + [
                intent
                for intent in matched
                if intent not in {"recovery_check", "injury_or_risk"}
            ]
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
        task_plan = self._build_task_plan(primary, secondary, allowed_actions, missing_slots, risk_level)

        return IntentDecision(
            primary_intent=primary,
            secondary_intents=secondary,
            confidence=self._confidence(primary, secondary),
            risk_level=risk_level,
            entities=entities,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            allowed_actions=allowed_actions,
            task_plan=task_plan,
            reason=self._reason(primary, secondary, risk_level, needs_clarification),
        )

    def from_intent(self, intent: str) -> IntentDecision:
        risk_level = "high" if intent == "injury_or_risk" else "low"
        allowed_actions = self._allowed_actions(intent, [], risk_level, False)
        return IntentDecision(
            primary_intent=intent,
            confidence=0.8,
            risk_level=risk_level,
            allowed_actions=allowed_actions,
            task_plan=self._build_task_plan(intent, [], allowed_actions, [], risk_level),
            reason="Intent was supplied by caller.",
        )

    def is_plan_request(self, message: str) -> bool:
        text = message.lower()
        if _has_any(text, self.NEGATED_PLAN_TERMS) and _has_any(text, ["计划", "training plan", "workout plan", "generate"]):
            return False
        return _has_any(text, self.PLAN_TERMS)

    def _is_recovery_soreness(self, text: str) -> bool:
        return (
            "肌肉酸痛" in text
            and any(term in text for term in ["训练后", "练后", "缓解", "恢复", "酸痛怎么"])
        )

    def _has_risk_signal(self, text: str, terms: list[str] | None = None) -> bool:
        """Detect a positive risk mention while respecting simple negations.

        Phrases such as ``没有疼痛`` describe an absence of risk and should
        not override a progression request. The matcher remains conservative:
        it only suppresses a term when a short, explicit negation immediately
        precedes it; any separate positive symptom still wins.
        """
        terms = terms or self.RISK_TERMS
        # Remove explicitly negated symptom spans before matching. Chinese
        # terms such as ``疼痛`` contain the shorter term ``痛``; checking only
        # the immediate prefix would therefore incorrectly leave a positive
        # match behind after ``没有疼痛``.
        negated_terms = "|".join(
            re.escape(term) for term in sorted(set(terms), key=len, reverse=True)
        )
        cleaned_text = re.sub(
            rf"(?:没有|无|未|不|并不|不是|no|not|without)\s*(?:{negated_terms})",
            "",
            text,
            flags=re.IGNORECASE,
        )
        negation_pattern = re.compile(r"(?:没有|无|未|不|并不|不是|no|not|without)\s*$", re.IGNORECASE)
        for term in terms:
            if term.isascii() and term.isalpha() and len(term) <= 12:
                matches = re.finditer(rf"\b{re.escape(term)}\b", cleaned_text)
            else:
                matches = re.finditer(re.escape(term), cleaned_text)
            for match in matches:
                prefix = cleaned_text[max(0, match.start() - 8):match.start()]
                if negation_pattern.search(prefix):
                    continue
                return True
        return False

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

    def _build_task_plan(
        self,
        primary: str,
        secondary: list[str],
        allowed_actions: dict[str, bool],
        missing_slots: list[str],
        risk_level: str,
    ) -> list[dict[str, Any]]:
        intents = self._ordered_intents([primary] + secondary)
        steps: list[IntentTaskStep] = []
        for intent in intents:
            status = "ready"
            required_slots: list[str] = []
            action = self._task_action_for_intent(intent)
            reason = self._task_reason_for_intent(intent)

            if intent == "injury_or_risk":
                if missing_slots:
                    status = "needs_clarification"
                    required_slots = missing_slots
                    action = "ask_safety_clarification"
                    reason = "Safety-related requests must clarify symptoms before plan changes."
                elif risk_level in {"medium", "high"}:
                    status = "ready"
                    action = "assess_risk_boundary"
                    reason = "Risk is present, so safety boundaries must be handled before downstream tasks."

            if intent == "training_plan" and not allowed_actions.get("generate_plan", False):
                status = "blocked"
                required_slots = missing_slots
                action = "block_plan_generation"
                if risk_level in {"medium", "high"}:
                    reason = "Plan generation is blocked until safety risk is clarified."
                elif missing_slots:
                    reason = "Plan generation is blocked until required profile slots are collected."
                else:
                    reason = "Plan generation is not allowed for the current primary intent."

            steps.append(
                IntentTaskStep(
                    order=len(steps) + 1,
                    intent=intent,
                    action=action,
                    status=status,
                    reason=reason,
                    required_slots=required_slots,
                )
            )
        return [step.to_dict() for step in steps]

    def _ordered_intents(self, intents: list[str]) -> list[str]:
        deduped = self._dedupe(intents)
        priority = {intent: index for index, intent in enumerate(self.TASK_PRIORITY)}
        return sorted(deduped, key=lambda intent: priority.get(intent, len(priority)))

    def _task_action_for_intent(self, intent: str) -> str:
        return {
            "profile_correction": "apply_profile_correction",
            "injury_or_risk": "assess_risk_boundary",
            "training_log": "record_training_log",
            "nutrition_log": "record_nutrition_log",
            "profile_update": "extract_profile_update",
            "memory_query": "retrieve_memory",
            "recovery_check": "assess_recovery",
            "progression_decision": "decide_progression",
            "nutrition_advice": "answer_nutrition_advice",
            "weekly_review": "generate_weekly_review",
            "monthly_review": "generate_monthly_review",
            "training_plan": "generate_training_plan",
            "concept_explanation": "answer_concept",
            "general_chat": "answer_general",
        }.get(intent, "answer_general")

    def _task_reason_for_intent(self, intent: str) -> str:
        return {
            "profile_correction": "User correction must update the source of truth before other tasks.",
            "injury_or_risk": "Safety and medical boundaries take precedence in fitness coaching.",
            "training_log": "Training facts can be recorded as structured state.",
            "nutrition_log": "Nutrition facts can be recorded as structured state.",
            "profile_update": "Profile facts should be extracted before personalization.",
            "memory_query": "Relevant long-term memory should be retrieved before answering.",
            "recovery_check": "Recovery state influences training recommendations.",
            "progression_decision": "Progression decisions depend on current state and risk.",
            "nutrition_advice": "Nutrition advice can be answered after safety and profile checks.",
            "weekly_review": "Weekly review summarizes recent logs and outcomes.",
            "monthly_review": "Monthly review summarizes longer-term trends.",
            "training_plan": "Training plan generation is allowed only after gates pass.",
            "concept_explanation": "Concept questions can be answered directly.",
            "general_chat": "General chat does not require tool execution.",
        }.get(intent, "Default answer task.")

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
        if self._has_risk_signal(text, self.HARD_RISK_TERMS):
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
