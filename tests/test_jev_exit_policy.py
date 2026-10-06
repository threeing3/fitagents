import asyncio
from dataclasses import replace

import pytest

from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.jev_exit_policy import JevCalibration, JevExitPolicy
from fast_api.app.services.jev_intent_client import STATES, TASKS, JevIntentClient, JevIntentResult
from tests.test_intent_decision_engine import FakeModelProvider


def candidate(intent="concept_explanation"):
    states = {task: "requested" if task == intent else "absent" for task in TASKS}
    return JevIntentResult(
        True,
        True,
        "candidate_only_uncalibrated",
        model=JevIntentClient.MODEL,
        task_states=states,
        confidence={task: 0.99 for task in TASKS},
        probabilities={
            task: {state: float(state == selected) for state in STATES}
            for task, selected in states.items()
        },
        current_risk_probability=0.0,
        usage={"input_tokens": 2000, "output_tokens": 100},
    )


def policy():
    # Simulated reviewed profile for branch testing, NOT real calibration.
    return JevExitPolicy(
        JevCalibration("test-fixture-only", JevIntentClient.MODEL, {"concept_explanation": 0.98})
    )


def test_no_calibration_never_accepts_even_perfect_candidate():
    assert (
        JevExitPolicy().assess("解释渐进超负荷", candidate(), IntentRouter().analyze("你好")).reason
        == "calibration_missing"
    )


def test_read_only_accepts_complete_simulated_candidate():
    assert (
        policy().assess("解释渐进超负荷", candidate(), IntentRouter().analyze("你好")).outcome
        == "accept"
    )


@pytest.mark.parametrize(
    "kind",
    ["second", "absent_uncertain", "risk", "version", "negated", "background", "write", "missing"],
)
def test_uncertain_or_unsafe_candidate_escalates(kind):
    result = candidate()
    if kind == "second":
        result.task_states["nutrition_advice"] = "requested"
    elif kind == "absent_uncertain":
        result.confidence["training_log"] = 0.4
    elif kind == "risk":
        result.current_risk_probability = 0.5
    elif kind == "version":
        result.model = "new-version"
    elif kind in {"negated", "background"}:
        result.task_states["training_plan"] = kind
    elif kind == "write":
        result = candidate("training_log")
    else:
        del result.confidence["training_log"]
    assert (
        policy().assess("解释渐进超负荷", result, IntentRouter().analyze("你好")).outcome
        == "escalate"
    )


class FakeJev:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def classify(self, message):
        self.calls += 1
        return self.result


def test_rule_exit_skips_jev_and_llm():
    jev, provider = FakeJev(candidate()), FakeModelProvider("not-used")
    result = asyncio.run(IntentDecisionEngine(provider, jev_client=jev).decide("你好"))
    assert jev.calls == provider.model.calls == 0
    assert not result.decision.provenance["jev_used"]


def test_uncalibrated_jev_records_candidate_and_continues_llm():
    jev, provider = FakeJev(candidate()), FakeModelProvider("not-json")
    result = asyncio.run(IntentDecisionEngine(provider, jev_client=jev).decide("解释渐进超负荷"))
    assert jev.calls == provider.model.calls == 1
    assert result.decision.provenance["jev_exit"]["reason"] == "calibration_missing"
    assert not result.decision.provenance["jev_used"]


def test_simulated_calibrated_jev_exits_but_keeps_clarification():
    jev, provider = FakeJev(candidate()), FakeModelProvider("not-used")
    result = asyncio.run(
        IntentDecisionEngine(provider, jev_client=jev, jev_exit_policy=policy()).decide(
            "解释渐进超负荷"
        )
    )
    assert provider.model.calls == 0
    assert result.decision.primary_intent == "concept_explanation"
    assert result.decision.provenance["final_source"] == "jev_with_policy_checks"
    assert not result.decision.allowed_actions["generate_plan"]


def test_write_route_cannot_be_enabled_by_calibration():
    with pytest.raises(ValueError):
        JevExitPolicy(replace(policy().calibration, route_thresholds={"training_log": 0.99}))
