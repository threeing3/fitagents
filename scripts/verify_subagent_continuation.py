"""Synthetic scripted continuation on the dedicated acceptance PostgreSQL DB."""

import asyncio
import json
import subprocess
import sys
import uuid
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.domain_subagents import project_read
from fast_api.app.services.subagent_continuation import ContinuableChild
from fast_api.app.services.subagent_journal import JournalCheckpoint, SubagentJournal


def final(ids=None):
    return {
        "action": "final",
        "summary": "候选建议",
        "recommendations": ["先核对时间限制"],
        "uncertainties": ["记录不足"],
        "evidence_ids": ids or [],
    }


class Provider:
    """Scripted provider for this standalone flow; never calls a paid model."""

    settings = SimpleNamespace(chat_model="scripted-continuation")

    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def has_live_model(self):
        return True  # Exercises the protocol with scripted responses, not a live model.

    def chat_model(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(next(self.decisions), ensure_ascii=False))


def reader(domain, tool):
    return project_read(
        {
            "relevant_memories": [
                {"id": "active", "status": "active", "content": "40分钟"},
                {"id": "old", "status": "superseded"},
            ],
            "recent_training": [{"duration": 40}],
            "core_profile": {"goal": "maintenance"},
        },
        domain,
        tool,
    )


async def main():
    engine = create_engine(
        "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"
    )
    if len(sys.argv) == 5 and sys.argv[1] == "--restore-child":
        parent, owner_arg, session_arg = sys.argv[2:]
        owner, session = uuid.UUID(owner_arg), uuid.UUID(session_arg)
        provider = Provider([final(["active"])])
        with Session(engine) as db:
            child, saved_revision = ContinuableChild.restore(
                provider,
                lambda message: reader,
                db=db,
                parent_id=parent,
                owner_id=owner,
                session_id=session,
            )
        assert child.worker.remaining_calls == 8
        checkpoint = JournalCheckpoint(engine)
        checkpoint.revision = saved_revision
        child.attach_checkpoint(checkpoint)
        try:
            revision = child.worker.runtime.children[child.child_id]["revision"]
            await consume(
                child.run(
                    owner, session, "Synthetic restored activation", expected_revision=revision
                )
            )
            assert child.worker.remaining_calls == 7
        finally:
            checkpoint.close()
        engine.dispose()
        return
    owner, session = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add(
            models.User(
                id=owner,
                email=f"continuation-{owner}@example.com",
                password_hash="synthetic-disabled",
            )
        )
        db.flush()
        db.add(
            models.ConversationSession(
                id=session, user_id=owner, title="Synthetic continuation probe"
            )
        )
        db.commit()
    provider = Provider([final(["active"])] * 2)
    child = ContinuableChild(
        provider, lambda message: reader, owner_id=owner, session_id=session, role="training"
    )
    checkpoint = JournalCheckpoint(engine)
    child.attach_checkpoint(checkpoint)
    try:
        await consume(child.run(owner, session, "Synthetic first activation"))
        identity = child.child_id
        checkpoint.close()
        subprocess.run(
            [
                sys.executable,
                __file__,
                "--restore-child",
                child.worker.runtime.parent_id,
                str(owner),
                str(session),
            ],
            check=True,
            timeout=30,
        )
        with Session(engine) as db:
            saved = SubagentJournal(db).load(
                uuid.UUID(child.worker.runtime.parent_id), owner, session
            )
            assert saved["children"][0]["activation"] == 2
            assert saved["children"][0]["status"] == "completed"
            assert saved["children"][0]["child_id"] == identity
            assert saved["_continuation"]["remaining_calls"] == 7
    finally:
        checkpoint.close()
    print(
        json.dumps(
            {
                "status": "passed",
                "same_child_identity": True,
                "activations_saved": 2,
                "scripted_calls": 2,
                "separate_process_restore_verified": True,
                "restored_remaining_calls": 7,
                "real_model_called": False,
                "session_id": str(session),
                "parent_id": child.worker.runtime.parent_id,
                "child_id": identity,
            }
        )
    )
    engine.dispose()


async def consume(stream):
    return [entry async for entry in stream]


if __name__ == "__main__":
    asyncio.run(main())
