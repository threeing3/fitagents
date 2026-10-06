"""Stable positions, partial tails and authenticated read-only pagination."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fast_api.app.api.coach_platform import coach_router
from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db
from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.stream_event_pages import stream_event_page


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="pages@example.test", password_hash="synthetic")
        other = models.User(email="other-pages@example.test", password_hash="synthetic")
        db.add_all([user, other])
        db.flush()
        session = models.ConversationSession(user_id=user.id, title="pages")
        db.add(session)
        db.flush()
        request = models.IdempotencyRecord(
            user_id=user.id,
            operation="chat",
            idempotency_key="pages",
            request_json={"session_id": str(session.id)},
            status="processing",
        )
        db.add(request)
        db.commit()
        journal = journals.DurableStreamJournal(request.id, user.id, session.id)
        try:
            yield db, user, other, session, request, journal
        finally:
            journal.close()
    engine.dispose()


def test_append_during_pagination_preserves_positions_and_terminal(state):
    db, user, _, _, request, journal = state
    for index in range(3):
        journal.append({"type": "step", "event_id": f"e{index}", "stream_sequence": index * 2 + 1})
    first = stream_event_page(db, request.id, user.id, limit=2)
    assert [item["position"] for item in first["events"]] == [1, 2]
    assert first["has_more"] is True
    journal.append({"type": "journal.end", "state": "completed"})
    second = stream_event_page(db, request.id, user.id, first["next_cursor"], limit=2)
    assert [item["position"] for item in second["events"]] == [3, 4]
    assert second["status"] == "completed" and second["has_more"] is False
    empty = stream_event_page(db, request.id, user.id, second["next_cursor"])
    assert empty["events"] == [] and empty["next_cursor"] == second["next_cursor"]
    assert not db.new and not db.dirty


@pytest.mark.parametrize("tail", [b'{"type":"step"}', b"\xe4\xbd", b'{"type":'])
def test_partial_tail_never_advances_cursor_or_loses_valid_prefix(state, tail):
    db, user, _, _, request, journal = state
    journal.append({"type": "step", "event_id": "complete"})
    journal.file.buffer.write(tail)
    journal.file.flush()
    result = stream_event_page(db, request.id, user.id)
    assert len(result["events"]) == 1
    assert result["next_cursor"].endswith(":1")
    assert result["damaged_tail"] is True
    assert result["status"] == "unconfirmed" and result["may_repeat_writes"] is False


def test_cursor_cannot_cross_stream_account_or_future_position(state):
    db, user, other, _, request, _ = state
    with pytest.raises(LookupError):
        stream_event_page(db, request.id, other.id, "bad")
    for cursor in [
        f"{uuid.uuid4()}:0",
        f"{request.id}:-1",
        f"{request.id}:1",
        "invalid",
        f"{request.id}:０",
    ]:
        with pytest.raises(ValueError):
            stream_event_page(db, request.id, user.id, cursor)


def test_completed_tail_is_delivered_once_at_the_same_next_position(state):
    db, user, _, _, request, journal = state
    journal.append({"type": "step", "event_id": "first"})
    first = stream_event_page(db, request.id, user.id)
    journal.file.write('{"type":"step","event_id":"second"}')
    journal.file.flush()
    partial = stream_event_page(db, request.id, user.id, first["next_cursor"])
    assert partial["events"] == [] and partial["next_cursor"] == first["next_cursor"]
    journal.file.write("\n")
    journal.file.flush()
    complete = stream_event_page(db, request.id, user.id, partial["next_cursor"])
    assert complete["events"] == [{"position": 2, "event": {"type": "step", "event_id": "second"}}]
    assert complete["damaged_tail"] is False
    assert stream_event_page(db, request.id, user.id, complete["next_cursor"])["events"] == []


def test_wrong_session_marker_cannot_read_another_journal(state):
    db, user, _, _, request, _ = state
    other_session = models.ConversationSession(user_id=user.id, title="another")
    db.add(other_session)
    db.flush()
    run = models.AgentRun(
        user_id=user.id,
        session_id=other_session.id,
        run_type="chat",
        nodes=[{"type": "DurableStreamJournal", "journal_id": str(request.id)}],
    )
    db.add(run)
    db.commit()
    with pytest.raises(LookupError):
        stream_event_page(db, run.id, user.id)


def test_completed_run_uses_only_explicit_journal_reference(state):
    db, user, _, session, request, journal = state
    journal.append({"type": "step", "event_id": "known"})
    run = models.AgentRun(user_id=user.id, session_id=session.id, run_type="chat", nodes=[])
    db.add(run)
    db.commit()
    with pytest.raises(LookupError):
        stream_event_page(db, run.id, user.id)
    run.nodes = [{"type": "DurableStreamJournal", "journal_id": str(request.id)}]
    db.commit()
    assert stream_event_page(db, run.id, user.id)["journal_id"] == str(request.id)


def test_endpoint_auth_bounds_and_cursor_validation(state):
    db, user, other, _, request, _ = state
    app = FastAPI()
    app.include_router(coach_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    path = f"/v1/agent-runs/{request.id}/events"
    assert client.get(path).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.get(path + "?limit=0").status_code == 422
    assert client.get(path + "?limit=201").status_code == 422
    assert client.get(path + "?limit=2").json()["journal_id"] == str(request.id)
    assert client.get(path + "?cursor=invalid").status_code == 409
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.get(path).status_code == 404
