"""Balanced multi-intent data with an isolated threshold-calibration split."""

from __future__ import annotations

import json
from dataclasses import dataclass

from algorithm.inference.intent_catalog import AgentIntentCatalog

from .schemas import TrainingExample, stable_hash


@dataclass(frozen=True)
class IntentSeed:
    intent: str
    train_messages: tuple[str, str]
    calibration_message: str


INTENT_SEEDS = (
    IntentSeed(
        "general_chat", ("最近训练状态还不错，想随便聊聊", "陪我聊聊健身习惯"), "今天想聊聊运动"
    ),
    IntentSeed("concept_explanation", ("解释一下渐进超负荷", "什么是训练容量"), "RPE 是什么意思"),
    IntentSeed("small_talk", ("你好，今天过得怎么样", "早上好，教练"), "晚上好"),
    IntentSeed(
        "onboarding", ("我是新用户，带我开始设置", "第一次使用，先了解我的情况"), "帮我完成初始设置"
    ),
    IntentSeed(
        "profile_update", ("把我的目标改成减脂", "我现在每周能练四天"), "更新资料：我只有哑铃"
    ),
    IntentSeed(
        "profile_correction",
        ("之前记录的身高错了", "那条伤病信息不是我的"),
        "纠正资料：体重应为七十公斤",
    ),
    IntentSeed("training_plan", ("给我安排下周训练", "做一份三天力量计划"), "帮我调整本周计划"),
    IntentSeed(
        "training_log", ("记录今天完成了深蹲", "把刚才的五公里跑记下来"), "登记今天卧推五组"
    ),
    IntentSeed(
        "progression_decision",
        ("下次深蹲是否该加重量", "我连续完成目标次数该怎么进阶"),
        "判断卧推能不能加重",
    ),
    IntentSeed(
        "nutrition_advice", ("减脂期晚餐怎么安排", "训练后应该吃什么"), "增肌期蛋白质怎么分配"
    ),
    IntentSeed(
        "nutrition_log", ("记录午餐吃了鸡胸和米饭", "把这杯牛奶记入饮食"), "登记早餐两个鸡蛋"
    ),
    IntentSeed(
        "recovery_check",
        ("昨晚只睡五小时今天怎么练", "腿很酸帮我评估恢复"),
        "静息心率偏高是否应该减量",
    ),
    IntentSeed("injury_or_risk", ("深蹲时膝盖突然刺痛", "跑步时胸闷而且头晕"), "硬拉后腿麻无力"),
    IntentSeed(
        "weekly_review", ("总结我这周的训练表现", "做一次本周训练复盘"), "看看最近七天完成得怎样"
    ),
    IntentSeed(
        "monthly_review", ("总结我这个月的进展", "做一次月度训练复盘"), "比较本月和上月表现"
    ),
    IntentSeed(
        "memory_query", ("你记得我的训练目标吗", "查看你保存的器械条件"), "你记得我之前的伤病吗"
    ),
)

SECONDARY_COMBINATIONS = (
    ("injury_or_risk", "training_plan", "肩膀抬起会痛，同时调整推举计划"),
    ("recovery_check", "training_log", "昨晚没睡好，并记录早上的跑步"),
    ("training_plan", "nutrition_advice", "安排增肌训练，同时给训练后饮食建议"),
    ("training_plan", "profile_update", "按四天训练重新排计划，并更新每周可训练天数"),
    ("weekly_review", "progression_decision", "复盘这周表现，并判断深蹲是否加重"),
    ("nutrition_advice", "recovery_check", "安排训练后饮食，并结合疲劳判断恢复情况"),
    ("training_plan", "profile_correction", "纠正器械信息，再按哑铃条件改计划"),
    ("training_log", "monthly_review", "登记今天训练，并纳入本月复盘"),
)


def _decision(primary: str, secondary: tuple[str, ...], risk: str = "low") -> str:
    return json.dumps(
        {
            "primary_intent": primary,
            "secondary_intents": list(secondary),
            "risk_level": risk,
            "needs_clarification": False,
            "reason_codes": ["ontology_v2", "rule_labeled"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _example(
    *, split: str, family: str, index: int, message: str, primary: str, secondary: tuple[str, ...]
) -> TrainingExample:
    risk = "high" if primary == "injury_or_risk" else "low"
    user_key = f"multilabel-v2-{split}-{family}-{index // 3}"
    return TrainingExample(
        example_id=f"multilabel-v2-{split}-{family}-{index:03d}",
        user_hash=stable_hash(user_key, "intent-multilabel-v2"),
        session_hash=stable_hash(f"{user_key}-{index}", "intent-multilabel-v2"),
        task_type="intent_decision_v2",
        user_message=message,
        assistant_response=_decision(primary, secondary, risk),
        intent_label=primary,
        risk_label=risk,
        quality_labels={"weakness": "multilabel_coverage", "review_status": "not_reviewed"},
        label_source="deterministic_rule_template",
        template_family=family,
        human_review_status="not_reviewed",
        training_eligible=True,
        model_version="none",
        prompt_version="intent-multilabel-template-v2",
        rule_version="intent-ontology-v2",
        source="rule_generated",
        split=split,
        created_at=f"2026-08-30T{index % 24:02d}:{index % 60:02d}:00+08:00",
    )


def build_multilabel_intent_examples(
    *, train_per_family: int = 12, calibration_per_family: int = 5
) -> list[TrainingExample]:
    """Build disjoint train/calibration families without using development data."""

    if train_per_family <= 0 or calibration_per_family <= 0:
        raise ValueError("family sizes must be positive")
    if {seed.intent for seed in INTENT_SEEDS} != AgentIntentCatalog.VALID_INTENTS:
        raise RuntimeError("intent seeds must exactly match the runtime catalog")

    rows: list[TrainingExample] = []
    suffixes = ("请直接判断", "以当前信息为准", "不要补充未提供的信息", "先识别我的需求")
    for seed in INTENT_SEEDS:
        for family_index, base in enumerate(seed.train_messages):
            family = f"primary_{seed.intent}_train_{family_index}"
            for index in range(train_per_family):
                message = f"{base}，{suffixes[index % len(suffixes)]}。"
                rows.append(
                    _example(
                        split="train",
                        family=family,
                        index=index,
                        message=message,
                        primary=seed.intent,
                        secondary=(),
                    )
                )
        family = f"primary_{seed.intent}_calibration"
        for index in range(calibration_per_family):
            rows.append(
                _example(
                    split="validation",
                    family=family,
                    index=index,
                    message=f"{seed.calibration_message}，{suffixes[index % len(suffixes)]}。",
                    primary=seed.intent,
                    secondary=(),
                )
            )

    for primary, secondary, base in SECONDARY_COMBINATIONS:
        for family_index in range(2):
            family = f"multi_{primary}_{secondary}_train_{family_index}"
            for index in range(train_per_family):
                rows.append(
                    _example(
                        split="train",
                        family=family,
                        index=index,
                        message=f"{base}，{suffixes[(index + family_index) % len(suffixes)]}。",
                        primary=primary,
                        secondary=(secondary,),
                    )
                )
        family = f"multi_{primary}_{secondary}_calibration"
        for index in range(calibration_per_family):
            rows.append(
                _example(
                    split="validation",
                    family=family,
                    index=index,
                    message=f"换个场景：{base}，{suffixes[index % len(suffixes)]}。",
                    primary=primary,
                    secondary=(secondary,),
                )
            )
    return rows
