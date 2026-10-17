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


def test_runtime_checkpoint_metadata_survives_model_message_roundtrip(monkeypatch):
    snapshots = []

    async def compact(db, provider, owner, session, run, surface):
        snapshots.append(surface)
        if len(snapshots) == 1:
            return [
                *surface[:2],
                {
                    "id": "saved-checkpoint",
                    "role": "user",
                    "content": "confirmed constraints",
                    "context_checkpoint": True,
                    "source_message_ids": ["old-source"],
                },
            ]
        return surface

    monkeypatch.setattr("fast_api.app.services.session_compaction.compact_runtime_context", compact)
    agent = LLMAgentService(
        None,
        SimpleNamespace(settings=SimpleNamespace(chat_model="unknown")),
        ToolRegistry(),
        uuid4(),
        uuid4(),
        SimpleNamespace(),
        "review",
    )

    async def run():
        messages = await agent._compact_runtime_messages(
            [SystemMessage(content="rules"), HumanMessage(content="review")]
        )
        await agent._compact_runtime_messages(messages)

    asyncio.run(run())
    assert snapshots[1][2]["context_checkpoint"]
    assert snapshots[1][2]["source_message_ids"] == ["old-source"]


@pytest.mark.parametrize(
    "code,recover", [("context_length_exceeded", True), ("rate_limit_exceeded", False)]
)
def test_provider_overflow_recovers_once_without_tool_replay(code, recover):
    calls = []

    class Failure(Exception):
        pass

    class Model:
        async def ainvoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                error = Failure("synthetic failure")
                error.code = code
                raise error
            return AIMessage(content="done")

    reservations = []

    def model(**kwargs):
        reservations.append(kwargs)
        return Model()

    agent = LLMAgentService(
        None,
        SimpleNamespace(settings=SimpleNamespace(chat_model="unknown"), chat_model=model),
        ToolRegistry(),
        uuid4(),
        uuid4(),
        SimpleNamespace(),
        "review",
    )
    triggers = []

    async def compact(messages, *, trigger="pressure"):
        triggers.append(trigger)
        return [SystemMessage(content="rules"), HumanMessage(content="compressed")]

    agent._compact_runtime_messages = compact

    async def run():
        return await agent._invoke_with_context_recovery(
            Model(), [HumanMessage(content="original")]
        )

    if recover:
        result, _ = asyncio.run(run())
        assert result.content == "done"
        assert len(calls) == 2 and len(reservations) == 1
        assert triggers == ["context-overflow"]
    else:
        with pytest.raises(Failure):
            asyncio.run(run())
        assert len(calls) == 1 and not reservations and not triggers


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
