"""Deterministic, explicitly labelled synthetic data for offline experiments.

Synthetic rows are useful for exercising the data pipeline before enough real
feedback exists.  They deliberately carry ``source=synthetic`` and simulated
outcome provenance so reports cannot mistake them for production evidence.
"""

from __future__ import annotations

from typing import Any

from .schemas import OutcomeLabel, PreferencePair, TrainingExample, stable_hash
from .split_dataset import split_records

TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "task_type": "general_chat",
        "intent": "general_chat",
        "message": "今天状态还可以，帮我安排一个轻量训练。",
        "response": "先做 5 分钟热身，再进行 3 组自重训练；如果出现疼痛，立即停止并调整。",
        "tools": ["context.build", "knowledge.retrieve"],
        "quality": 0.82,
    },
    {
        "task_type": "training_plan",
        "intent": "training_plan",
        "message": "我每周训练三次，想做一个循序渐进的计划。",
        "response": "建议每周安排两次力量和一次低强度有氧，先保留 2 次余力，再根据完成情况逐步增加负荷。",
        "tools": ["context.build", "plan.generate", "plan.validate"],
        "quality": 0.9,
    },
    {
        "task_type": "recovery_check",
        "intent": "recovery_check",
        "message": "昨晚只睡了五个小时，今天还要继续训练吗？",
        "response": "今天优先恢复；可以改为低强度活动并缩短训练时间，等睡眠和主观疲劳改善后再恢复负荷。",
        "tools": ["context.build", "recovery.evaluate"],
        "quality": 0.91,
    },
    {
        "task_type": "nutrition_advice",
        "intent": "nutrition_advice",
        "message": "训练后我应该怎么安排一餐？",
        "response": "训练后可优先补充水分、蛋白质和适量碳水；具体份量结合你的目标、体重和全天饮食调整。",
        "tools": ["context.build", "nutrition.estimate"],
        "quality": 0.84,
    },
    {
        "task_type": "exercise_selection",
        "intent": "exercise_selection",
        "message": "只有弹力带，如何练下肢？",
        "response": "可以选择弹力带深蹲、髋推和侧向走，每个动作先做 2–3 组，保持动作稳定并逐步增加次数。",
        "tools": ["context.build", "exercise.select"],
        "quality": 0.86,
    },
    {
        "task_type": "injury_or_risk",
        "intent": "injury_or_risk",
        "message": "动作时出现锐痛，我还可以坚持完成吗？",
        "response": "不要带着锐痛继续训练；请停止相关动作，记录触发动作，必要时咨询医生或康复专业人士。",
        "tools": ["context.build", "safety.check", "recovery.evaluate"],
        "quality": 0.96,
    },
)


def build_synthetic_examples(count: int = 24, seed: int = 42) -> list[TrainingExample]:
    """Create reproducible training examples without claiming real outcomes."""

    if count < 0:
        raise ValueError("count must be non-negative")
    # The seed is part of the experiment contract even though the current
    # templates are deterministic; future template sampling can use it.
    offset = int(seed) % len(TEMPLATES) if TEMPLATES else 0
    records: list[TrainingExample] = []
    for index in range(count):
        spec = TEMPLATES[(index + offset) % len(TEMPLATES)]
        sample_tag = f"（模拟案例 {index + 1}）"
        # Simulated acceptance has a learnable relationship to pre-response
        # quality and tool complexity, plus a small deterministic noise term.
        # It is deliberately not a claim about real user behaviour.
        signal = float(spec["quality"]) + (0.02 if len(spec["tools"]) <= 2 else -0.01)
        accepted = signal >= 0.86
        if (index * 17 + seed) % 19 in {0, 1}:
            accepted = not accepted
        implemented = accepted and ((index * 13 + seed) % 10) < 8
        adherence = round(0.35 + (((index * 19 + seed) % 60) / 100), 2)
        # Group a complete template cycle under one user so every user can
        # contribute multiple scenarios without crossing dataset partitions.
        user_count = max(1, min(max(1, count // len(TEMPLATES)), 50))
        user_key = f"synthetic-user-{(index // len(TEMPLATES)) % user_count}"
        records.append(
            TrainingExample(
                example_id=f"synthetic-{index:05d}",
                user_hash=stable_hash(user_key, "synthetic-v1"),
                session_hash=stable_hash(f"{user_key}-session-{index}", "synthetic-v1"),
                task_type=str(spec["task_type"]),
                user_message=f"{spec['message']}{sample_tag}",
                profile_context={"goal": "general_fitness", "synthetic_profile": True},
                retrieved_context={"source_terms": [str(spec["intent"]), "safety_boundary"]},
                tool_trace=[{"tool_name": name, "status": "success"} for name in spec["tools"]],
                assistant_response=f"{spec['response']} 本案例仅用于离线方法验证。",
                intent_label=str(spec["intent"]),
                risk_label="high" if spec["intent"] == "injury_or_risk" else "low",
                quality_labels={
                    "overall_score": float(spec["quality"]),
                    "label_source": "synthetic_template",
                },
                guardrail_result={"synthetic_check": "passed"},
                outcome=OutcomeLabel(
                    accepted_by_user=accepted,
                    implementation_status="implemented" if implemented else "not_implemented",
                    adherence_7d=adherence,
                    negative_feedback=not accepted,
                    safety_status="safe",
                    outcome_status="simulated",
                    label_confidence=0.65,
                    label_source="simulated_outcome",
                ),
                model_version="synthetic-teacher-v1",
                prompt_version="synthetic-prompt-v1",
                rule_version="synthetic-rules-v1",
                source="synthetic",
                split="quarantine",
                created_at=f"2026-08-01T00:{index % 60:02d}:00+00:00",
            )
        )
    split, _ = split_records([record.to_dict() for record in records])
    return [TrainingExample.from_dict(row) for row in split]


def build_synthetic_preference_pairs(
    rows: list[TrainingExample | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build transparent synthetic preferences from template quality labels."""

    pairs: list[dict[str, Any]] = []
    for raw in rows:
        row = raw.to_dict() if isinstance(raw, TrainingExample) else dict(raw)
        response = str(row.get("assistant_response") or "").strip()
        if not response or row.get("source") != "synthetic":
            continue
        pair = PreferencePair(
            example_id=f"{row.get('example_id', 'synthetic')}-preference",
            prompt=str(row.get("user_message") or ""),
            chosen=response,
            rejected="请坚持训练，注意安全。",
            preference_reason=["specificity", "actionability", "safety_boundary"],
            feedback_source="synthetic_pair",
            guardrail_comparison={"chosen": "safe", "rejected": "underspecified"},
            business_outcome_comparison={
                "chosen": "simulated_higher_acceptance",
                "rejected": "simulated_lower_acceptance",
            },
            source="synthetic",
            split=str(row.get("split") or "quarantine"),
        )
        if not pair.validate():
            pairs.append(pair.to_dict())
    return pairs
