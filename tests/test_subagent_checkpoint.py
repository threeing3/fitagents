"""Lifecycle checkpoint adapter integration, without network/provider calls."""

import asyncio
import copy

import pytest

from fast_api.app.services.domain_subagents import DomainSubagents
from fast_api.app.services.subagent_journal import JournalCheckpoint
from fast_api.app.services.subagent_runtime import SubagentError
from tests.test_domain_subagents import Provider, collect, final, reader, result


def test_every_transition_has_checkpoint_before_public_success():
    manager = DomainSubagents(Provider([final()]), reader)
    snapshots = []
    manager.runtime.checkpoint = lambda runtime: snapshots.append(copy.deepcopy(runtime.children))
    rows = result(asyncio.run(collect(manager)))
    assert rows[0]["status"] == "completed"
    assert [next(iter(snapshot.values()))["status"] for snapshot in snapshots] == [
        "pending",
        "running",
        "completed",
    ]


def test_checkpoint_failure_prevents_model_call():
    manager = DomainSubagents(Provider([final()]), reader)

    def unavailable(runtime):
        raise SubagentError("checkpoint_failed")

    manager.runtime.checkpoint = unavailable
    with pytest.raises(SubagentError, match="checkpoint_failed"):
        asyncio.run(collect(manager))
    assert manager.provider.calls == 0
    assert not manager._active


def test_consumer_close_checkpoints_terminal_state():
    async def run():
        manager = DomainSubagents(Provider([final()]), reader)
        statuses = []
        manager.runtime.checkpoint = lambda runtime: statuses.append(
            next(iter(runtime.children.values()))["status"]
        )
        stream = manager.run({"intent": "training_plan"}, "synthetic")
        await anext(stream)
        await stream.aclose()
        assert statuses == ["pending", "failed"]

    asyncio.run(run())


def test_non_independent_database_backend_is_not_silently_used():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    with pytest.raises(SubagentError, match="independent_journal_backend_unsupported"):
        JournalCheckpoint(engine)
    engine.dispose()
