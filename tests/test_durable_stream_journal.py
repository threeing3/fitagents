"""Durable public events survive cancellation without committing business work."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.chat_request_status import get_chat_request_status
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.execution_trace import recorded_execution_trace


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    return tmp_path


def test_saved_prefix_without_terminal_does_not_claim_dead_process(journal_dir):
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, session)
    journal.append(
        {
            "event_id": "first",
            "type": "step",
            "stream_sequence": 1,
            "api_key": "private",
            "summary": "request dispatched",
        }
    )
    # Closing the file without a terminal models the surviving prefix of a hard loss.
    journal.close()
    saved = journals.read_stream_journal(identity, owner, session)
    assert saved["state"] == "unconfirmed"
    assert saved["liveness"] == "not_checked"
    assert saved["entries"][0]["event_id"] == "first"
    assert "private" not in str(saved)
    assert journals.read_stream_journal(identity, uuid.uuid4(), session) is None
    assert journals.read_stream_journal(identity, owner, uuid.uuid4()) is None
    with pytest.raises(FileExistsError):
        journals.DurableStreamJournal(identity, owner, session)
    with pytest.raises(ValueError):
        journals.journal_path("../outside")


def test_damaged_tail_keeps_complete_prefix_and_is_explicit(journal_dir):
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, session)
    journal.append({"type": "step", "event_id": "first"})
    journal.close()
    with journals.journal_path(identity).open("a", encoding="utf-8") as file:
        file.write('{"type":')
    saved = journals.read_stream_journal(identity, owner, session)
    assert saved["damaged_tail"] is True
    assert saved["entries"] == [{"type": "step", "event_id": "first"}]
    assert saved["state"] == "unconfirmed"


def test_cancelled_stream_is_readable_without_committing_pending_business(journal_dir, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="durable@example.test", password_hash="synthetic")
        db.add(user)
        db.flush()
        session = models.ConversationSession(user_id=user.id, title="durable")
        db.add(session)
        db.flush()
        record = models.IdempotencyRecord(
            user_id=user.id,
            operation="chat",
            idempotency_key="cancelled",
            request_json={"session_id": str(session.id)},
            status="processing",
        )
        db.add(record)
        db.commit()
        identity, owner, sid = record.id, user.id, session.id
        service = object.__new__(CoachAgentService)
        service.db = db
        monkeypatch.setattr(service, "_begin_chat_request", lambda *args: (record, None))

        async def stream(*args):
            db.add(models.UserProfile(user_id=owner, goal="muscle_gain"))
            yield json.dumps({"type": "step", "name": "before_write", "status": "running"}) + "\n"
            raise AssertionError("closed stream must not execute next step")

        monkeypatch.setattr(service, "_stream_chat_events_once", stream)

        async def cancel():
            stream = service.stream_chat_events(sid, owner, "test")
            first = json.loads(await anext(stream))
            assert (
                journals.read_stream_journal(identity, owner, sid)["entries"][0]["event_id"]
                == first["event_id"]
            )
            await stream.aclose()

        asyncio.run(cancel())
        assert any(isinstance(row, models.UserProfile) for row in db.new)
        db.rollback()
        assert db.scalar(select(models.UserProfile)) is None
        saved = journals.read_stream_journal(identity, owner, sid)
        assert saved["state"] == "interrupted"
        assert saved["entries"][-1]["business_result"] == "unconfirmed"
        assert sum(row.get("type") == "journal.end" for row in saved["entries"]) == 1
        status = get_chat_request_status(db, owner, sid, "cancelled")
        assert status["status"] == "unconfirmed"
        assert status["trace_id"] == str(identity)
        assert status["may_repeat_writes"] is False
        packet = recorded_execution_trace(db, identity, owner)
        assert packet["status"] == "interrupted"
        assert packet["events"][0]["name"] == "before_write"
        assert not db.new and not db.dirty
        with pytest.raises(ValueError, match="not found"):
            recorded_execution_trace(db, identity, uuid.uuid4())
    engine.dispose()


def test_close_after_done_does_not_replace_completed_with_interrupted(journal_dir, monkeypatch):
    service = object.__new__(CoachAgentService)
    monkeypatch.setattr(service, "_begin_chat_request", lambda *args: (None, None))

    async def stream(*args):
        yield json.dumps({"type": "done"}) + "\n"

    monkeypatch.setattr(service, "_stream_chat_events_once", stream)

    async def finish():
        stream = service.stream_chat_events(uuid.uuid4(), uuid.uuid4(), "test")
        await anext(stream)
        await stream.aclose()

    asyncio.run(finish())
    path = next((journal_dir / "streams").glob("*.jsonl"))
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    terminals = [entry for entry in entries if entry.get("type") == "journal.end"]
    assert len(terminals) == 1
    assert terminals[0]["state"] == "completed"
