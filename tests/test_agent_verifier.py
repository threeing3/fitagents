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
    context = {
        "relevant_memories": [
            {"memory_type": "medical_context", "summary": "甲亢用药背景"}
        ]
    }

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
    assert repair["repaired"] is True
    assert "旧计划指令" in repair["repair_text"]


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
