"""Owner-scoped recorded trace, isolated database, no real model or business writes."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta

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
from fast_api.app.services.execution_events import execution_event
from fast_api.app.services.execution_trace import recorded_execution_trace, trace_detail


@pytest.fixture
def trace_state():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="trace@example.test", password_hash="synthetic")
        other = models.User(email="other-trace@example.test", password_hash="synthetic")
        db.add_all([user, other])
        db.flush()
        session = models.ConversationSession(user_id=user.id, title="trace")
        db.add(session)
        db.flush()
        parent = uuid.uuid4()
        entry = execution_event(
            "subagent.result",
            "failed",
            "child failed",
            details={
                "parent_id": str(parent),
                "child_id": "training",
                "failure_reason": "budget_exhausted",
            },
        )
        run = models.AgentRun(
            user_id=user.id,
            session_id=session.id,
            run_type="chat",
            nodes=[
                {
                    "event_id": "step-1",
                    "node": "ContextBuilder",
                    "sequence": 1,
                    "output": {"evidence_ids": ["evidence-1"], "api_key": "do-not-show"},
                },
                entry,
            ],
        )
        catalog = models.AgentRun(
            id=parent,
            user_id=user.id,
            session_id=session.id,
            run_type="subagent_catalog",
            nodes=[
                {
                    "parent_id": str(parent),
                    "children": [{"child_id": "training", "status": "failed"}],
                    "_continuation": {"prompt": "private"},
                }
            ],
        )
        db.add_all([run, catalog])
        db.flush()
        replay = models.AgentRunReplay(
            agent_run_id=run.id,
            user_id=user.id,
            session_id=session.id,
            state_snapshot={"state_updates": {"execution_events": [entry]}},
            request_json={},
            tool_plan_json={},
            response_snapshot={},
            config_snapshot={},
        )
        call = models.ToolCall(
            agent_run_id=run.id,
            tool_name="records.read",
            status="success",
            input_json={"password": "no"},
            output_json={"records": 2},
            latency_ms=3,
        )
        db.add_all([replay, call])
        db.commit()
        yield db, user, other, run, catalog
    engine.dispose()


def test_trace_is_stable_deduplicated_scoped_and_read_only(trace_state):
    db, user, other, run, catalog = trace_state
    before = [(item.id, item.nodes) for item in db.query(models.AgentRun).all()]
    result = recorded_execution_trace(db, run.id, user.id)
    assert result == recorded_execution_trace(db, run.id, user.id)
    assert len(result["events"]) == 4
    assert len({item["event_id"] for item in result["events"]}) == 4
    assert any(
        item["child_id"] == "training" and item["status"] == "failed" for item in result["events"]
    )
    assert any(item["name"] == "SubagentCheckpoint" for item in result["events"])
    assert "do-not-show" not in str(result)
    assert "private" not in str(result)
    assert result["coverage"]["exact_model_requests"] is False
    assert not db.new and not db.dirty
    assert before == [(item.id, item.nodes) for item in db.query(models.AgentRun).all()]
    with pytest.raises(ValueError, match="not found"):
        recorded_execution_trace(db, run.id, other.id)


def test_trace_refuses_foreign_catalog_join(trace_state):
    db, user, other, run, catalog = trace_state
    catalog.user_id = other.id
    db.commit()
    result = recorded_execution_trace(db, run.id, user.id)
    assert not any(item["name"] == "SubagentCheckpoint" for item in result["events"])


def test_trace_endpoint_requires_login_and_returns_404_for_foreign_run(trace_state):
    db, user, other, run, _ = trace_state
    app = FastAPI()
    app.include_router(coach_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    path = f"/v1/agent-runs/{run.id}/trace"
    assert client.get(path).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.get(path).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.get(path).status_code == 200


def test_trace_detail_excludes_private_fields_and_marks_truncation():
    result = trace_detail(
        {
            "system_prompt": "private",
            "reasoning_content": "private",
            "token_count": 10,
            "text": "a" * 4001,
            "rows": list(range(101)),
        }
    )
    assert result["system_prompt"] == result["reasoning_content"] == "[excluded]"
    assert result["token_count"] == 10
    assert "truncated" in result["text"]
    assert result["rows"][-1] == {"_truncated_items": 1}


def test_later_approval_events_are_joined_by_explicit_identity(trace_state):
    db, user, other, run, _ = trace_state
    approval = models.PendingApproval(
        user_id=user.id,
        session_id=run.session_id,
        tool_name="plan.adjust",
        tool_description="synthetic",
        permission_level="ask",
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(approval)
    db.flush()
    waiting = execution_event(
        "approval.wait", "blocked", "waiting", details={"approval_id": str(approval.id)}
    )
    completed = execution_event(
        "approval.decision",
        "completed",
        "approved, not executed",
        source="user",
        details={"approval_id": str(approval.id)},
    )
    approval.context_json = {"execution_events": [waiting, completed]}
    replay = db.query(models.AgentRunReplay).filter_by(agent_run_id=run.id).one()
    replay.state_snapshot = {"state_updates": {"execution_events": [waiting]}}
    db.commit()
    result = recorded_execution_trace(db, run.id, user.id)
    assert sum(event["name"] == "approval.wait" for event in result["events"]) == 1
    assert any(event["name"] == "approval.decision" for event in result["events"])
    approval.user_id = other.id
    db.commit()
    result = recorded_execution_trace(db, run.id, user.id)
    assert not any(event["name"] == "approval.decision" for event in result["events"])


def test_history_keeps_explicit_message_run_link(trace_state):
    db, user, _, run, _ = trace_state
    message = models.ChatMessage(
        user_id=user.id, session_id=run.session_id, role="assistant", content="synthetic response"
    )
    db.add(message)
    db.flush()
    run.nodes = [*run.nodes, {"type": "ChatExecutionJournal", "message_id": str(message.id)}]
    db.commit()
    app = FastAPI()
    app.include_router(coach_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    response = TestClient(app).get(f"/v1/chat/sessions/{run.session_id}/messages")
    assert response.status_code == 200
    assert response.json()[0]["agent_run_id"] == str(run.id)


def test_public_stream_identity_is_saved_without_answer_chunks(trace_state, monkeypatch):
    from fast_api.app.services.coach_agent import CoachAgentService

    db, user, _, run, _ = trace_state
    service = object.__new__(CoachAgentService)
    service.db = db
    monkeypatch.setattr(service, "_begin_chat_request", lambda *args: (None, None))

    async def stream(*args):
        yield json.dumps({"type": "status", "text": "reading records"}) + "\n"
        yield json.dumps({"type": "answer_delta", "text": "private reply"}) + "\n"
        yield json.dumps({"type": "done", "run_id": str(run.id)}) + "\n"

    monkeypatch.setattr(service, "_stream_chat_events_once", stream)

    async def collect():
        return [
            json.loads(raw)
            async for raw in service.stream_chat_events(run.session_id, user.id, "test")
        ]

    entries = asyncio.run(collect())
    assert [entry["stream_sequence"] for entry in entries] == [1, 2, 3]
    assert len({entry["event_id"] for entry in entries}) == 3
    saved = [node for node in run.nodes if node.get("trace_source") == "stream"]
    assert len(saved) == 1
    assert saved[0]["event_id"] == entries[0]["event_id"]
    assert "private reply" not in str(saved)
    result = recorded_execution_trace(db, run.id, user.id)
    assert any(
        event["source"] == "stream" and event["stream_sequence"] == 1 for event in result["events"]
    )
