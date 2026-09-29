"""Paired attribution checks: do not clear a separate positive self symptom."""

import pytest

from fast_api.app.services.clarification_protocol import ClarificationProtocolValidator
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.risk_evidence import acute_safety_signal, self_risk_evidence


@pytest.mark.parametrize(
    "message,expected",
    [
        ("我刚训练到一半突然喘不上气，请告诉我该怎么办。", True),
        ("我现在胸口发紧，明天的慢跑先别安排。", True),
        ("我没有呼吸不畅，是旁边的人突然喘不上气。", True),
        ("我没有胸闷和呼吸困难。", False),
        ("我说‘胸口发紧’是在引用歌词，并不是我的症状。", False),
        ("歌词里写着胸痛，不是当前症状。", False),
    ],
)
def test_acute_safety_signal_respects_negation_and_quote(message, expected):
    assert acute_safety_signal(message) is expected


@pytest.mark.parametrize(
    "message",
    [
        "是朋友胸口闷，不是我；我没有胸闷和呼吸困难。请给我制定本周训练计划。",
        "我没有胸闷、头晕和呼吸困难，请制定训练计划",
        "我的朋友胸闷，他还头晕；请给我制定训练计划",
        "My friend has chest tightness; I have no pain and dizzy symptoms.",
    ],
)
def test_other_or_explicit_absence_does_not_become_self_risk(message):
    router = IntentRouter()
    decision = router.analyze(message)
    assert decision.risk_level == "low"
    assert "injury_or_risk" not in {decision.primary_intent, *decision.secondary_intents}
    assert self_risk_evidence(message, router.RISK_TERMS) == []
    protocol = ClarificationProtocolValidator().validate(message, decision)
    assert "RED_FLAG_IMMEDIATE_BLOCK" not in protocol.reason_codes
    assert "provide_safety_guidance" not in protocol.allowed_actions


@pytest.mark.parametrize(
    "message",
    [
        "朋友胸闷，我也胸闷。请制定训练计划",
        "朋友胸闷，我没有胸闷，但是我呼吸困难",
        "我没有胸闷和头晕，但呼吸困难",
        "朋友没有胸闷，我胸闷",
        "朋友胸闷我也胸闷",
        "我不是没有胸闷",
        "我不能说没有呼吸困难",
        "我不确定有没有胸闷",
        "胸口闷，还呼吸困难",
        "朋友胸闷。现在呼吸困难",
        "My friend is fine but I have chest tightness",
    ],
)
def test_positive_or_uncertain_self_risk_stays_blocked(message):
    router = IntentRouter()
    decision = router.analyze(message)
    assert decision.risk_level == "high"
    assert "injury_or_risk" in {decision.primary_intent, *decision.secondary_intents}
    assert decision.allowed_actions["generate_plan"] is False
    assert self_risk_evidence(message, router.RISK_TERMS)


def test_narrow_risk_check_keeps_full_negation_context():
    router = IntentRouter()
    message = "我没有胸闷和呼吸困难"
    assert not router._has_risk_signal(message, ["呼吸困难"])
