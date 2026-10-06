"""Committed receipt verification, isolated file database and negative references."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.write_receipts import workout_write_receipt


@pytest.fixture
def receipt_state(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'receipts.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="receipt@example.test", password_hash="synthetic")
        other = models.User(email="receipt-other@example.test", password_hash="synthetic")
        db.add_all([user, other])
        db.flush()
        session = models.ConversationSession(user_id=user.id, title="receipt")
        db.add(session)
        db.flush()
        message = models.ChatMessage(
            user_id=user.id, session_id=session.id, role="user", content="synthetic workout"
        )
        workout = models.WorkoutLog(
            user_id=user.id, workout_name="synthetic", performed_at=datetime.utcnow()
        )
        db.add_all([message, workout])
        db.flush()
        receipt = models.IdempotencyRecord(
            user_id=user.id,
            operation="workout_log",
            idempotency_key="chat-workout:" + str(message.id),
            status="completed",
            completed_at=datetime.utcnow(),
            request_json={},
            response_json={"workout_log_id": str(workout.id)},
        )
        db.add(receipt)
        db.commit()
        yield db, user, other, message, workout, receipt
    engine.dispose()


def test_committed_receipt_is_independently_verified_without_writes(receipt_state):
    db, user, _, message, workout, receipt = receipt_state
    result = workout_write_receipt(db, user.id, message.id, workout.id)
    assert result["state"] == "committed"
    assert result["receipt_id"] == str(receipt.id)
    assert result["verification"] == "independent_committed_read"
    assert result["may_repeat_writes"] is False
    assert not db.new and not db.dirty


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_owner",
        "wrong_message",
        "wrong_target",
        "pending",
        "missing_time",
        "foreign_record",
        "foreign_session",
    ],
)
def test_false_references_cannot_prove_commit(receipt_state, failure):
    db, user, other, message, workout, receipt = receipt_state
    owner, source, target = user.id, message.id, workout.id
    if failure == "foreign_owner":
        owner = other.id
    elif failure == "wrong_message":
        source = uuid.uuid4()
    elif failure == "wrong_target":
        target = uuid.uuid4()
    elif failure == "pending":
        receipt.status = "processing"
    elif failure == "missing_time":
        receipt.completed_at = None
    elif failure == "foreign_session":
        db.get(models.ConversationSession, message.session_id).user_id = other.id
    else:
        workout.user_id = other.id
    db.commit()
    result = workout_write_receipt(db, owner, source, target)
    assert result["state"] == "unconfirmed"
    assert "receipt_id" not in result
    assert result["may_repeat_writes"] is False


def test_uncommitted_receipt_is_not_visible_and_caller_transaction_is_preserved(receipt_state):
    db, user, _, message, workout, receipt = receipt_state
    receipt.status = "processing"
    db.commit()
    receipt.status = "completed"
    db.flush()
    result = workout_write_receipt(db, user.id, message.id, workout.id)
    assert result["state"] == "unconfirmed"
    assert db.in_transaction()
    db.rollback()
    assert receipt.status == "processing"


def test_verification_connection_failure_does_not_change_committed_write(
    receipt_state, monkeypatch
):
    db, user, _, message, workout, _ = receipt_state
    owner, source, target = user.id, message.id, workout.id
    engine = db.get_bind()
    with monkeypatch.context() as patch:

        def unavailable(*args, **kwargs):
            raise OperationalError("synthetic", {}, Exception("reader unavailable"))

        patch.setattr(engine, "connect", unavailable)
        result = workout_write_receipt(db, owner, source, target)
    assert result["state"] == "unconfirmed"
    assert result["reason"] == "verification_unavailable"
    assert result["may_repeat_writes"] is False
    assert db.get(models.WorkoutLog, target) is not None
    assert workout_write_receipt(db, owner, source, target)["state"] == "committed"


def test_trace_rechecks_receipt_instead_of_trusting_stale_tool_output(receipt_state):
    from fast_api.app.services.execution_trace import recorded_execution_trace

    db, user, other, message, workout, _ = receipt_state
    run = models.AgentRun(user_id=user.id, session_id=message.session_id, run_type="chat", nodes=[])
    db.add(run)
    db.flush()
    call = models.ToolCall(
        agent_run_id=run.id,
        tool_name="training.log.write",
        status="success",
        input_json={"request_key": str(message.id)},
        output_json={"workout_log_id": str(workout.id), "write_receipt": {"state": "committed"}},
        latency_ms=1,
    )
    db.add(call)
    db.commit()
    trace = recorded_execution_trace(db, run.id, user.id)
    event = next(item for item in trace["events"] if item["source"] == "tool")
    assert event["output"]["write_receipt"]["state"] == "committed"
    workout.user_id = other.id
    db.commit()
    trace = recorded_execution_trace(db, run.id, user.id)
    event = next(item for item in trace["events"] if item["source"] == "tool")
    assert event["status"] == "success"
    assert event["output"]["write_receipt"]["state"] == "unconfirmed"
    assert not db.new and not db.dirty


def test_same_owner_other_session_cannot_prove_this_run_write(receipt_state):
    db, user, _, message, workout, _ = receipt_state
    result = workout_write_receipt(db, user.id, message.id, workout.id, session_id=uuid.uuid4())
    assert result["state"] == "unconfirmed"
    assert result["reason"] == "source_session_mismatch"


@pytest.mark.parametrize("committed", [True, False])
def test_interrupted_trace_and_status_keep_request_unknown_but_verify_write(
    receipt_state, tmp_path, monkeypatch, committed
):
    from types import SimpleNamespace

    from fast_api.app.services import durable_stream_journal as journals
    from fast_api.app.services.chat_request_status import get_chat_request_status
    from fast_api.app.services.execution_trace import recorded_execution_trace

    db, user, _, message, _, receipt = receipt_state
    if not committed:
        receipt.status = "processing"
    request = models.IdempotencyRecord(
        user_id=user.id,
        operation="chat",
        idempotency_key="interrupted",
        status="processing",
        request_json={"session_id": str(message.session_id)},
        response_json={"execution": {"user_message_id": str(message.id)}},
    )
    db.add(request)
    db.commit()
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    journal = journals.DurableStreamJournal(request.id, user.id, message.session_id)
    journal.append({"type": "step", "name": "write_requested", "event_id": "prefix"})
    journal.close()
    trace = recorded_execution_trace(db, request.id, user.id)
    status = get_chat_request_status(db, user.id, message.session_id, "interrupted")
    assert trace["status"] == status["status"] == "unconfirmed"
    assert trace["write_receipts"] == status["write_receipts"]
    assert trace["write_receipts"][0]["state"] == ("committed" if committed else "unconfirmed")
    assert len(status["confirmed_writes"]) == int(committed)
    assert status["may_repeat_writes"] is False
    assert len(trace["events"]) == 1
    assert not db.new and not db.dirty


def test_uncommitted_request_link_cannot_confirm_an_unrelated_workout(receipt_state):
    from fast_api.app.services.write_receipts import chat_workout_receipt

    db, user, _, message, _, _ = receipt_state
    request = models.IdempotencyRecord(
        user_id=user.id,
        operation="chat",
        idempotency_key="pending-link",
        status="processing",
        request_json={"session_id": str(message.session_id)},
        response_json={},
    )
    db.add(request)
    db.commit()
    request.response_json = {"execution": {"user_message_id": str(message.id)}}
    db.flush()
    result = chat_workout_receipt(db, user.id, request.id, message.session_id)
    assert result["state"] == "unconfirmed"
    assert result["reason"] == "source_message_unavailable"
    assert db.in_transaction()
    db.rollback()
