"""Tests for safety guardrails: rule functions, severity tiers, and integration."""
from fast_api.app.core.guardrails import (
    BLOCK_REPLACEMENT_DANGEROUS,
    BLOCK_REPLACEMENT_GENERIC,
    BLOCK_REPLACEMENT_MEDICAL,
    Flag,
    GuardrailResult,
    Severity,
    _compile_rules,
    quick_check,
    run_guardrails,
)


# ----------------------------------------------------------------
# Helper
# ----------------------------------------------------------------

def _flags_for(response: str, user_message: str | None = None) -> list[Flag]:
    result = run_guardrails(response, user_message=user_message)
    return result.flags


# ----------------------------------------------------------------
# medication_discontinuation — BLOCK
# ----------------------------------------------------------------

def test_medication_discontinuation_english_blocked():
    flags = _flags_for("You should stop taking your medication and try exercise instead.")
    assert any(f.rule_id == "stop_medication" for f in flags), flags
    assert all(f.severity == Severity.BLOCK for f in flags)


def test_medication_discontinuation_chinese_blocked():
    flags = _flags_for("建议你停掉赛治，用运动替代。")
    assert any(f.rule_id == "stop_medication_cn" for f in flags), flags


def test_medication_discontinuation_reduce_dosage():
    flags = _flags_for("You can reduce your medication dose by half starting this week.")
    assert any(f.rule_id == "reduce_medication" for f in flags)


def test_medication_discontinuation_wean_off():
    flags = _flags_for("Try to wean yourself off your medication gradually.")
    assert any(f.rule_id == "wean_off_medication" for f in flags)


def test_medication_discontinuation_no_false_positive():
    """Medication mention in non-directive context should not flag."""
    flags = _flags_for("Be aware that some medications can affect your heart rate during exercise.")
    med_flags = [f for f in flags if f.severity == Severity.BLOCK]
    assert all("medication" not in (f.rule_id or "") for f in med_flags) or not med_flags


# ----------------------------------------------------------------
# medical_diagnosis — BLOCK
# ----------------------------------------------------------------

def test_medical_diagnosis_english_blocked():
    flags = _flags_for("You may have a thyroid disorder based on these symptoms.")
    assert any(f.rule_id == "medical_diagnosis" for f in flags), flags


def test_medical_diagnosis_chinese_blocked():
    flags = _flags_for("根据你的描述，你可能患有甲亢。")
    assert any(f.rule_id == "medical_diagnosis_cn" for f in flags), flags


def test_medical_diagnosis_allowed_with_referral():
    """Diagnosis-like language is OK when accompanied by doctor referral."""
    flags = _flags_for(
        "These symptoms could indicate a thyroid issue, but you should see a doctor for proper diagnosis."
    )
    diag_flags = [f for f in flags if f.category == "medical_boundary"]
    assert len(diag_flags) == 0, f"Should not flag when doctor referral is present: {diag_flags}"


def test_medical_diagnosis_clear_sign_of():
    flags = _flags_for("This is a clear sign of overtraining syndrome and you should rest.")
    assert any(f.rule_id == "clear_sign_of" for f in flags)


# ----------------------------------------------------------------
# dangerous_calorie_restriction — BLOCK / WARN
# ----------------------------------------------------------------

def test_dangerous_calorie_restriction_below_800_blocked():
    flags = _flags_for("To lose weight fast, eat only 600 calories a day.")
    block_flags = [f for f in flags if f.severity == Severity.BLOCK]
    assert any("low_calorie" in f.rule_id for f in block_flags), flags


def test_dangerous_calorie_restriction_chinese_blocked():
    flags = _flags_for("建议每天只吃500大卡来快速减脂。")
    block_flags = [f for f in flags if f.severity == Severity.BLOCK]
    assert any("low_calorie" in f.rule_id for f in block_flags), flags


def test_calorie_restriction_moderate_not_flagged():
    """Normal calorie targets should not be flagged."""
    flags = _flags_for("Aim for about 1800 calories per day for steady fat loss.")
    cal_flags = [f for f in flags if "calorie" in f.rule_id]
    assert len(cal_flags) == 0, f"Normal calories should not flag: {cal_flags}"


def test_dangerous_calorie_restriction_extended_fast():
    flags = _flags_for("Try fasting for 5 days to reset your metabolism.")
    assert any(f.rule_id == "extended_fast" for f in flags)


# ----------------------------------------------------------------
# train_through_pain — BLOCK
# ----------------------------------------------------------------

def test_train_through_pain_english_blocked():
    flags = _flags_for("Just push through the pain during squats, it will get better.")
    assert any(f.rule_id == "train_through_pain" for f in flags)


def test_train_through_pain_ignore():
    flags = _flags_for("Ignore the shoulder pain and keep pressing.")
    assert any(f.rule_id == "ignore_pain" for f in flags)


def test_train_through_pain_no_pain_no_gain():
    flags = _flags_for("No pain no gain — if it hurts you're doing it right.")
    assert any(f.rule_id == "no_pain_no_gain" for f in flags)


def test_train_through_pain_chinese_blocked():
    flags = _flags_for("膝盖疼是正常的，坚持下去就好了。")
    assert any(f.rule_id in ("push_through_pain_cn", "pain_normal_cn") for f in flags)


# ----------------------------------------------------------------
# skip_warmup — BLOCK
# ----------------------------------------------------------------

def test_skip_warmup_blocked():
    flags = _flags_for("You don't need to warm up before lifting, just go straight in.")
    assert any(f.rule_id == "skip_warmup" for f in flags)


def test_skip_warmup_unnecessary():
    flags = _flags_for("Warmups are a waste of time for experienced lifters.")
    assert any(f.rule_id == "warmup_unnecessary" for f in flags)


# ----------------------------------------------------------------
# dangerous_supplements — BLOCK
# ----------------------------------------------------------------

def test_dangerous_supplements_steroids_blocked():
    flags = _flags_for("You should try steroids to speed up your muscle gain.")
    assert any(f.rule_id == "dangerous_supplement" for f in flags)


def test_dangerous_supplements_sarms_blocked():
    flags = _flags_for("SARMs are a safer alternative — I recommend trying Ostarine.")
    assert any(f.rule_id == "dangerous_supplement" for f in flags)


def test_dangerous_supplements_chinese_blocked():
    flags = _flags_for("你可以试试减肥药来加速效果。")
    assert any(f.rule_id == "dangerous_supplement_cn" for f in flags)


def test_supplements_normal_not_flagged():
    """Normal supplements should not be flagged."""
    flags = _flags_for("Consider taking creatine and whey protein for recovery.")
    supp_flags = [f for f in flags if "supplement" in f.rule_id]
    assert len(supp_flags) == 0, f"Normal supplements should not flag: {supp_flags}"


# ----------------------------------------------------------------
# missing_disclaimer — WARN
# ----------------------------------------------------------------

def test_missing_disclaimer_warns():
    flags = _flags_for("Your knee pain is likely from patellar tendinitis. Reduce squat depth.")
    assert any(f.rule_id == "missing_medical_disclaimer" for f in flags)


def test_missing_disclaimer_with_referral_passes():
    """When doctor referral is present, no missing disclaimer flag."""
    flags = _flags_for(
        "Your knee pain could be tendinitis. Please consult a doctor for proper diagnosis. Reduce squat depth."
    )
    discl_flags = [f for f in flags if f.rule_id == "missing_medical_disclaimer"]
    assert len(discl_flags) == 0, f"Should not flag when referral is present: {discl_flags}"


def test_missing_disclaimer_no_medical_topic_passes():
    """No medical topic = no disclaimer needed."""
    flags = _flags_for("Try doing 3 sets of 10 squats with proper form.")
    discl_flags = [f for f in flags if f.rule_id == "missing_medical_disclaimer"]
    assert len(discl_flags) == 0


# ----------------------------------------------------------------
# missing_modifiers — WARN
# ----------------------------------------------------------------

def test_missing_modifiers_absolute_english():
    flags = _flags_for("You must always do deadlifts first in every workout.")
    assert any(f.rule_id == "absolute_advice" for f in flags)


def test_missing_modifiers_absolute_chinese():
    flags = _flags_for("你绝对要做深蹲，这是必须的。")
    assert any(f.rule_id == "absolute_advice_cn" for f in flags)


# ----------------------------------------------------------------
# eating_disorder_triggers — WARN
# ----------------------------------------------------------------

def test_ed_trigger_purge():
    flags = _flags_for("If you overeat, just purge afterwards to stay on track.")
    assert any(f.rule_id == "ed_trigger_purge" for f in flags)


def test_ed_trigger_body_shame():
    flags = _flags_for("You're too fat and need to lose weight fast before summer.")
    assert any(f.rule_id == "ed_trigger_body_shame" for f in flags)


def test_ed_trigger_detox():
    flags = _flags_for("Try this juice cleanse to detox and flush out toxins from your body.")
    assert any(f.rule_id == "ed_trigger_detox" for f in flags)


# ----------------------------------------------------------------
# excessive_exercise — WARN
# ----------------------------------------------------------------

def test_excessive_exercise_frequency_english():
    flags = _flags_for("Train every single day for maximum results.")
    assert any(f.rule_id == "excessive_frequency" for f in flags)


def test_excessive_exercise_chinese():
    flags = _flags_for("建议你每天训练，一周七天不要休息。")
    assert any(f.rule_id == "excessive_frequency_cn" for f in flags)


def test_excessive_exercise_duration():
    flags = _flags_for("Do 3 hours per session for optimal hypertrophy.")
    assert any(f.rule_id == "excessive_duration" for f in flags)


# ----------------------------------------------------------------
# run_guardrails — BLOCK result
# ----------------------------------------------------------------

def test_run_guardrails_block_returns_correct_action():
    result = run_guardrails("Stop taking your thyroid medication immediately.")
    assert result.action == Severity.BLOCK
    assert result.passed is False


def test_run_guardrails_block_uses_medical_replacement():
    result = run_guardrails("You have hyperthyroidism based on your symptoms.")
    assert result.action == Severity.BLOCK
    assert result.blocked_replacement is not None
    assert "医疗" in result.blocked_replacement


def test_run_guardrails_block_uses_dangerous_replacement():
    result = run_guardrails("Just push through the knee pain, no pain no gain.")
    assert result.action == Severity.BLOCK
    assert result.blocked_replacement is not None
    assert "健康风险" in result.blocked_replacement


def test_run_guardrails_warn_returns_passed():
    result = run_guardrails(
        "Your knee pain could be from overuse. Reduce squat volume this week."
    )
    assert result.action == Severity.WARN
    assert result.passed is True
    assert len(result.flags) > 0


def test_run_guardrails_pass_clean_response():
    result = run_guardrails(
        "Great work today! Your squat form is improving. Remember to warm up before each session "
        "and listen to your body. If you feel any sharp pain, stop and consult a doctor."
    )
    assert result.action == Severity.PASS
    assert result.passed is True
    assert len(result.flags) == 0


def test_run_guardrails_empty_response():
    result = run_guardrails("")
    assert result.action == Severity.PASS
    assert result.passed is True


def test_run_guardrails_multiple_rules_fire():
    """A response can trigger multiple rules — all should be collected."""
    result = run_guardrails(
        "Stop taking your medication and train through the pain every single day. "
        "You must never warm up."
    )
    assert result.action == Severity.BLOCK
    # Should have at least medication + train_through_pain + skip_warmup + excessive
    rule_ids = {f.rule_id for f in result.flags}
    assert len(rule_ids) >= 3, f"Expected at least 3 distinct rules, got {rule_ids}"


# ----------------------------------------------------------------
# quick_check
# ----------------------------------------------------------------

def test_quick_check_safe_returns_true():
    assert quick_check("Your form is improving! Keep up the good work.") is True


def test_quick_check_dangerous_returns_false():
    assert quick_check("Stop taking your prescription medication right now.") is False


# ----------------------------------------------------------------
# Context-aware: _medical_diagnosis proximity check
# ----------------------------------------------------------------

def test_medical_diagnosis_with_embedded_referral_passes():
    """The proximity check should prevent flagging when 'see a doctor' is nearby."""
    flags = _flags_for(
        "While these symptoms might suggest you have a thyroid condition, "
        "it is essential that you consult a doctor for a proper evaluation."
    )
    diag_flags = [f for f in flags if f.category == "medical_boundary"]
    assert len(diag_flags) == 0, f"Should pass with nearby referral: {diag_flags}"


# ----------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------

def test_guardrail_handles_none_user_message():
    result = run_guardrails("Stop taking your medication.", user_message=None)
    assert result.action == Severity.BLOCK


def test_guardrail_handles_custom_rules():
    """Custom rule set should be usable."""
    def always_warn(response: str, _ctx):
        return [Flag(rule_id="test_rule", severity=Severity.WARN, category="test", message="test")]
    result = run_guardrails("anything", rules=[always_warn])
    assert result.action == Severity.WARN
    assert len(result.flags) == 1


def test_guardrail_result_dataclass_defaults():
    result = GuardrailResult()
    assert result.action is None  # No default — must be set
    assert result.flags == []
    assert result.passed is True
    assert result.blocked_replacement is None


def test_rule_compile_returns_all_ten():
    rules = _compile_rules()
    assert len(rules) == 10, f"Expected 10 rules, got {len(rules)}"


def test_all_replacement_messages_are_non_empty():
    assert len(BLOCK_REPLACEMENT_MEDICAL) > 20
    assert len(BLOCK_REPLACEMENT_DANGEROUS) > 20
    assert len(BLOCK_REPLACEMENT_GENERIC) > 20
