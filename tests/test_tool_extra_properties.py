import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.llm_agent import LLMAgentService


@pytest.mark.parametrize("kind", ["top", "nested", "empty_nested", "array"])
def test_explicit_extra_property_rejection_prevents_handler_execution(kind):
    closed = {"type": "object", "properties": {}, "additionalProperties": False}
    if kind == "top":
        schema, payload = closed, {"session_ref": "invented"}
    elif kind == "nested":
        schema = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "properties": {"known": {"type": "string"}},
                    "additionalProperties": False,
                }
            },
        }
        payload = {"options": {"known": "ok", "foreign": "bad"}}
    elif kind == "empty_nested":
        schema = {"type": "object", "properties": {"options": closed}}
        payload = {"options": {"foreign": "bad"}}
    else:
        schema = {"type": "object", "properties": {"items": {"type": "array", "items": closed}}}
        payload = {"items": [{"foreign": "bad"}]}
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="context.build", description="test", input_schema=schema),
        lambda inputs: calls.append(inputs) or {},
    )
    result = asyncio.run(registry.execute("context.build", payload))
    assert result.status == "schema_error"
    assert result.attempts == 0
    assert result.validation_errors
    assert not calls
    assert registry.successful_call("context.build") is None


@pytest.mark.parametrize("extra_policy", [None, True])
def test_open_schemas_remain_compatible(extra_policy):
    schema = {"type": "object", "properties": {}}
    if extra_policy is not None:
        schema["additionalProperties"] = extra_policy
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read", description="test", input_schema=schema), lambda p: p)
    assert asyncio.run(registry.execute("read", {"legacy": "value"})).status == "success"


def test_valid_closed_nested_input_still_executes():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read",
            description="test",
            input_schema={
                "type": "object",
                "properties": {
                    "options": {"type": "object", "properties": {}, "additionalProperties": False}
                },
                "additionalProperties": False,
            },
        ),
        lambda _: {},
    )
    assert asyncio.run(registry.execute("read", {"options": {}})).status == "success"


def test_actual_model_loop_can_recover_without_silent_argument_stripping():
    calls, observed = [], []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="context.build",
            description="No arguments",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        lambda inputs: calls.append(dict(inputs)) or {"session_ref": "observed-ref"},
    )

    class Model:
        replies = iter(
            [
                '<tool_call>{"name":"context.build","input":{"session_ref":"invented"}}</tool_call>',
                '<tool_call>{"name":"context.build","input":{}}</tool_call>',
                "查询完成",
            ]
        )

        async def ainvoke(self, messages):
            observed.append([str(m.content) for m in messages])
            return SimpleNamespace(content=next(self.replies))

    model = Model()
    provider = SimpleNamespace(
        settings=SimpleNamespace(chat_model="unknown", llm_provider="scripted"),
        chat_model=lambda **_: model,
    )
    agent = LLMAgentService(
        None,
        provider,
        registry,
        uuid4(),
        uuid4(),
        SimpleNamespace(injuries=[], goal="maintenance"),
        "读取当前上下文",
    )
    result = asyncio.run(agent.run())
    assert result.error is None
    assert calls == [{}]
    assert [t["status"] for t in result.tool_calls] == ["schema_error", "success"]
    assert any("additional property not allowed" in text for text in observed[1])
    assert result.tool_calls[0]["input"] == {"session_ref": "invented"}
