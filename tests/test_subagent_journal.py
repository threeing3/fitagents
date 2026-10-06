"""Durability across SQL sessions, scope isolation and optimistic concurrency."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.subagent_journal import SubagentJournal, public_catalog
from fast_api.app.services.subagent_runtime import SubagentError, SubagentRuntime


def test_public_continuation_hint_requires_ready_budget_and_completed_child():
    value = {
        "children": [{"status": "completed", "mode": "continuable", "activation": 1}],
        "_continuation": {"phase": "ready", "remaining_calls": 8, "observations": "private"},
    }
    assert public_catalog(value)["continuation_available"] is True
    assert "_continuation" not in public_catalog(value)
    value["_continuation"]["phase"] = "unconfirmed"
    assert public_catalog(value)["continuation_available"] is False
    value["_continuation"]["phase"] = "ready"
    value["children"][0]["activation"] = 3
    assert public_catalog(value)["continuation_available"] is False


@pytest.fixture
def journal_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "synthetic-journal.db"))
    Base.metadata.create_all(engine)
    owner, session, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add_all(
            [
                models.User(id=owner, email="journal@example.test", password_hash="none"),
                models.User(id=other, email="other@example.test", password_hash="none"),
            ]
        )
        db.flush()
        db.add(models.ConversationSession(id=session, user_id=owner, title="Synthetic"))
        db.commit()
    yield engine, owner, session, other
    engine.dispose()


def runtime_for(owner, session):
    runtime = SubagentRuntime()
    runtime.bind(str(owner), str(session))
    child = runtime.start("training")
    runtime.transition(child["child_id"], "running")
    return runtime, child["child_id"]


def test_commit_reload_cancel_and_stale_revision(journal_db):
    engine, owner, session, _ = journal_db
    runtime, child_id = runtime_for(owner, session)
    with Session(engine) as db:
        saved = SubagentJournal(db).save(runtime, expected_revision=0)
        db.commit()
    assert saved["children"][0]["status"] == "running"
    with Session(engine) as db:
        loaded = SubagentJournal(db).load(uuid.UUID(runtime.parent_id), owner, session)
        assert loaded == saved
        assert "created_at" not in loaded["children"][0]
        runtime.close_active("parent_cancelled")
        SubagentJournal(db).save(runtime, expected_revision=1)
        db.commit()
    with Session(engine) as db:
        journal = SubagentJournal(db)
        with pytest.raises(SubagentError, match="checkpoint_conflict"):
            journal.save(runtime, expected_revision=1)
        assert (
            journal.load(uuid.UUID(runtime.parent_id), owner, session)["children"][0][
                "failure_reason"
            ]
            == "parent_cancelled"
        )
    assert runtime.children[child_id]["status"] == "failed"


def test_cross_owner_duplicate_creation_and_rollback(journal_db):
    engine, owner, session, other = journal_db
    runtime, _ = runtime_for(owner, session)
    with Session(engine) as db:
        journal = SubagentJournal(db)
        journal.save(runtime, expected_revision=0)
        db.commit()
        with pytest.raises(SubagentError, match="scope_mismatch"):
            journal.load(uuid.UUID(runtime.parent_id), other, session)
        with pytest.raises(SubagentError, match="checkpoint_conflict"):
            journal.save(runtime, expected_revision=0)
        runtime.close_active("consumer_closed")
        journal.save(runtime, expected_revision=1)
        db.rollback()
    with Session(engine) as db:
        assert (
            SubagentJournal(db).load(uuid.UUID(runtime.parent_id), owner, session)["revision"] == 1
        )


def test_rejects_child_disappearance_and_revision_regression(journal_db):
    engine, owner, session, _ = journal_db
    runtime, child_id = runtime_for(owner, session)
    with Session(engine) as db:
        journal = SubagentJournal(db)
        journal.save(runtime, expected_revision=0)
        db.commit()
        row = runtime.children.pop(child_id)
        with pytest.raises(SubagentError, match="invalid_checkpoint"):
            journal.save(runtime, expected_revision=1)
        runtime.children[child_id] = {**row, "revision": 1}
        with pytest.raises(SubagentError, match="invalid_checkpoint"):
            journal.save(runtime, expected_revision=1)


def test_first_creation_rollback_is_not_a_hidden_commit(journal_db):
    engine, owner, session, _ = journal_db
    runtime, _ = runtime_for(owner, session)
    with Session(engine) as db:
        SubagentJournal(db).save(runtime, expected_revision=0)
        db.rollback()
    with Session(engine) as db:
        with pytest.raises(SubagentError, match="catalog_not_found"):
            SubagentJournal(db).load(uuid.UUID(runtime.parent_id), owner, session)


def test_terminal_child_cannot_be_resurrected(journal_db):
    engine, owner, session, _ = journal_db
    runtime, child_id = runtime_for(owner, session)
    runtime.close_active("parent_cancelled")
    with Session(engine) as db:
        journal = SubagentJournal(db)
        journal.save(runtime, expected_revision=0)
        db.commit()
        assert db.get(models.AgentRun, uuid.UUID(runtime.parent_id)).status == "failed"
        runtime.children[child_id].update(status="running", revision=4)
        with pytest.raises(SubagentError, match="invalid_checkpoint"):
            journal.save(runtime, expected_revision=1)
