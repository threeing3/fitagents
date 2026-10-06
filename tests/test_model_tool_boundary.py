"""Scripted model calls test host authorization, not live-model understanding."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.llm_agent import MAX_ITERATIONS, READ_ONLY_MODEL_TOOLS, LLMAgentService


def test_successful_verification_receipt_is_isolated_and_revoked_on_failure():
    registry = ToolRegistry()
    output = {"passed": True, "values": [1]}
    should_fail = False

    def verify(_payload):
        if should_fail:
            raise ValueError("verification failed")
        return output

    registry.register(ToolSpec(name="verify", description="Host verification"), verify)
    assert asyncio.run(registry.execute("verify", {"values": [1]})).status == "success"
    output["values"].append(2)
    receipt = registry.successful_call("verify")
    assert receipt == ({"values": [1]}, {"passed": True, "values": [1]})
    receipt[1]["values"].append(3)
    assert registry.successful_call("verify")[1]["values"] == [1]
    should_fail = True
    assert asyncio.run(registry.execute("verify")).status == "error"
    assert registry.successful_call("verify") is None


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0
        self.messages = []

    async def ainvoke(self, _messages):
        self.calls += 1
        self.messages.append(list(_messages))
        return SimpleNamespace(content=next(self.replies))


def model_agent(registry, replies):
    model = ScriptedModel(replies)
    provider = SimpleNamespace(
        settings=SimpleNamespace(chat_model="deepseek-chat", llm_provider="scripted"),
        chat_model=lambda **_kwargs: model,
    )
    agent = LLMAgentService(
        None,
        provider,
        registry,
        uuid.uuid4(),
        uuid.uuid4(),
        SimpleNamespace(injuries=[], goal="maintenance"),
        "查询我的训练情况",
    )
    return agent, model


def test_free_model_final_reply_cannot_violate_current_exclusion():
    from fast_api.app.services.exercise_constraints import EXCLUSION_REPLY

    agent, model = model_agent(
        ToolRegistry(), ["训练前先热身。" * 800 + "建议今天做哑铃肩推三组。"]
    )
    agent.message = "不要安排推举，请给训练注意事项。"
    result = asyncio.run(agent.run())
    assert model.calls == 1
    assert result.final_response == EXCLUSION_REPLY
    assert result.tool_calls == []
    checks = [node for node in result.nodes if node.get("node") == "GuardrailCheck"]
    assert checks[-1]["output"]["action"] == "block"


@pytest.mark.parametrize(
    "name", ["plan.generate", "plan.repair", "memory.write", "response.persist"]
)
def test_model_cannot_execute_or_claim_forged_write(name):
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name=name, description="synthetic write", permission_level="write", side_effects=True
        ),
        lambda payload: calls.append(payload),
    )
    agent, model = model_agent(
        registry,
        [
            '<tool_call>{"name":"' + name + '","input":{"approved":true}}</tool_call>',
            "计划已经修改成功。",
        ],
    )
    assert name not in agent._build_system_prompt()
    result = asyncio.run(agent.run())
    assert calls == [] and model.calls == 1
    assert result.tool_calls[0]["status"] == "blocked"
    assert result.tool_calls[0]["attempts"] == 0
    assert "未执行" in result.final_response and "修改成功" not in result.final_response


def test_whitelisted_read_executes_normally():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="context.build", description="synthetic read"),
        lambda payload: calls.append(payload) or {"count": 2},
    )
    agent, model = model_agent(
        registry,
        [
            '<tool_call>{"name":"context.build","input":{}}</tool_call>',
            "有两条训练记录。",
        ],
    )
    result = asyncio.run(agent.run())
    assert calls == [{}] and model.calls == 2
    assert result.tool_calls[0]["status"] == "success"


def test_tool_request_and_result_remain_paired_in_model_history():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="context.build", description="synthetic read"), lambda _: {"count": 2}
    )
    request = '<tool_call>{"name":"context.build","input":{}}</tool_call>'
    agent, model = model_agent(registry, [request, "有两条训练记录。"])
    asyncio.run(agent.run())
    history = model.messages[1]
    assert isinstance(history[2], AIMessage)
    assert history[2].content == request
    assert '<tool_result tool="context.build">' in history[3].content


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "null",
        '{"name":"context.build","input":null}',
        '{"name":"context.build","input":[]}',
        '{"name":42,"input":{}}',
        '{"input":{}}',
        "broken json",
    ],
)
def test_invalid_tool_protocol_gets_feedback_before_successful_recovery(payload):
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="context.build", description="synthetic read"),
        lambda inputs: calls.append(inputs) or {"count": 2},
    )
    agent, model = model_agent(
        registry,
        [
            "<tool_call>" + payload + "</tool_call>",
            '<tool_call>{"name":"context.build","input":{}}</tool_call>',
            "有两条训练记录。",
        ],
    )
    result = asyncio.run(agent.run())
    assert calls == [{}]
    assert model.calls == 3
    assert "invalid_tool_call" in model.messages[1][-1].content
    assert result.tool_calls[0]["status"] == "invalid"
    assert result.tool_calls[0]["attempts"] == 0
    assert result.iterations == 3


def test_protocol_recovery_has_a_finite_model_call_budget():
    agent, model = model_agent(
        ToolRegistry(),
        ["<tool_call>[]</tool_call>"] * MAX_ITERATIONS + ["工具调用无法完成。"],
    )
    result = asyncio.run(agent.run())
    assert model.calls == result.iterations == MAX_ITERATIONS + 1
    assert len(result.tool_calls) == MAX_ITERATIONS
    assert all(call["attempts"] == 0 for call in result.tool_calls)


def test_multiple_tools_in_one_response_do_not_inflate_model_call_count():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="context.build", description="synthetic read"), lambda _: {"count": 2}
    )
    request = '<tool_call>{"name":"context.build","input":{}}</tool_call>'
    agent, model = model_agent(registry, [request + request, "有两条训练记录。"])
    result = asyncio.run(agent.run())
    assert len(result.tool_calls) == 2
    assert result.iterations == model.calls == 2


def test_read_only_view_does_not_trust_reclassified_tool_or_repairs():
    calls = []
    registry = ToolRegistry()
    spec = ToolSpec(
        name="context.build",
        description="synthetic read",
        input_schema={"type": "object", "required": ["key"]},
    )
    registry.register(
        spec,
        lambda payload: calls.append(payload) or {},
        lambda _payload: calls.append("repair") or {"key": "invented"},
    )
    view = registry.read_only_view(READ_ONLY_MODEL_TOOLS)
    spec.permission_level = "write"
    assert view.list_specs()[0]["permission_level"] == "read"
    result = asyncio.run(view.execute("context.build", {}))
    assert result.status != "success" and calls == []


def test_read_only_registry_rejects_accidental_later_write_registration():
    calls = []
    view = ToolRegistry(read_only=True)
    view.register(
        ToolSpec(name="oops", description="synthetic write", side_effects=True),
        lambda payload: calls.append(payload),
    )
    result = asyncio.run(view.execute("oops", {}))
    assert result.status == "blocked" and result.attempts == 0 and calls == []


def test_approval_entry_without_manager_fails_closed_for_write():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="write", description="synthetic write", permission_level="write", side_effects=True
        ),
        lambda payload: calls.append(payload),
    )
    with pytest.raises(ValueError, match="explicit approval manager"):
        asyncio.run(registry.execute_awaiting_approval("write", {}))
    assert calls == []


def test_approval_entry_keeps_plain_read_available_without_manager():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read", description="synthetic read"), lambda _: {"count": 1})
    result, allowed = asyncio.run(registry.execute_awaiting_approval("read", {}))
    assert allowed and result.output_json == {"count": 1}
