from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService


# ---- Original tests ----

def test_explanation_and_cases_are_retrieved_as_rag_sources_without_db():
    service = FitnessKnowledgeService(db=None)

    explanations = service.retrieve_explanation_knowledge(
        "injury_or_risk",
        "我有甲亢，今天能不能做HIIT？",
        {},
    )
    cases = service.retrieve_coaching_cases(
        "nutrition_advice",
        "我不自己做饭，外卖怎么吃？",
        {},
    )

    assert any(item["knowledge_id"] == "exp_hyperthyroid_training_boundary_001" for item in explanations)
    assert any(item["case_id"] == "case_takeout_nutrition_001" for item in cases)


def test_decision_rules_match_structured_context_without_rag():
    service = FitnessKnowledgeService(db=None)
    context = {
        "recent_recovery": [{"sleep_hours": 5, "fatigue_score": 9, "soreness_score": 8}],
        "exercise_history": [{"exercise_name": "bench_press", "rpe": 8, "pain_score": 0}],
        "relevant_memories": [],
        "active_risk_notes": [],
    }

    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [item["rule_id"] for item in rules]

    assert "rule_training_hold_high_fatigue_001" in rule_ids
    assert "rule_training_add_weight_clean_bench_001" in rule_ids
    assert rule_ids.index("rule_training_hold_high_fatigue_001") < rule_ids.index("rule_training_add_weight_clean_bench_001")


def test_plan_template_selection_prefers_takeout_nutrition_template():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "fat_loss",
            "experience_level": "intermediate",
            "workout_frequency": 5,
            "equipment_available": ["gym", "barbell"],
            "dietary_preferences": ["takeout_friendly"],
        },
        "relevant_memories": [{"memory_type": "nutrition_habit", "content": "用户平时不自己做饭"}],
    }

    templates = service.select_plan_templates("nutrition_advice", context)

    assert templates[0]["template_id"] == "tpl_nutrition_fat_loss_takeout_001"


# ---- Expanded tests: Knowledge retrieval for new topics ----

def test_retrieve_knowledge_for_female_cycle_training():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "progression_decision",
        "月经周期对训练有什么影响？黄体期是不是该降低强度？",
        {"core_profile": {"biological_sex": "female"}},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert "exp_female_training_cycle_001" in ids


def test_retrieve_knowledge_for_sleep_and_recovery():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "recovery_check",
        "睡不好对训练有什么影响？每天应该睡多久？",
        {},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert "exp_recovery_sleep_science_001" in ids


def test_retrieve_knowledge_for_supplement_questions():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "nutrition_advice",
        "我需要买蛋白粉和肌酸吗？BCAA有用吗？",
        {},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert "exp_supplements_evidence_based_001" in ids


def test_retrieve_knowledge_for_age_related_training():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "training_plan",
        "我50岁了还能练力量训练吗？需要注意什么？",
        {"core_profile": {"age": 50}},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert "exp_age_related_training_001" in ids


def test_retrieve_knowledge_for_injury_return():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "injury_or_risk",
        "肩膀伤好之后怎么重新开始训练？",
        {},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert "exp_injury_return_to_training_001" in ids


def test_retrieve_knowledge_for_macronutrients():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "nutrition_advice",
        "减脂期碳水应该吃多少？完全不吃碳水会怎样？",
        {},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert any("carb" in kid for kid in ids), f"Expected carb knowledge, got: {ids}"


def test_retrieve_knowledge_for_progressive_overload():
    service = FitnessKnowledgeService(db=None)
    explanations = service.retrieve_explanation_knowledge(
        "progression_decision",
        "渐进超负荷到底是什么意思？是不是每次都要加重量？",
        {},
    )
    ids = [item["knowledge_id"] for item in explanations]
    assert "exp_progressive_overload_001" in ids


# ---- Expanded tests: Template selection across goals/levels/equipment ----

def test_template_selection_muscle_gain_intermediate_gym_4d():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "muscle_gain",
            "experience_level": "intermediate",
            "workout_frequency": 4,
            "equipment_available": ["gym", "barbell", "dumbbell"],
        },
    }
    templates = service.select_plan_templates("training_plan", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_training_muscle_gain_intermediate_gym_4d_upper_lower_001" in ids


def test_template_selection_beginner_home_dumbbell():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "general_fitness",
            "experience_level": "beginner",
            "workout_frequency": 3,
            "equipment_available": ["dumbbell"],
        },
    }
    templates = service.select_plan_templates("training_plan", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_training_home_dumbbell_beginner_3d_001" in ids


def test_template_selection_beginner_bodyweight():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "general_fitness",
            "experience_level": "beginner",
            "workout_frequency": 3,
            "equipment_available": [],
        },
    }
    templates = service.select_plan_templates("training_plan", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_training_bodyweight_beginner_3d_001" in ids


def test_template_selection_fat_loss_advanced_gym_5d():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "fat_loss",
            "experience_level": "advanced",
            "workout_frequency": 5,
            "equipment_available": ["gym", "barbell", "dumbbell", "machines", "cable", "cardio"],
        },
    }
    templates = service.select_plan_templates("training_plan", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_training_fat_loss_advanced_gym_5d_001" in ids


def test_template_selection_strength_intermediate_gym_4d():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "strength",
            "experience_level": "intermediate",
            "workout_frequency": 4,
            "equipment_available": ["gym", "barbell", "power_rack"],
        },
    }
    templates = service.select_plan_templates("training_plan", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_training_strength_intermediate_gym_4d_001" in ids


def test_template_selection_maintenance_3d():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "maintenance",
            "experience_level": "intermediate",
            "workout_frequency": 3,
            "equipment_available": ["gym", "barbell", "dumbbell"],
        },
    }
    templates = service.select_plan_templates("training_plan", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_training_maintenance_intermediate_gym_3d_001" in ids


def test_template_selection_vegetarian_nutrition():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "fat_loss",
            "dietary_preferences": ["vegetarian"],
        },
        "relevant_memories": [{"memory_type": "nutrition_habit", "content": "素食"}],
    }
    templates = service.select_plan_templates("nutrition_advice", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_nutrition_fat_loss_vegetarian_001" in ids


def test_template_selection_clean_bulk_nutrition():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {
            "goal": "muscle_gain",
        },
    }
    templates = service.select_plan_templates("nutrition_advice", context)
    ids = [t["template_id"] for t in templates]
    assert "tpl_nutrition_muscle_gain_clean_bulk_001" in ids


# ---- Expanded tests: Coaching case retrieval ----

def test_retrieve_coaching_case_beginner_doms():
    service = FitnessKnowledgeService(db=None)
    cases = service.retrieve_coaching_cases(
        "recovery_check",
        "昨天第一次训练今天全身酸痛走路都困难，是不是受伤了？",
        {},
    )
    ids = [c["case_id"] for c in cases]
    assert "case_beginner_soreness_worry_001" in ids


def test_retrieve_coaching_case_gym_intimidation():
    service = FitnessKnowledgeService(db=None)
    cases = service.retrieve_coaching_cases(
        "general_chat",
        "我办了健身卡但不敢去，看到里面都是肌肉男就害怕",
        {},
    )
    ids = [c["case_id"] for c in cases]
    assert "case_beginner_intimidated_by_gym_001" in ids


def test_retrieve_coaching_case_supplement_overwhelm():
    service = FitnessKnowledgeService(db=None)
    cases = service.retrieve_coaching_cases(
        "nutrition_advice",
        "刚开始健身要买哪些补剂？蛋白粉肌酸BCAA都需要吗？",
        {},
    )
    ids = [c["case_id"] for c in cases]
    assert "case_beginner_supplement_questions_001" in ids


def test_retrieve_coaching_case_knee_pain_replacement():
    service = FitnessKnowledgeService(db=None)
    cases = service.retrieve_coaching_cases(
        "injury_or_risk",
        "深蹲膝盖前面疼有什么动作可以替代？",
        {},
    )
    ids = [c["case_id"] for c in cases]
    assert "case_knee_pain_squat_replacement_001" in ids


def test_retrieve_coaching_case_vegetarian_muscle_gain():
    service = FitnessKnowledgeService(db=None)
    cases = service.retrieve_coaching_cases(
        "nutrition_advice",
        "我吃素想增肌，担心蛋白质不够怎么办？",
        {},
    )
    ids = [c["case_id"] for c in cases]
    assert "case_nutrition_vegetarian_muscle_gain_001" in ids


# ---- Expanded tests: Build knowledge context completeness ----

def test_build_knowledge_context_returns_all_four_sections():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"goal": "fat_loss"},
        "recent_recovery": [{"sleep_hours": 7, "fatigue_score": 4}],
        "exercise_history": [{"exercise_name": "bench_press", "rpe": 8, "pain_score": 0}],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    result = service.build_knowledge_context("progression_decision", "我能加重吗？", context)
    assert "explanation_knowledge" in result
    assert "decision_rules" in result
    assert "plan_templates" in result
    assert "coaching_cases" in result
    assert "debug" in result
    assert len(result["debug"]["matched_rule_ids"]) > 0


def test_build_knowledge_context_debug_includes_all_match_ids():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"goal": "fat_loss", "dietary_preferences": ["takeout_friendly"]},
        "relevant_memories": [{"memory_type": "nutrition_habit", "content": "用户不自己做饭"}],
        "active_risk_notes": [],
    }
    result = service.build_knowledge_context("nutrition_advice", "外卖吃什么", context)
    assert len(result["debug"]["matched_rule_ids"]) >= 1
    assert len(result["debug"]["matched_template_ids"]) >= 1
    assert len(result["debug"]["matched_knowledge_ids"]) >= 1
    assert len(result["debug"]["matched_case_ids"]) >= 1


def test_match_decision_rules_sorted_by_priority_desc():
    service = FitnessKnowledgeService(db=None)
    context = {
        "relevant_memories": [{"memory_type": "medical_context"}],
        "active_risk_notes": [{"risk_type": "thyroid", "severity_score": 0.9}],
        "recent_recovery": [{"sleep_hours": 5, "fatigue_score": 9}],
        "exercise_history": [],
    }
    rules = service.match_decision_rules("training_plan", context)
    priorities = [r["priority"] for r in rules]
    assert priorities == sorted(priorities, reverse=True), f"Rules not sorted by priority: {priorities}"
    assert priorities[0] >= 90  # Medical boundary should be highest priority
