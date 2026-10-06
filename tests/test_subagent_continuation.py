"""Continuable kernel tests, scripted provider and mutable synthetic evidence."""

import asyncio
import json

import pytest

from fast_api.app.services.subagent_continuation import ContinuableChild
from fast_api.app.services.subagent_runtime import SubagentError
from tests.test_domain_subagents import Provider, final, reader


async def turn(child, message="synthetic", revision=None, owner="owner"):
    return [
        entry async for entry in child.run(owner, "session", message, expected_revision=revision)
    ]


def make_child(provider, factory=None):
    return ContinuableChild(
        provider,
        factory or (lambda message: reader),
        owner_id="owner",
        session_id="session",
        role="training",
    )


def revision(child):
    return child.worker.runtime.children[child.child_id]["revision"]


def test_same_identity_new_evidence_handoff_and_activation_limit():
    async def run():
        provider = Provider([final(["active"])] * 3)
        child = make_child(provider)
        first = await turn(child)
        identity = child.child_id
        await turn(child, "next", revision(child))
        assert child.child_id == identity
        assert json.loads(provider.messages[1][-1].content)["handoff"][0]["child_id"] == identity
        assert first[-1]["results"][0]["mode"] == "continuable"
        await turn(child, "third", revision(child))
        with pytest.raises(SubagentError, match="activation_limit"):
            await turn(child, "fourth", revision(child))
        assert provider.calls == 3

    asyncio.run(run())


def test_corrected_evidence_is_not_sent_as_old_handoff():
    async def run():
        data = {"corrected": False}

        def factory(message):
            def read(role, tool):
                value = reader(role, tool)
                if data["corrected"] and tool == "memory.recall":
                    value["memories"][0]["content"] = "20分钟"
                return value

            return read

        provider = Provider([final(["active"])] * 2)
        child = make_child(provider, factory)
        await turn(child)
        data["corrected"] = True
        entries = await turn(child, "corrected", revision(child))
        payload = json.loads(provider.messages[1][-1].content)
        assert payload["handoff"] == []
        assert payload["memory"]["memories"][0]["content"] == "20分钟"
        assert entries[0]["name"] == "subagent.handoff"

    asyncio.run(run())


def test_owner_and_stale_revision_rejected_without_calls():
    async def run():
        provider = Provider([final()] * 2)
        child = make_child(provider)
        await turn(child)
        for owner, version, reason in [
            ("other", revision(child), "scope_mismatch"),
            ("owner", 1, "checkpoint_conflict"),
        ]:
            with pytest.raises(SubagentError, match=reason):
                await turn(child, revision=version, owner=owner)
        assert provider.calls == 1

    asyncio.run(run())


def test_failed_or_closed_child_is_not_automatically_retried():
    async def run():
        child = make_child(Provider([{"action": "read", "tool": "write"}, final()]))
        await turn(child)
        with pytest.raises(SubagentError, match="child_not_ready"):
            await turn(child, revision=revision(child))
        assert child.worker.provider.calls == 1

    asyncio.run(run())


def test_session_budget_is_not_reset_on_next_activation():
    async def run():
        provider = Provider([{"action": "read", "tool": "records.read"}, final()] * 2)
        child = make_child(provider)
        child.worker.remaining_calls = 2
        await turn(child)
        with pytest.raises(SubagentError, match="shared_budget_exhausted"):
            await turn(child, revision=revision(child))
        assert provider.calls == 2

    asyncio.run(run())


def test_consumer_close_blocks_continuation_instead_of_retrying():
    async def run():
        child = make_child(Provider([final()]))
        stream = child.run("owner", "session", "synthetic")
        await anext(stream)
        await stream.aclose()
        assert child.worker.runtime.children[child.child_id]["failure_reason"] == "consumer_closed"
        with pytest.raises(SubagentError, match="child_not_ready"):
            await turn(child, revision=revision(child))
        assert child.worker.provider.calls == 0

    asyncio.run(run())
