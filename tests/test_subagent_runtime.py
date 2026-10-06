"""Lifecycle and hostile protocol tests, all synthetic and no network calls."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from fast_api.app.services.domain_subagents import DomainSubagents
from fast_api.app.services.subagent_runtime import (
    SubagentError,
    SubagentRuntime,
    parse_child_response,
)
from tests.test_domain_subagents import Provider, collect, final, reader, result


def test_start_capabilities_depth_and_scoped_catalog():
    runtime = SubagentRuntime()
    runtime.bind("owner", "session")
    for arguments in ({"mode": "continuable"}, {"depth": 2}, {"depth": 0}):
        with pytest.raises(SubagentError):
            runtime.start("training", **arguments)
    assert runtime.children == {}
    child = runtime.start("training")
    runtime.transition(child["child_id"], "running")
    with pytest.raises(SubagentError, match="scope_mismatch"):
        runtime.catalog(owner_id="other", session_id="session")
    catalog = runtime.catalog(owner_id="owner", session_id="session")
    catalog[0]["status"] = "completed"
    assert runtime.children[child["child_id"]]["status"] == "running"
    runtime.transition(child["child_id"], "completed")
    with pytest.raises(SubagentError, match="terminal_child"):
        runtime.transition(child["child_id"], "running")
    runtime.invalidate(child["child_id"])
    assert runtime.catalog(owner_id="owner", session_id="session")[0]["revision"] == 4


@pytest.mark.parametrize(
    "content",
    [
        '{"action":"read","action":"final"}',
        '{"action":NaN}',
        "[]",
        "```json\n{}\n```",
        "x" * 32769,
        [{"text": "{}"}],
    ],
    ids=["duplicate-key", "nan", "array-root", "fenced", "oversized", "content-block"],
)
def test_strict_response_parser(content):
    with pytest.raises(SubagentError):
        parse_child_response(content)


def test_generator_close_marks_pending_child_terminal():
    async def run():
        manager = DomainSubagents(Provider([final()]), reader)
        stream = manager.run({"intent": "training_plan"}, "test")
        entry = await anext(stream)
        assert entry["status"] == "pending"
        await stream.aclose()
        child = manager.runtime.catalog()[0]
        assert child["status"] == "failed"
        assert child["failure_reason"] == "consumer_closed"
        assert manager.provider.calls == 0

    asyncio.run(run())


def test_cancellation_reaches_inflight_model_and_no_later_child_starts():
    async def run():
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        provider = Provider()

        async def slow(_messages):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        provider.ainvoke = slow
        manager = DomainSubagents(provider, reader)
        task = asyncio.create_task(collect(manager, {"intent": "weekly_review"}))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        catalog = manager.runtime.catalog()
        assert len(catalog) == 1
        assert catalog[0]["failure_reason"] == "parent_cancelled"

    asyncio.run(run())


def test_rejected_concurrent_activation_does_not_cancel_original():
    async def run():
        manager = DomainSubagents(Provider([final()]), reader)
        stream = manager.run({"intent": "training_plan"}, "one")
        await anext(stream)
        with pytest.raises(ValueError, match="concurrent_activation_not_supported"):
            await collect(manager)
        assert manager.runtime.catalog()[0]["status"] == "pending"
        remaining = [entry async for entry in stream]
        assert result(remaining)[0]["status"] == "completed"

    asyncio.run(run())


def test_repeated_read_cannot_erase_original_observation():
    reads = 0

    def changed(role, tool):
        nonlocal reads
        value = reader(role, tool)
        if tool == "memory.recall":
            reads += 1
            if reads > 1:
                value = {"memories": []}
        return value

    provider = Provider([{"action": "read", "tool": "memory.recall"}, final()])
    rows = result(asyncio.run(collect(DomainSubagents(provider, changed))))
    assert rows[0]["failure_reason"] == "evidence_changed"
    assert provider.calls == 1


def test_response_shape_violation_has_safe_reason_and_terminal_catalog():
    provider = Provider()

    async def malformed(_messages):
        return SimpleNamespace(content='{"action":"read","action":"final"}')

    provider.ainvoke = malformed
    manager = DomainSubagents(provider, reader)
    rows = result(asyncio.run(collect(manager)))
    assert rows[0]["failure_reason"] == "duplicate_json_key"
    assert manager.runtime.catalog()[0]["status"] == "failed"


def test_model_timeout_is_classified_and_cancelled():
    async def run():
        provider = Provider()

        async def slow(_messages):
            await asyncio.sleep(5)
            return SimpleNamespace(content=json.dumps(final()))

        provider.ainvoke = slow
        manager = DomainSubagents(provider, reader)
        manager.deadline = time.monotonic() + 0.05
        rows = result(await collect(manager))
        assert rows[0]["failure_reason"] == "model_timeout"
        assert rows[0]["model_calls"] == 1

    asyncio.run(run())


def test_invalid_role_batch_has_no_partial_creation_or_model_calls():
    manager = DomainSubagents(Provider(), reader)

    async def run():
        return [entry async for entry in manager.run({}, "test", roles=["training", "unknown"])]

    with pytest.raises(ValueError, match="invalid_roles"):
        asyncio.run(run())
    assert manager.runtime.catalog() == []


def test_child_count_limit_and_rebinding_are_checked_before_activation():
    runtime = SubagentRuntime(max_children=1)
    child = runtime.start("training")
    runtime.transition(child["child_id"], "skipped")
    with pytest.raises(SubagentError, match="child_limit"):
        runtime.start("recovery")
    with pytest.raises(SubagentError, match="invalid_scope_binding"):
        runtime.bind("other", "session")
    assert len(runtime.children) == 1


def test_empty_list_entry_is_not_a_valid_structured_claim():
    decision = final()
    decision["recommendations"] = [""]
    rows = result(asyncio.run(collect(DomainSubagents(Provider([decision]), reader))))
    assert rows[0]["failure_reason"] == "invalid_result"
