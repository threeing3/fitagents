"""Scripted routing/delegation checks, not live model quality measurements."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from fast_api.app.services.agent_runtime import ToolRegistry
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.domain_subagents import DomainSubagents
from fast_api.app.services.intent_cascade import ExampleVectorIndex, IntentCascadePolicy
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import IntentDecisionEngine
from fast_api.app.services.llm_intent_classifier import LLMIntentClassifier
from fast_api.app.services.runtime_router import RuntimeRoute
from tests.test_domain_subagents import Provider, final, reader
from tests.test_intent_decision_engine import FakeModelProvider


@pytest.mark.parametrize(
    "message,intent,domains",
    [
        ("帮我制定一周训练计划", "training_plan", ["training"]),
        ("推荐运动后的晚餐", "nutrition_advice", ["nutrition"]),
        ("评估今天的恢复状态", "recovery_check", ["recovery"]),
        ("什么是渐进超负荷", "concept_explanation", []),
        ("查询昨天的训练记录", "memory_query", []),
    ],
)
def test_clear_dispatch_needs_no_classifier_or_vectors(monkeypatch, message, intent, domains):
    def forbidden(*args, **kwargs):
        raise AssertionError("default routing must not encode vectors")

    monkeypatch.setattr(ExampleVectorIndex, "retrieve", forbidden)
    provider = FakeModelProvider("not-used")
    result = asyncio.run(IntentDecisionEngine(provider).decide(message))
    assert result.decision.primary_intent == intent
    assert provider.model.calls == 0
    assert result.decision.provenance["delegated_domains"] == domains
    assert result.decision.provenance["semantic_assistance_enabled"] is False
    assert result.decision.provenance["cascade"]["candidates"] == []
    assert result.decision.provenance["execution_authority"] != "router"


def test_multidomain_one_host_call_then_bounded_children():
    host = FakeModelProvider(
        json.dumps(
            {
                "primary_intent": "training_plan",
                "secondary_intents": ["nutrition_advice"],
                "risk_level": "low",
                "needs_clarification": False,
            }
        )
    )
    result = asyncio.run(
        IntentDecisionEngine(host).decide("帮我排好未来三天锻炼，也建议运动后吃什么")
    )
    assert host.model.calls == 1
    assert result.decision.provenance["delegated_domains"] == ["training", "nutrition"]
    packet = {
        "intent": result.decision.primary_intent,
        "secondary_intents": result.decision.secondary_intents,
    }
    child_provider = Provider([final(), final()])

    async def run():
        return [
            entry async for entry in DomainSubagents(child_provider, reader).run(packet, "本轮请求")
        ]

    entries = asyncio.run(run())
    assert [item["domain"] for item in entries[-1]["results"]] == ["training", "nutrition"]
    assert all(item["status"] == "completed" for item in entries[-1]["results"])
    assert child_provider.calls == 2  # business execution, not duplicate host classification


def test_negated_rule_task_is_not_reintroduced_after_host_decision():
    provider = FakeModelProvider(
        json.dumps(
            {
                "primary_intent": "concept_explanation",
                "secondary_intents": [],
                "risk_level": "low",
            }
        )
    )
    result = asyncio.run(IntentDecisionEngine(provider).decide("不需要训练计划，只解释一下蛋白质"))
    assert provider.model.calls == 1
    assert result.decision.primary_intent == "concept_explanation"
    assert result.decision.secondary_intents == []
    assert result.decision.provenance["delegated_domains"] == []
    assert result.runtime_mode == "llm_driven"
    assert result.decision.allowed_actions["generate_plan"] is False


def test_risk_stays_host_owned_even_when_model_requests_children():
    provider = FakeModelProvider(
        json.dumps(
            {
                "primary_intent": "training_plan",
                "secondary_intents": ["nutrition_advice"],
                "risk_level": "low",
            }
        )
    )
    result = asyncio.run(IntentDecisionEngine(provider).decide("我胸闷还想训练，晚饭怎么安排"))
    assert result.decision.primary_intent == "injury_or_risk"
    assert result.decision.provenance["delegated_domains"] == []
    assert result.runtime_mode == "code_driven"
    assert not result.decision.allowed_actions["generate_plan"]


def test_semantic_retrieval_remains_explicitly_optional():
    class Encoder:
        model_id = "test-only"

        def encode(self, texts):
            return [[1.0, 0.0] for _ in texts]

    policy = IntentCascadePolicy(ExampleVectorIndex(encoder=Encoder()))
    result = asyncio.run(
        IntentDecisionEngine(FakeModelProvider("not-json"), cascade_policy=policy).decide(
            "随便讲讲怎样运动"
        )
    )
    assert result.decision.provenance["semantic_assistance_enabled"]
    assert result.decision.provenance["cascade"]["candidates"]


@pytest.mark.parametrize(
    "payload",
    [
        {"primary_intent": "invented_tool"},
        {"primary_intent": "training_plan", "secondary_intents": "nutrition_advice"},
        {"primary_intent": "training_plan", "risk_level": []},
        {"primary_intent": "training_plan", "needs_clarification": "false"},
    ],
)
def test_host_task_payload_is_validated(payload):
    assert not LLMIntentClassifier._valid_task_payload(payload)


def test_existing_dispatch_is_reused_without_another_model_planner(monkeypatch):
    from fast_api.app.services import coach_agent

    monkeypatch.setattr(
        coach_agent, "get_settings", lambda: SimpleNamespace(code_driven_planner="llm")
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("must reuse dispatch instead of invoking another planner")

    monkeypatch.setattr(coach_agent.LLMPlanner, "plan", forbidden)
    provider = FakeModelProvider(
        json.dumps(
            {
                "primary_intent": "training_plan",
                "secondary_intents": ["nutrition_advice"],
                "risk_level": "low",
            }
        )
    )
    routing = asyncio.run(IntentDecisionEngine(provider).decide("帮我排运动日程，再建议晚饭"))
    route = RuntimeRoute(
        mode=routing.runtime_mode,
        reason=routing.runtime_reason,
        intent_decision=routing.decision.to_dict(),
    )
    coach = object.__new__(CoachAgentService)
    plan, debug = asyncio.run(
        coach._build_code_driven_execution_plan(
            "本轮请求", ToolRegistry(), SimpleNamespace(user_id="test-only"), route
        )
    )
    assert provider.model.calls == 1
    assert debug["dispatch_reused"] is True
    assert debug["llm_planner_raw"] is None
    assert plan.planner_mode == "dispatch_reuse"
    assert plan.intent == "training_plan"


def test_missing_profile_blocks_execution_not_domain_selection():
    provider = FakeModelProvider("not-used")
    result = asyncio.run(
        IntentDecisionEngine(provider).decide("帮我制定一周训练计划", profile=SimpleNamespace())
    )
    assert provider.model.calls == 0
    assert result.decision.provenance["delegated_domains"] == ["training"]
    assert result.decision.needs_clarification
    assert not result.decision.allowed_actions["generate_plan"]


def test_discarded_task_does_not_keep_its_unrelated_missing_fields():
    rule = IntentRouter().from_intent("training_plan")
    rule.missing_slots = ["age", "equipment_available"]
    rule.needs_clarification = True
    decision = LLMIntentClassifier(FakeModelProvider("unused"))._merge_with_rule_decision(
        {"primary_intent": "concept_explanation", "secondary_intents": [], "risk_level": "low"},
        rule,
        authoritative_tasks=True,
    )
    assert not decision.needs_clarification
    assert decision.missing_slots == []


def test_unclear_reference_still_needs_host_clarification():
    provider = FakeModelProvider(
        json.dumps(
            {
                "primary_intent": "general_chat",
                "secondary_intents": [],
                "risk_level": "low",
            }
        )
    )
    result = asyncio.run(IntentDecisionEngine(provider).decide("把第二个改成跑步"))
    assert provider.model.calls == 1
    assert "AMBIGUOUS_REFERENCE" in result.decision.provenance["clarification_reason_codes"]
    assert not result.decision.allowed_actions["generate_plan"]
