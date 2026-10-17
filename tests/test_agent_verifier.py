import pytest

from fast_api.app.services.agent_verifier import AgentVerifier
from fast_api.app.services.strategy_memory_policy import build_strategy_memory_response_note


def test_plan_verifier_accepts_repairable_warnings_and_repair_adds_safety_fields():
    verifier = AgentVerifier()
    plan = {
        "goal": "fat_loss",
        "training_days": [
            {
                "day": 1,
                "name": "Full Body",
                "exercises": [{"name": "squat", "sets": 3, "reps": "8-10"}],
            }
        ],
        "nutrition": {"target_calories": None},
    }
    profile = {
        "workout_frequency": 1,
        "target_calories": 2100,
        "target_protein_g": 160,
        "target_carbs_g": 200,
        "target_fat_g": 70,
    }
    context = {"relevant_memories": [{"memory_type": "medical_context", "summary": "甲亢用药背景"}]}

    result = verifier.verify_plan(plan, profile, context)

    assert result.passed is True
    assert "missing_pain_stop_note" in result.repair_actions
    assert "missing_target_calories" in result.repair_actions
    assert "missing_medical_boundary_note" in result.repair_actions

    repaired = verifier.repair_plan(plan, result.to_dict(), profile, context)
    assert repaired["review_cadence"] == "weekly"
    assert repaired["nutrition"]["target_calories"] == 2100
    assert "safety_boundary" in repaired
    assert "pain" in repaired["training_days"][0]["exercises"][0]["notes"].lower()


def test_plan_verifier_fails_when_training_days_are_missing():
    result = AgentVerifier().verify_plan({"goal": "fat_loss"}, {}, {})

    assert result.passed is False
    assert result.issues[0].issue_id == "missing_training_days"


def test_response_verifier_detects_old_plan_carryover_and_repairs():
    verifier = AgentVerifier()
    context = {
        "current_request_policy": {
            "current_intent": "general_chat",
            "should_generate_plan": False,
            "allow_plan_content": False,
        }
    }
    response = "今天训练计划：卧推 3 组，划船 3 组，深蹲 3 组。"

    result = verifier.verify_response(response, "你觉得蛋白粉有必要吗？", context)
    repair = verifier.repair_response(result.to_dict(), context)

    assert result.passed is False
    assert "old_plan_carryover" in result.repair_actions
    assert repair["repaired"] is False
    assert repair["repair_text"] == ""
    assert repair["unresolved_issues"] == ["old_plan_carryover"]


@pytest.mark.parametrize(
    "response",
    [
        "好的，我按这个理解记录：\n- 当前状态：标准自重正手引体向上，只能拉1–2个\n"
        "- 测试时间：前天\n- 停练前水平：约4组5次\n"
        "先不调整现有计划。等你恢复训练1–2次后，再根据实际表现判断。",
        "你之前卧推3组，每组8次，目前只是记录，不自动追加训练计划。",
        "先不调整训练计划：等你补充近期表现再讨论。",
        "Previously I did squats for 3 sets of 8 reps. This is a historical record.",
        "训练计划需要考虑恢复情况，组数和次数不是越多越好。",
    ],
)
def test_response_verifier_does_not_treat_history_or_plan_mentions_as_prescriptions(response):
    verifier = AgentVerifier()
    context = {"current_request_policy": {"allow_plan_content": False}}
    result = verifier.verify_response(response, "记录一下停练前的水平", context)
    assert "old_plan_carryover" not in result.repair_actions
    assert verifier.repair_response(result.to_dict(), context)["repair_text"] == ""


@pytest.mark.parametrize(
    "response",
    [
        "今天训练计划：卧推3组，深蹲3组。",
        "建议做3组引体向上，每组5次，组间休息两分钟。",
        "以前卧推3组。接下来建议深蹲4组，每组8次。",
        "之前深蹲3组，但明天建议做4组。",
        "Here is today's workout plan: squat 3 sets of 8 reps.",
        "Previously I did 3 sets. Tomorrow perform 4 sets of squats.",
    ],
)
def test_response_verifier_still_detects_new_unsolicited_prescriptions(response):
    result = AgentVerifier().verify_response(
        response, "只记录，不制定计划", {"current_request_policy": {"allow_plan_content": False}}
    )
    assert "old_plan_carryover" in result.repair_actions


def test_response_verifier_blocks_plan_before_required_clarification():
    verifier = AgentVerifier()
    context = {
        "current_request_policy": {
            "current_intent": "training_plan",
            "should_generate_plan": False,
            "allow_plan_content": True,
            "needs_clarification": True,
            "missing_slots": ["age", "equipment_available"],
            "risk_level": "low",
        }
    }
    response = "Here is today's workout plan: squat 3 sets of 8 reps, bench press 3 sets of 8 reps."

    result = verifier.verify_response(response, "What should I train today?", context)
    repair = verifier.repair_response(result.to_dict(), context)

    assert result.passed is False
    assert "plan_generated_before_clarification" in result.repair_actions
    assert repair["repaired"] is True
    assert "equipment_available" in repair["repair_text"]


def test_response_verifier_adds_medical_boundary_when_needed():
    context = {
        "current_request_policy": {
            "current_intent": "training_plan",
            "should_generate_plan": True,
            "allow_plan_content": True,
        },
        "relevant_memories": [
            {"memory_type": "medical_context", "summary": "甲状腺异常，正在用药"}
        ],
    }
    response = "今天做 RPE 8 的力量训练，控制心率。"

    result = AgentVerifier().verify_response(response, "今天练什么？", context)

    assert result.passed is True
    assert "missing_medical_boundary_in_response" in result.repair_actions


def test_response_verifier_repairs_missing_strategy_memory_guidance():
    context = {
        "current_request_policy": {
            "current_intent": "training_plan",
            "should_generate_plan": True,
            "allow_plan_content": True,
        },
        "active_risk_notes": [{"risk_type": "thyroid"}],
        "knowledge_context": {"decision_rules": [{"rule_id": "rule_medical_conservative"}]},
        "strategy_memory_guidance": {
            "successful_strategies": [
                {"summary": "Reduced-load training improved completion after fatigue."}
            ],
            "failed_strategies": [
                {"summary": "High-intensity top sets worsened fatigue and completion."}
            ],
        },
    }
    response = "Today we will train with moderate effort and keep the plan simple."

    verifier = AgentVerifier()
    result = verifier.verify_response(response, "Build me a training plan", context)
    repair = verifier.repair_response(result.to_dict(), context)

    assert result.passed is True
    assert "missing_strategy_memory_reuse_guidance" in result.repair_actions
    assert "missing_failed_strategy_avoidance" in result.repair_actions
    assert "missing_strategy_rule_override" in result.repair_actions
    assert "Strategy memory guidance:" in repair["repair_text"]
    assert "Avoid repeating prior failed strategy" in repair["repair_text"]


def test_response_verifier_accepts_strategy_memory_guidance_note():
    context = {
        "current_request_policy": {
            "current_intent": "training_plan",
            "should_generate_plan": True,
            "allow_plan_content": True,
        },
        "active_risk_notes": [{"risk_type": "thyroid"}],
        "strategy_memory_guidance": {
            "successful_strategies": [
                {"summary": "Reduced-load training improved completion after fatigue."}
            ],
            "failed_strategies": [
                {"summary": "High-intensity top sets worsened fatigue and completion."}
            ],
        },
    }
    response = "Today we will train conservatively." + build_strategy_memory_response_note(context)

    result = AgentVerifier().verify_response(response, "Build me a training plan", context)

    assert "missing_strategy_memory_reuse_guidance" not in result.repair_actions
    assert "missing_failed_strategy_avoidance" not in result.repair_actions
    assert "missing_strategy_rule_override" not in result.repair_actions
