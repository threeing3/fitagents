"""Exercise final invocation boundaries without network calls or quota spend."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from fast_api.app.core.config import Settings
from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.llm_agent import LLMAgentService
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.prompt_budget import PromptBudgetExceeded, enforce_prompt_budget


@pytest.mark.parametrize(
    "system,user",
    [("x" * 40000, "brief"), ("system", "中" * 3000)],
    ids=["large_system", "unicode_user"],
)
def test_full_system_and_unicode_input_counted_without_text_leak(system, user):
    with pytest.raises(PromptBudgetExceeded) as captured:
        enforce_prompt_budget(
            "unknown", [SystemMessage(content=system), HumanMessage(content=user)]
        )
    report = captured.value.report
    assert report["input_units"] > report["input_limit"]
    assert report["output_reserve"] >= 1200
    assert system not in str(captured.value)
    assert user not in str(captured.value)


@pytest.mark.parametrize("stream", [False, True])
def test_oversized_complete_prompt_rejects_before_model_creation(monkeypatch, stream):
    provider = ModelProvider(Settings(_env_file=None, LLM_PROVIDER="offline"))
    calls = []
    monkeypatch.setattr(provider, "chat_model", lambda **kwargs: calls.append(kwargs))

    async def run():
        if stream:
            return [chunk async for chunk in provider.stream_coach_reply("system", "x" * 40000)]
        return await provider.coach_reply("system", "x" * 40000)

    with pytest.raises(ValueError, match="prompt budget"):
        asyncio.run(run())
    assert calls == []


@pytest.mark.parametrize("stream", [False, True])
def test_normal_prompt_still_reaches_model_unchanged(monkeypatch, stream):
    provider = ModelProvider(Settings(_env_file=None, LLM_PROVIDER="offline"))
    received = []

    class Model:
        async def ainvoke(self, messages):
            received.append(messages)
            return AIMessage(content="正常结果")

        async def astream(self, messages):
            received.append(messages)
            yield AIMessage(content="正常结果")

    monkeypatch.setattr(provider, "chat_model", lambda **kwargs: Model())

    async def run():
        if stream:
            return "".join(
                [chunk async for chunk in provider.stream_coach_reply("system", "用户问题")]
            )
        return await provider.coach_reply("system", "用户问题")

    assert asyncio.run(run()) == "正常结果"
    assert [message.content for message in received[0]] == ["system", "用户问题"]


@pytest.mark.parametrize("iterations", [1, 2])
def test_large_tool_observation_stops_next_or_final_call(monkeypatch, iterations):
    monkeypatch.setattr("fast_api.app.services.llm_agent.MAX_ITERATIONS", iterations)
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="training.log.read", description="synthetic read"),
        lambda payload: {"history": "x" * 40000},
    )
    calls = []

    class Model:
        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(
                content='<tool_call>{"name":"training.log.read","input":{}}</tool_call>'
            )

    agent = LLMAgentService(
        None,
        SimpleNamespace(
            settings=SimpleNamespace(chat_model="unknown", llm_provider="scripted"),
            chat_model=lambda **kwargs: Model(),
        ),
        registry,
        uuid4(),
        uuid4(),
        SimpleNamespace(injuries=[], goal="maintenance"),
        "查询训练记录",
    )
    result = asyncio.run(agent.run())
    assert len(calls) == 1
    assert result.error and "prompt budget" in result.error
    assert result.iterations == 1
    assert any(node.get("node") == "PromptBudgetRejected" for node in result.nodes)
