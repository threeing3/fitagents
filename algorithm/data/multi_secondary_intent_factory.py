"""Independent synthetic examples with two secondary intents per request."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .schemas import TrainingExample, stable_hash


@dataclass(frozen=True)
class MultiSecondarySeed:
    name: str
    primary: str
    secondary: tuple[str, str]
    train_messages: tuple[str, ...]
    validation_messages: tuple[str, ...]
    risk: str = "low"


MULTI_SECONDARY_SEEDS = (
    MultiSecondarySeed(
        "risk_plan_log",
        "injury_or_risk",
        ("training_plan", "training_log"),
        (
            "跑步时膝盖突然疼，先记下今天的五公里，再帮我调整后面的跑步计划",
            "卧推肩膀刺痛，今天这次训练要记录，下一次推举也需要重新安排",
            "深蹲时腰部不舒服，把本次训练记下来，并修改本周剩余计划",
            "引体向上时手臂发麻，记录刚完成的组数，再判断后续训练怎么改",
            "晨跑出现胸闷，先登记本次跑步，原定的间歇训练也需要调整",
            "硬拉后腿麻没力，记录今天的重量，同时重新安排下一次训练",
            "练腿时脚踝肿痛，把已经完成的动作记上，后续计划需要降低负荷",
            "推举时肩部锐痛，保存今天的训练记录，并重排这周的上肢训练",
        ),
        (
            "划船时背部突然疼，记录今天训练，并调整接下来的背部安排",
            "冲刺后头晕胸口不适，把这次训练登记下来，后面的计划也先改掉",
            "弓步时膝盖疼，记下已经完成的训练，再重新安排本周下肢内容",
            "双杠臂屈伸时肩膀痛，记录本次表现，同时修改下一次训练",
        ),
        "high",
    ),
    MultiSecondarySeed(
        "recovery_plan_nutrition",
        "recovery_check",
        ("training_plan", "nutrition_advice"),
        (
            "昨晚只睡五小时，帮我判断今天怎么练，也说说训练后怎么吃",
            "这几天静息心率偏高，调整一下本周训练，再给恢复期饮食建议",
            "腿部酸痛一直没退，评估恢复后安排今天训练，并建议一顿练后餐",
            "连续加班后很疲劳，今天训练需要减量，饮食上也帮我做个调整",
            "早上起来全身没劲，先看恢复状态，再决定训练内容和补充营养",
            "最近睡眠断断续续，重新安排这两天的训练，并说明该怎么吃更利于恢复",
            "训练后酸痛比平时重，判断是否该休息，同时调整计划和蛋白质摄入",
            "今天静息心率高了不少，帮我改训练强度，也给当天饮食一个建议",
        ),
        (
            "昨晚睡得很差，今天应该怎么训练，吃什么更适合恢复",
            "最近疲劳积累明显，帮我调整本周计划并给恢复饮食建议",
            "早晨心率偏高，先评估恢复，再安排训练和练后饮食",
            "腿还很酸，今天是否减量，饮食方面也一起调整一下",
        ),
        "medium",
    ),
    MultiSecondarySeed(
        "plan_profile_nutrition",
        "training_plan",
        ("profile_update", "nutrition_advice"),
        (
            "我现在每周能练四天，更新资料后做一份增肌计划，再安排练后饮食",
            "以后只能在家训练，把器械改成哑铃，重排计划并给饮食建议",
            "我的目标改成减脂，请更新目标，调整训练安排并说明晚餐怎么吃",
            "接下来每次只能练四十分钟，记到资料里，再改计划和训练后加餐",
            "我新办了健身房会员，更新器械条件，安排力量计划并搭配饮食",
            "最近每周只能练两天，先更新可训练天数，再改计划和蛋白质分配",
            "我的训练经验改为中级，按新水平排课，并建议训练日怎么吃",
            "目标从增肌换成提升力量，更新资料后重做计划，也给恢复餐建议",
        ),
        (
            "我现在一周能练三天，更新资料，重新排计划并给练后饮食建议",
            "器械条件改成徒手，请同步资料，再调整训练和当天饮食",
            "目标改为减脂，更新后安排本周训练，也说说晚饭怎么搭配",
            "单次训练时间只有半小时，记下这个变化，再改计划和补充建议",
        ),
    ),
    MultiSecondarySeed(
        "plan_correction_recovery",
        "training_plan",
        ("profile_correction", "recovery_check"),
        (
            "之前把膝伤写成左侧了，其实是右膝，纠正资料后按最近疲劳调整计划",
            "我上次说每周练五天是错的，应为三天，改正后结合睡眠重新排计划",
            "器械记录里写了杠铃，但我只有哑铃，纠正信息并按当前恢复状态改计划",
            "体重不是八十公斤而是七十二，修正资料后根据最近酸痛安排训练",
            "旧记录说我没有伤病，但肩部仍在恢复，先纠正再制定保守计划",
            "训练经验误填成高级，实际是新手，改正后结合疲劳程度重新安排",
            "目标记录成减脂是错误的，我现在想增肌，修正目标并考虑睡眠不足调整训练",
            "资料里的可训练天数写错了，正确是四天，纠正后按恢复情况排课",
        ),
        (
            "之前把伤腿记反了，请纠正资料，再结合疲劳状态调整训练",
            "每周训练天数填错了，应为三天，修正后重新评估恢复并排计划",
            "器械信息不对，我只有哑铃，改正后按当前酸痛情况安排训练",
            "训练水平不是高级而是初级，纠正资料，再结合睡眠调整本周计划",
        ),
    ),
    MultiSecondarySeed(
        "weekly_progress_log",
        "weekly_review",
        ("progression_decision", "training_log"),
        (
            "总结这周表现，把今天的深蹲记录进去，再判断下周是否加重量",
            "做一次周复盘，补记昨天的卧推训练，并决定下一次能不能进阶",
            "看看最近七天完成情况，登记今天跑步成绩，再判断是否提高配速",
            "复盘本周训练，补上周三硬拉的记录，并判断下周要不要加重",
            "整理这周的数据，把刚练完的引体向上记下，再决定是否增加次数",
            "总结过去一周，记录今天的训练完成度，并判断下一周期如何进阶",
            "回顾这周力量表现，补记最后一次卧推，再判断是否增加训练量",
            "做周总结，把今天五公里成绩登记上，并判断下周能否提高强度",
        ),
        (
            "复盘这周，把今天深蹲数据记上，再判断下周是否加重",
            "总结最近七天，补记昨天跑步，并决定下一次是否提高配速",
            "做本周训练回顾，登记今天卧推，再判断能否增加重量",
            "看一下这周完成情况，记录最后一次训练，并给出进阶判断",
        ),
    ),
    MultiSecondarySeed(
        "log_monthly_progress",
        "training_log",
        ("monthly_review", "progression_decision"),
        (
            "记录今天卧推五组，放进本月复盘里，再判断下个月是否加重量",
            "把这次十公里跑登记上，结合月度表现判断下阶段能否提速",
            "记下今天的深蹲重量，纳入本月总结，并判断下周期怎么进阶",
            "登记今晚的上肢训练，做月度回顾后决定是否增加训练量",
            "记录本次硬拉成绩，比较本月变化，再判断下个月要不要加重",
            "把今天的训练完成度记下来，加入月度复盘并给出进阶建议",
            "登记今天的跑步配速，回顾本月趋势，再判断是否增加里程",
            "记录刚完成的引体向上次数，纳入本月总结并判断能否升级难度",
        ),
        (
            "记下今天卧推，加入本月复盘，再判断下个月是否加重",
            "登记这次跑步，比较本月表现并决定下阶段是否提速",
            "记录今天深蹲，做月度总结后判断下一周期如何进阶",
            "把本次训练记上，纳入月度回顾并给出进阶判断",
        ),
    ),
)


def _decision(seed: MultiSecondarySeed) -> str:
    return json.dumps(
        {
            "primary_intent": seed.primary,
            "secondary_intents": list(seed.secondary),
            "risk_level": seed.risk,
            "needs_clarification": seed.primary == "injury_or_risk",
            "reason_codes": ["ontology_v3", "multi_secondary_composition"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_multi_secondary_examples() -> list[TrainingExample]:
    rows: list[TrainingExample] = []
    for seed in MULTI_SECONDARY_SEEDS:
        for split, messages in (
            ("train", seed.train_messages),
            ("validation", seed.validation_messages),
        ):
            family = f"multi_secondary_{seed.name}_{split}"
            for index, message in enumerate(messages):
                identity = f"{family}:{index}"
                rows.append(
                    TrainingExample(
                        example_id=f"intent-multilabel-v3-1-{family}-{index:02d}",
                        task_type="intent_decision_v2",
                        user_message=message,
                        user_hash=stable_hash(identity, "intent-multilabel-v3-1-user"),
                        session_hash=stable_hash(identity, "intent-multilabel-v3-1-session"),
                        assistant_response=_decision(seed),
                        intent_label=seed.primary,
                        risk_label=seed.risk,
                        quality_labels={
                            "weakness": "secondary_cardinality",
                            "review_status": "assistant_authored_synthetic",
                        },
                        label_source="assistant_authored_synthetic",
                        template_family=family,
                        human_review_status="not_reviewed",
                        training_eligible=True,
                        model_version="none",
                        prompt_version="intent-multilabel-v3.1",
                        rule_version="intent-ontology-v3",
                        source="synthetic",
                        split=split,
                        created_at="2026-09-22T00:00:00+08:00",
                    )
                )
    return rows
