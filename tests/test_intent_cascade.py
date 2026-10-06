import asyncio

import pytest

from fast_api.app.services.intent_cascade import ExampleVectorIndex, IntentCascadePolicy
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from tests.test_intent_decision_engine import FakeModelProvider


@pytest.mark.parametrize("message", ["你好", "帮我制定一周训练计划", "查询昨天的训练记录"])
def test_full_message_allowlist_accepts(message):
    result = IntentCascadePolicy().assess(message, IntentRouter().analyze(message))
    assert result.outcome == "accept"
    assert result.candidates == []


@pytest.mark.parametrize(
    "message",
    [
        "帮我制定一周训练计划，再看看晚饭吃什么",
        "不要生成训练计划",
        "查询昨天的训练记录，再把距离改成5公里",
        "他说帮我制定一周训练计划，我只是引用",
        "周五还是跑步",
        "我胸闷但想训练",
        "把那个改成跑步",
        "帮我制定一周训练计划，不要力量训练",
    ],
)
def test_uncovered_content_never_exits_on_rule_score(message):
    result = IntentCascadePolicy().assess(message, IntentRouter().analyze(message))
    assert result.outcome == "escalate"
    assert "uncalibrated" in result.reason


def test_exact_vector_match_is_not_permission_to_exit():
    class Encoder:
        model_id = "test-only"

        def encode(self, texts):
            return [[1.0, 0.0] if "渐进超负荷" in text else [0.0, 1.0] for text in texts]

    message = "什么是渐进超负荷"
    index = ExampleVectorIndex(encoder=Encoder())
    result = IntentCascadePolicy(index).assess(message, IntentRouter().analyze(message))
    assert result.candidates[0]["score"] == pytest.approx(1)
    assert result.outcome == "escalate"
    assert index.search("") == []


def test_complex_request_forces_model_even_when_old_heuristic_would_skip():
    provider = FakeModelProvider("not-json")
    result = asyncio.run(
        IntentDecisionEngine(provider).decide("帮我制定一周训练计划，不要力量训练")
    )
    assert provider.model.calls == 1
    assert result.decision.provenance["cascade"]["outcome"] == "escalate"
    assert result.decision.provenance["model_succeeded"] is False


def test_simple_request_skips_model_and_records_acceptance():
    provider = FakeModelProvider("not-used")
    result = asyncio.run(IntentDecisionEngine(provider).decide("帮我制定一周训练计划"))
    assert provider.model.calls == 0
    assert result.decision.provenance["cascade"]["reason"] == "full_message_allowlist"
