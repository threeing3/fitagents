from fast_api.app.services.decision_rules import TrainingDecisionRules
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService


# ---- Original TrainingDecisionRules tests ----

def test_progression_rule_increases_clean_completed_upper_body_work():
    rules = TrainingDecisionRules()

    decision = rules.evaluate_progression(
        "bench_press",
        [
            {"completed": True, "rpe": 8, "pain_score": 0},
            {"completed": True, "rpe": 8.5, "pain_score": 0},
        ],
        recovery_logs=[{"sleep_hours": 8, "fatigue_score": 4}],
    )

    assert decision["decision_type"] == "progression_adjustment"
    assert decision["decision_result"] == "increase_next_session_by_2.5kg"


def test_progression_rule_holds_load_when_recovery_is_poor():
    rules = TrainingDecisionRules()

    decision = rules.evaluate_progression(
        "bench_press",
        [{"completed": True, "rpe": 8, "pain_score": 0}],
        recovery_logs=[{"sleep_hours": 5.5, "fatigue_score": 9}],
    )

    assert decision["decision_result"] == "hold_load_and_reduce_accessory_volume"


def test_progression_rule_deloads_when_pain_is_present():
    rules = TrainingDecisionRules()

    decision = rules.evaluate_progression(
        "squat",
        [{"completed": True, "rpe": 8, "pain_score": 4}],
    )

    assert decision["decision_type"] == "deload"


def test_progression_rule_prioritizes_high_risk_symptoms():
    rules = TrainingDecisionRules()

    decision = rules.evaluate_progression(
        "squat",
        [{"completed": True, "rpe": 7, "pain_score": 0}],
        symptom_logs=[{"symptom_type": "胸闷", "severity_score": 8}],
    )

    assert decision["decision_type"] == "risk_warning"
    assert decision["decision_result"] == "avoid_high_intensity_training"


# ---- Expanded tests: Knowledge base rule matching ----

def test_high_fatigue_rule_matches_poor_recovery_context():
    service = FitnessKnowledgeService(db=None)
    context = {
        "recent_recovery": [{"sleep_hours": 5, "fatigue_score": 9, "soreness_score": 8}],
        "exercise_history": [],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_training_hold_high_fatigue_001" in rule_ids


def test_medical_hyperthyroid_rule_matches_medical_context_memory():
    service = FitnessKnowledgeService(db=None)
    context = {
        "relevant_memories": [{"memory_type": "medical_context", "content": "甲亢"}],
        "active_risk_notes": [],
        "recent_recovery": [],
        "exercise_history": [],
    }
    rules = service.match_decision_rules("training_plan", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_medical_hyperthyroid_conservative_001" in rule_ids


def test_injury_recovery_rule_matches_recent_injury_context():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"recent_injury": True},
        "relevant_memories": [],
        "active_risk_notes": [],
        "recent_recovery": [{"pain_score": 6}],
        "exercise_history": [],
    }
    rules = service.match_decision_rules("training_plan", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_injury_recovery_return_001" in rule_ids


def test_injury_pain_rule_matches_sharp_pain_during_exercise():
    service = FitnessKnowledgeService(db=None)
    context = {
        "exercise_history": [
            {"exercise_name": "bench_press", "pain_type": "sharp", "pain_score": 5}
        ],
        "relevant_memories": [],
        "active_risk_notes": [],
        "recent_recovery": [],
    }
    rules = service.match_decision_rules("exercise_selection", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_injury_pain_during_exercise_001" in rule_ids


def test_shoulder_injury_modify_rule_matches_shoulder_memory():
    service = FitnessKnowledgeService(db=None)
    context = {
        "relevant_memories": [{"content": "肩膀不舒服"}],
        "exercise_history": [
            {"exercise_name": "overhead_press", "pain_score": 6}
        ],
        "active_risk_notes": [],
        "recent_recovery": [],
    }
    rules = service.match_decision_rules("exercise_selection", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_injury_shoulder_modify_overhead_001" in rule_ids


def test_plateau_strength_3week_stall_rule_matches():
    service = FitnessKnowledgeService(db=None)
    context = {
        "exercise_history": [
            {"exercise_name": "squat", "progression_status": "stalled", "stall_weeks": 4}
        ],
        "recent_recovery": [{"fatigue_score": 5, "sleep_hours": 7}],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_plateau_strength_3week_stall_001" in rule_ids


def test_plateau_fat_loss_2week_stall_rule_matches():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"goal": "fat_loss"},
        "recent_checkins": [
            {"weight_change_kg": 0.0, "nutrition_adherence": 0.8},
            {"weight_change_kg": 0.1, "nutrition_adherence": 0.85},
        ],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("nutrition_advice", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_plateau_fat_loss_2week_stall_001" in rule_ids


def test_plateau_muscle_gain_calorie_gap_rule_matches():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"goal": "muscle_gain"},
        "recent_checkins": [{"weight_change_kg": 0.1}],
        "exercise_history": [
            {"progression_status": "stalled", "stall_weeks": 5}
        ],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("nutrition_advice", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_plateau_muscle_gain_calorie_gap_001" in rule_ids


def test_female_luteal_phase_rule_matches():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"biological_sex": "female"},
        "recent_checkins": [{"menstrual_phase": "luteal"}],
        "recent_recovery": [{"fatigue_score": 8}],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_female_luteal_phase_adjust_001" in rule_ids


def test_female_follicular_optimize_rule_matches():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"biological_sex": "female"},
        "recent_checkins": [{"menstrual_phase": "follicular"}],
        "recent_recovery": [{"fatigue_score": 4, "sleep_hours": 8}],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_female_follicular_optimize_001" in rule_ids


def test_overtraining_rule_matches_multiple_red_flags():
    service = FitnessKnowledgeService(db=None)
    context = {
        "recent_recovery": [
            {"fatigue_score": 9, "sleep_hours": 5, "motivation_score": 2, "mood_score": 2}
        ],
        "recent_checkins": [{"resting_hr_elevated": True}],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_overtraining_multiple_red_flags_001" in rule_ids


def test_knee_pain_squat_modify_rule_matches():
    service = FitnessKnowledgeService(db=None)
    context = {
        "exercise_history": [
            {"exercise_name": "squat", "pain_location": "knee", "pain_score": 4}
        ],
        "relevant_memories": [],
        "active_risk_notes": [],
        "recent_recovery": [],
    }
    rules = service.match_decision_rules("exercise_selection", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_joint_knee_pain_squat_modify_001" in rule_ids


def test_lower_back_modify_rule_matches_back_pain_memory():
    service = FitnessKnowledgeService(db=None)
    context = {
        "relevant_memories": [{"content": "腰伤"}],
        "exercise_history": [],
        "active_risk_notes": [],
        "recent_recovery": [],
    }
    rules = service.match_decision_rules("exercise_selection", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_joint_lower_back_modify_001" in rule_ids


def test_chronic_sleep_rule_matches_consecutive_bad_sleep():
    service = FitnessKnowledgeService(db=None)
    context = {
        "recent_recovery": [
            {"sleep_hours": 5, "consecutive_bad_sleep_days": 6},
            {"sleep_hours": 4.5, "consecutive_bad_sleep_days": 6},
        ],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_sleep_chronic_poor_quality_001" in rule_ids


def test_age_masters_rule_matches_over_45():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"age": 50},
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("training_plan", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_age_masters_over45_programming_001" in rule_ids


def test_beginner_form_rule_matches_low_experience_and_poor_form():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"training_experience_months": 3},
        "exercise_history": [
            {"exercise_name": "squat", "form_quality_score": 5}
        ],
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("progression_decision", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_beginner_form_priority_over_weight_001" in rule_ids


def test_hydration_heat_rule_matches_hot_environment():
    service = FitnessKnowledgeService(db=None)
    context = {
        "core_profile": {"training_environment": "hot"},
        "relevant_memories": [],
        "active_risk_notes": [],
    }
    rules = service.match_decision_rules("training_plan", context)
    rule_ids = [r["rule_id"] for r in rules]
    assert "rule_hydration_heat_training_adjust_001" in rule_ids


# ---- TrainingDecisionRules edge cases ----

def test_progression_lower_body_returns_5kg_increment():
    rules = TrainingDecisionRules()

    decision = rules.evaluate_progression(
        "squat",
        [
            {"completed": True, "rpe": 8, "pain_score": 0},
            {"completed": True, "rpe": 7.5, "pain_score": 0},
        ],
        recovery_logs=[{"sleep_hours": 8, "fatigue_score": 3}],
    )

    assert "5kg" in decision["decision_result"]


def test_progression_empty_history_returns_keep_load():
    rules = TrainingDecisionRules()

    decision = rules.evaluate_progression("bench_press", [])

    assert "keep_current_load" in decision["decision_result"]


def test_progression_does_not_lower_body_pull_up():
    rules = TrainingDecisionRules()
    assert not rules._is_lower_body("pull_up")
    assert not rules._is_lower_body("bench_press")
    assert rules._is_lower_body("squat")
    assert rules._is_lower_body("deadlift")
    assert rules._is_lower_body("leg_press")
