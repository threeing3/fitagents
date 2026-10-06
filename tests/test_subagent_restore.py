"""Cross-manager restoration with durable SQLite fixture; no live model claims."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy.orm import Session

from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.subagent_continuation import ContinuableChild
from fast_api.app.services.subagent_journal import SubagentJournal
from fast_api.app.services.subagent_runtime import SubagentError
from tests import test_subagent_journal
from tests.test_domain_subagents import Provider, final, reader

journal_db = test_subagent_journal.journal_db


class SyntheticCheckpoint:
    def __init__(self, engine, revision=0):
        self.engine, self.revision = engine, revision

    def __call__(self, runtime):
        with Session(self.engine) as db:
            saved = SubagentJournal(db).save(runtime, expected_revision=self.revision)
            db.commit()
            self.revision = saved["revision"]


async def collect(child, owner, session, version=None):
    return [
        entry async for entry in child.run(owner, session, "synthetic", expected_revision=version)
    ]


def test_new_manager_restores_budget_identity_and_private_handoff(journal_db):
    engine, owner, session, other = journal_db
    first = ContinuableChild(
        Provider([final(["active"])]),
        lambda message: reader,
        owner_id=owner,
        session_id=session,
        role="training",
    )
    first.attach_checkpoint(SyntheticCheckpoint(engine))
    asyncio.run(collect(first, owner, session))
    parent = uuid.UUID(first.worker.runtime.parent_id)
    with Session(engine) as db:
        assert "_continuation" not in SubagentJournal(db).list_catalogs(owner, session)[0]
        assert "_continuation" not in str(CoachAgentService(db, None).agent_run(parent)["nodes"])
        with pytest.raises(SubagentError, match="scope_mismatch"):
            ContinuableChild.restore(
                Provider(),
                lambda message: reader,
                db=db,
                parent_id=parent,
                owner_id=other,
                session_id=session,
            )
        provider = Provider([final(["active"])])
        restored, catalog_revision = ContinuableChild.restore(
            provider,
            lambda message: reader,
            db=db,
            parent_id=parent,
            owner_id=owner,
            session_id=session,
        )
    assert restored.worker.remaining_calls == 8
    restored.attach_checkpoint(SyntheticCheckpoint(engine, catalog_revision))
    version = restored.worker.runtime.children[restored.child_id]["revision"]
    asyncio.run(collect(restored, owner, session, version))
    assert restored.child_id == first.child_id
    assert restored.worker.remaining_calls == 7
    assert json.loads(provider.messages[0][-1].content)["handoff"][0]["child_id"] == first.child_id


def test_incomplete_delivery_cannot_restore_even_if_child_completed(journal_db):
    engine, owner, session, _ = journal_db
    child = ContinuableChild(
        Provider([final()]),
        lambda message: reader,
        owner_id=owner,
        session_id=session,
        role="training",
    )
    child.attach_checkpoint(SyntheticCheckpoint(engine))

    async def stop_before_delivery():
        stream = child.run(owner, session, "synthetic")
        async for entry in stream:
            if entry.get("name") == "subagent.result" and entry["status"] == "completed":
                break
        await stream.aclose()

    asyncio.run(stop_before_delivery())
    with Session(engine) as db:
        with pytest.raises(SubagentError, match="continuation_not_ready"):
            ContinuableChild.restore(
                Provider(),
                lambda message: reader,
                db=db,
                parent_id=uuid.UUID(child.worker.runtime.parent_id),
                owner_id=owner,
                session_id=session,
            )


def test_budget_is_persisted_before_model_invoke(journal_db):
    engine, owner, session, _ = journal_db
    provider = Provider()
    child = ContinuableChild(
        provider, lambda message: reader, owner_id=owner, session_id=session, role="training"
    )
    child.attach_checkpoint(SyntheticCheckpoint(engine))

    async def crash(_messages):
        with Session(engine) as db:
            saved = SubagentJournal(db).load(
                uuid.UUID(child.worker.runtime.parent_id), owner, session
            )
            assert saved["_continuation"]["remaining_calls"] == 8
        raise RuntimeError("synthetic transport failure")

    provider.ainvoke = crash
    asyncio.run(collect(child, owner, session))
    with Session(engine) as db:
        saved = SubagentJournal(db).load(uuid.UUID(child.worker.runtime.parent_id), owner, session)
        assert saved["_continuation"]["remaining_calls"] == 8
        assert saved["children"][0]["status"] == "failed"
