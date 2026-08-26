from fast_api.app.services.clarification_protocol import ClarificationProtocolValidator
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter


def test_injury_protocol_requires_details_and_blocks_plan():
    validator = ClarificationProtocolValidator()
    decision = IntentDecision("injury_or_risk", ["training_plan"], risk_level="medium")

    result = validator.validate("膝盖不舒服，今天还能练腿吗", decision)
    updated = validator.apply(decision, result, IntentRouter())

    assert result.reason_codes == ["INJURY_DETAILS_REQUIRED"]
    assert result.missing_slots == ["symptom_severity", "symptom_duration"]
    assert updated.needs_clarification is True
    assert updated.allowed_actions["generate_plan"] is False


def test_red_flag_blocks_immediately_without_forcing_question():
    validator = ClarificationProtocolValidator()
    decision = IntentDecision("injury_or_risk", risk_level="high")

    result = validator.validate("冲刺时胸闷心悸，我还要继续吗", decision)

    assert result.needs_clarification is False
    assert result.allowed_actions == ["provide_safety_guidance"]
    assert result.reason_codes == ["RED_FLAG_IMMEDIATE_BLOCK"]


def test_ambiguous_reference_requires_referent():
    result = ClarificationProtocolValidator().validate(
        "把刚才那个换掉", IntentDecision("general_chat")
    )

    assert result.needs_clarification is True
    assert result.missing_slots == ["referent"]
    assert result.reason_codes == ["AMBIGUOUS_REFERENCE"]
