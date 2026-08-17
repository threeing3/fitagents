"""Deterministic intent-training samples with explicit provenance and split isolation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .schemas import TrainingExample, stable_hash


@dataclass(frozen=True)
class IntentFamily:
    name: str
    weakness: str
    split: str
    messages: tuple[str, ...]
    primary_intent: str
    secondary_intents: tuple[str, ...] = ()
    risk_level: str = "low"
    needs_clarification: bool = False
    context: str = ""


_SAFE_ACTIONS = ("我该怎么处理", "今天还能练吗", "请先帮我判断", "下一步怎么安排", "需要调整计划吗")
_PLAN_GOALS = ("增肌", "减脂", "提升力量", "恢复体能", "准备一次五公里跑")


def _families() -> tuple[IntentFamily, ...]:
    # Families, rather than individual paraphrases, are assigned to a split.
    # This prevents near-duplicate wording from inflating held-out metrics.
    return (
        IntentFamily(
            "risk_sharp_joint",
            "risk_level",
            "train",
            ("深蹲时膝盖突然刺痛", "卧推时肩膀出现锐痛"),
            "injury_or_risk",
            risk_level="high",
        ),
        IntentFamily(
            "risk_neuro",
            "risk_level",
            "train",
            ("硬拉后腿麻而且没力", "训练后手臂发麻握不住东西"),
            "injury_or_risk",
            risk_level="critical",
        ),
        IntentFamily(
            "risk_chest",
            "risk_level",
            "train",
            ("跑步时胸口压迫并且喘不上气", "冲刺后胸痛还头晕"),
            "injury_or_risk",
            risk_level="critical",
        ),
        IntentFamily(
            "risk_persistent",
            "risk_level",
            "train",
            ("腰痛已经持续一周还在加重", "脚踝肿痛三天没有缓解"),
            "injury_or_risk",
            risk_level="high",
        ),
        IntentFamily(
            "risk_uncertain",
            "risk_level",
            "test",
            ("运动后出现异常疼痛但我说不清位置", "练完以后身体很不对劲却无法描述"),
            "injury_or_risk",
            risk_level="high",
            needs_clarification=True,
        ),
        IntentFamily(
            "clarify_plan_days",
            "clarification",
            "train",
            ("给我重新做一个训练计划", "我想换一套训练安排"),
            "training_plan",
            needs_clarification=True,
        ),
        IntentFamily(
            "clarify_nutrition_amount",
            "clarification",
            "train",
            ("训练后应该吃多少", "帮我安排减脂饮食"),
            "nutrition_advice",
            needs_clarification=True,
        ),
        IntentFamily(
            "clarify_pain_location",
            "clarification",
            "train",
            ("练完以后有点疼", "这个动作让我不舒服"),
            "injury_or_risk",
            risk_level="medium",
            needs_clarification=True,
        ),
        IntentFamily(
            "clarify_exercise_equipment",
            "clarification",
            "train",
            ("推荐几个适合我的动作", "今天在家练什么"),
            "exercise_selection",
            needs_clarification=True,
        ),
        IntentFamily(
            "clarify_load",
            "clarification",
            "validation",
            ("下次训练要不要加重量", "今天的强度怎么定"),
            "training_plan",
            needs_clarification=True,
        ),
        IntentFamily(
            "multi_risk_plan",
            "multi_intent_priority",
            "train",
            ("膝盖疼但我还想增加训练天数", "肩膀痛，同时帮我调整下周计划"),
            "injury_or_risk",
            ("training_plan",),
            "high",
            True,
        ),
        IntentFamily(
            "multi_recovery_plan",
            "multi_intent_priority",
            "train",
            ("昨晚没睡好，顺便安排今天训练", "今天很疲劳但还想练腿"),
            "recovery_check",
            ("training_plan",),
            "medium",
            False,
        ),
        IntentFamily(
            "multi_plan_nutrition",
            "multi_intent_priority",
            "train",
            ("给我做增肌计划并安排训练后饮食", "想减脂，请同时调整训练和饮食"),
            "training_plan",
            ("nutrition_advice",),
        ),
        IntentFamily(
            "multi_log_recovery",
            "multi_intent_priority",
            "train",
            ("记录今天的训练，再看看恢复情况", "我完成计划了，也想评估疲劳"),
            "workout_logging",
            ("recovery_check",),
        ),
        IntentFamily(
            "multi_ambiguous",
            "multi_intent_priority",
            "validation",
            ("训练、吃饭和恢复都帮我看看", "计划和饮食我都想改"),
            "training_plan",
            ("nutrition_advice", "recovery_check"),
            needs_clarification=True,
        ),
        IntentFamily(
            "memory_stale_goal",
            "memory_conflict",
            "train",
            ("按我现在的减脂目标调整计划", "继续围绕我现在的目标安排"),
            "training_plan",
            context="历史记忆写着增肌；本轮用户明确说明当前目标是减脂。",
        ),
        IntentFamily(
            "memory_stale_injury",
            "memory_conflict",
            "train",
            ("之前肩膀疼，现在已经康复，推荐动作", "旧记录里有膝伤，但医生已允许逐步恢复"),
            "exercise_selection",
            context="长期记忆包含旧伤；本轮提供了更新状态，但仍需保守渐进。",
        ),
        IntentFamily(
            "memory_wrong_equipment",
            "memory_conflict",
            "train",
            ("我现在只有哑铃，重新选动作", "健身房关了，按徒手条件安排"),
            "exercise_selection",
            context="历史记忆中的器械条件已经过期，以本轮条件为准。",
        ),
        IntentFamily(
            "memory_identity_conflict",
            "memory_conflict",
            "train",
            ("那条马拉松记录不是我的，请按新手处理", "历史里的高阶训练数据录错人了"),
            "training_plan",
            context="用户否认历史记忆归属，必须忽略冲突记忆并澄清。",
            needs_clarification=True,
        ),
        IntentFamily(
            "memory_unclear_time",
            "memory_conflict",
            "test",
            ("用我最近的状态安排今天训练", "根据之前说的情况给建议"),
            "training_plan",
            context="存在多条时间冲突的恢复记录，无法确认哪条最新。",
            needs_clarification=True,
        ),
    )


def build_intent_examples(per_family: int = 50) -> list[TrainingExample]:
    """Build reproducible rule-labelled rows; no teacher or human claim is made."""

    if per_family <= 0:
        raise ValueError("per_family must be positive")
    rows: list[TrainingExample] = []
    for family_index, family in enumerate(_families()):
        for variant in range(per_family):
            base = family.messages[variant % len(family.messages)]
            action = _SAFE_ACTIONS[(variant // len(family.messages)) % len(_SAFE_ACTIONS)]
            goal = _PLAN_GOALS[
                (variant // (len(family.messages) * len(_SAFE_ACTIONS))) % len(_PLAN_GOALS)
            ]
            user_message = f"{base}，{action}？我目前主要想{goal}。"
            decision = {
                "primary_intent": family.primary_intent,
                "secondary_intents": list(family.secondary_intents),
                "risk_level": family.risk_level,
                "needs_clarification": family.needs_clarification,
                "reason_codes": [family.weakness, "rule_labeled"],
            }
            user_key = f"intent-{family.name}-{variant // 5:02d}"
            rows.append(
                TrainingExample(
                    example_id=f"intent-{family.name}-{variant:04d}",
                    user_hash=stable_hash(user_key, "intent-v1"),
                    session_hash=stable_hash(f"{user_key}-{variant}", "intent-v1"),
                    task_type="intent_decision_v2",
                    user_message=user_message,
                    retrieved_context={"memory_summary": family.context} if family.context else {},
                    assistant_response=json.dumps(decision, ensure_ascii=False, sort_keys=True),
                    intent_label=family.primary_intent,
                    risk_label=family.risk_level,
                    quality_labels={"weakness": family.weakness, "review_status": "not_reviewed"},
                    label_source="deterministic_rule_template",
                    template_family=family.name,
                    human_review_status="not_reviewed",
                    training_eligible=family.split in {"train", "validation"},
                    exclusion_reason="held_out_intent_test" if family.split == "test" else None,
                    model_version="none",
                    prompt_version="intent-template-v1",
                    rule_version="intent-label-rules-v1",
                    source="rule_generated",
                    split=family.split,
                    created_at=f"2026-08-17T{family_index % 24:02d}:{variant % 60:02d}:00+08:00",
                )
            )
    return rows
