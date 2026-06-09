import asyncio
import json
from types import SimpleNamespace

import pytest

from fast_api.app.services.agent_runtime import (
    LLMPlanner,
    PlannerDecision,
    PlannerVerifier,
    ToolRegistry,
    ToolSpec,
)


TOOL_NAMES = [
    "profile.extract",
    "memory.verify",
    "memory.write",
    "context.build",
    "plan.decide",
    "plan.generate",
    "plan.verify",
    "plan.repair",
    "coach.reply",
    "response.verify",
    "response.repair",
    "guardrail.check",
    "response.persist",
]


def tool_specs() -> list[dict]:
    registry = ToolRegistry()
    for name in TOOL_NAMES:
        registry.register(
            ToolSpec(
                name=name,
                description=name,
                permission_level="write" if name in {"memory.write", "response.persist"} else "read",
                side_effects=name in {"memory.write", "response.persist"},
                risk_level="high" if name == "guardrail.check" else "low",
                idempotency_key_fields=["message_id"] if name in {"memory.write", "response.persist"} else [],
            ),
            lambda _payload: {},
        )
    return registry.list_specs()


class FakeChatModel:
    def __init__(self, payload: dict):
        self.payload = payload

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


class FakeModelProvider:
    def __init__(self, payload: dict | None = None):
        self.payload = payload

    def chat_model(self, temperature: float = 0.0):
        if self.payload is None:
            return None
        return FakeChatModel(self.payload)


def run(coro):
    return asyncio.run(coro)


def test_llm_planner_parses_valid_plan_and_verifier_preserves_order():
    payload = {
        "intent": "training_log",
        "selected_tools": ["profile.extract", "memory.verify", "memory.write", "context.build", "coach.reply"],
        "skipped_tools": [{"tool": "plan.generate", "reason": "not requested"}],
        "tool_order": ["profile.extract", "memory.verify", "memory.write", "context.build", "coach.reply", "guardrail.check", "response.persist"],
        "required_context": ["profile", "memory", "training_history"],
        "write_intent": True,
        "safety_level": "low",
        "plan_generation_allowed": False,
        "reasoning_summary": "记录卧推训练表现，并基于上下文给建议。",
    }

    decision = run(LLMPlanner(FakeModelProvider(payload)).plan("我今天卧推55kg做了3x5", tool_specs()))
    verified = PlannerVerifier().verify_and_repair(decision, tool_specs(), "我今天卧推55kg做了3x5")
    plan = verified.to_execution_plan("我今天卧推55kg做了3x5")

    assert plan.planner_mode == "llm"
    assert plan.intent == "training_log"
    assert plan.steps[-1].tool_name == "response.persist"
    assert "plan.generate" not in [step.tool_name for step in plan.steps]


def test_planner_verifier_rejects_unknown_tool_for_fallback():
    decision = PlannerDecision(
        intent="general_chat",
        selected_tools=["context.build", "unknown.tool"],
        tool_order=["context.build", "unknown.tool", "response.persist"],
        reasoning_summary="Bad tool should be rejected.",
    )

    with pytest.raises(ValueError, match="unknown tools"):
        PlannerVerifier().verify_and_repair(decision, tool_specs(), "你好")


def test_planner_verifier_repairs_memory_write_order():
    decision = PlannerDecision(
        intent="profile_update",
        selected_tools=["memory.write", "memory.verify", "coach.reply", "response.persist"],
        tool_order=["memory.write", "memory.verify", "coach.reply", "response.persist"],
        write_intent=True,
    )

    verified = PlannerVerifier().verify_and_repair(decision, tool_specs(), "我的目标改成减脂")

    assert verified.tool_order.index("memory.verify") < verified.tool_order.index("memory.write")
    assert "ensure_memory_verify_before_write" in verified.repair_actions


def test_planner_verifier_inserts_guardrail_for_risk_request():
    decision = PlannerDecision(
        intent="injury_or_risk",
        selected_tools=["context.build", "coach.reply", "response.persist"],
        tool_order=["context.build", "coach.reply", "response.persist"],
        safety_level="low",
    )

    verified = PlannerVerifier().verify_and_repair(decision, tool_specs(), "胸口闷还能练吗")

    assert verified.safety_level == "high"
    assert "guardrail.check" in verified.tool_order
    assert verified.tool_order[-1] == "response.persist"


def test_planner_verifier_adds_plan_verify_after_plan_generate():
    decision = PlannerDecision(
        intent="training_plan",
        selected_tools=["context.build", "plan.generate", "coach.reply", "response.persist"],
        tool_order=["context.build", "plan.generate", "coach.reply", "response.persist"],
        plan_generation_allowed=True,
    )

    verified = PlannerVerifier().verify_and_repair(decision, tool_specs(), "帮我制定一周训练计划")

    assert verified.plan_generation_allowed is True
    assert verified.tool_order.index("plan.generate") < verified.tool_order.index("plan.verify")
    assert verified.tool_order[-1] == "response.persist"


def test_planner_verifier_removes_unrequested_plan_generation():
    decision = PlannerDecision(
        intent="training_log",
        selected_tools=["context.build", "plan.generate", "coach.reply", "response.persist"],
        tool_order=["context.build", "plan.generate", "plan.verify", "coach.reply", "response.persist"],
        plan_generation_allowed=True,
    )

    verified = PlannerVerifier().verify_and_repair(decision, tool_specs(), "我今天卧推55kg做了3x5")

    assert verified.plan_generation_allowed is False
    assert "plan.generate" not in verified.tool_order
    assert "plan.verify" not in verified.tool_order


def test_planner_verifier_canonicalizes_host_tool_order_for_execution_loop():
    decision = PlannerDecision(
        intent="training_plan",
        selected_tools=["coach.reply", "plan.generate", "memory.write", "context.build", "response.persist"],
        tool_order=["coach.reply", "plan.generate", "memory.write", "context.build", "response.persist"],
        plan_generation_allowed=True,
        write_intent=True,
    )

    verified = PlannerVerifier().verify_and_repair(decision, tool_specs(), "帮我制定一周训练计划")

    expected_order = [
        "profile.extract",
        "memory.verify",
        "memory.write",
        "context.build",
        "plan.decide",
        "plan.generate",
        "plan.verify",
        "coach.reply",
        "response.verify",
        "guardrail.check",
        "response.persist",
    ]
    for tool_name in expected_order:
        assert tool_name in verified.tool_order
    assert [tool for tool in verified.tool_order if tool in expected_order] == expected_order
    assert "canonicalize_host_tool_order" in verified.repair_actions
