"""Cloud journals survive fresh readers and retain scoped, non-executing replay."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from fast_api.app.db.models import StreamJournal, StreamJournalEvent
from fast_api.app.services import database_stream_journal as database
from fast_api.app.services import durable_stream_journal as journals


@pytest.fixture
def durable_store(tmp_path, monkeypatch):
    path = tmp_path / "diagnostics.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    StreamJournal.__table__.create(engine)
    StreamJournalEvent.__table__.create(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(engine))
    monkeypatch.setattr(
        journals,
        "get_settings",
        lambda: SimpleNamespace(
            stream_journal_backend="database", agent_log_dir=str(tmp_path / "absent")
        ),
    )
    yield engine, path
    engine.dispose()


def test_persistent_prefix_redaction_ownership_and_fresh_reader(durable_store, monkeypatch):
    engine, path = durable_store
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, session)
    journal.append({"type": "step", "api_key": "never-persist", "position": 999})
    journal.append({"type": "step", "event_id": "second"})
    journal.close()
    engine.dispose()
    reopened = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(reopened))
    try:
        saved = journals.read_stream_journal(identity, owner, session, after=0, limit=1)
        assert saved["recorded_count"] == 2
        assert saved["state"] == "unconfirmed"
        assert saved["next_position"] == 1 and saved["has_more"]
        assert "never-persist" not in str(saved)
        assert journals.read_stream_journal(identity, uuid.uuid4(), session) is None
        assert journals.read_stream_journal(identity, owner, uuid.uuid4()) is None
        assert journals.read_stream_journal(identity, owner, session, after=1)["entries"] == [
            {"type": "step", "event_id": "second"}
        ]
        with pytest.raises(ValueError):
            journals.read_stream_journal(identity, owner, session, after=3)
        with pytest.raises(FileExistsError):
            journals.DurableStreamJournal(identity, owner, session)
        with pytest.raises(ValueError):
            journal.append({"type": "step"})
    finally:
        reopened.dispose()


def test_explicit_terminal_and_worker_identity(durable_store):
    identity = uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, "background-worker", None)
    journal.append({"type": "step"})
    journal.append({"type": "journal.end", "state": "cancelled"})
    journal.close()
    saved = journals.read_stream_journal(identity, "background-worker", None, limit=1)
    assert saved["state"] == "cancelled"
    assert saved["liveness"] == "not_checked"
    assert saved["may_repeat_writes"] is False
    assert saved["damaged_tail"] is False


def test_append_failure_does_not_publish_a_position(durable_store, monkeypatch):
    identity, owner = uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, None)
    journal.append({"type": "step", "event_id": "committed"})
    with monkeypatch.context() as patch:

        def fail_json(*args, **kwargs):
            raise RuntimeError("serialization failed")

        patch.setattr(database.json, "dumps", fail_json)
        with pytest.raises(RuntimeError):
            journal.append({"type": "step"})
    saved = journals.read_stream_journal(identity, owner, None)
    assert saved["recorded_count"] == 1
    assert saved["next_position"] == 1
    assert saved["state"] == "unconfirmed"


def test_explicit_log_directory_keeps_file_backend(durable_store, tmp_path):
    identity, owner = uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, None, tmp_path)
    journal.append({"type": "step"})
    journal.close()
    assert journals.journal_path(identity, tmp_path).exists()
    assert journals.read_stream_journal(identity, owner, None, tmp_path)["recorded_count"] == 1


def test_database_flush_failure_rolls_back_event_and_counter(durable_store):
    identity, owner = uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, None)

    def fail_flush(*args):
        raise RuntimeError("synthetic flush failure")

    event.listen(database.SessionLocal, "before_flush", fail_flush)
    try:
        with pytest.raises(RuntimeError):
            journal.append({"type": "step"})
    finally:
        event.remove(database.SessionLocal, "before_flush", fail_flush)
    assert journals.read_stream_journal(identity, owner, None)["recorded_count"] == 0
    journal.append({"type": "journal.end", "state": "completed"})
    assert journals.read_stream_journal(identity, owner, None)["next_position"] == 1


@pytest.mark.parametrize("state", ["completed", "cancelled", "interrupted", "outcome_unknown"])
def test_terminal_projection_preserves_explicit_recorded_state(state):
    from fast_api.app.services.execution_trace import stream_trace_node

    assert stream_trace_node({"type": "journal.end", "state": state})["status"] == state
    assert stream_trace_node({"type": "journal.end"})["status"] == "outcome_unknown"
