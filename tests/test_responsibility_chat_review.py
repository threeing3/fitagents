"""Synthetic end-to-end chat, review, approval and durable plan verification."""

import asyncio
import copy
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fast_api.app.api.coach_platform import coach_router
from fast_api.app.core.auth import get_current_user
from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.responsibilities import ResponsibilityService
from fast_api.app.services.responsibility_chat import handle_command


@pytest.fixture
def state():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(
            email="chat-review@example.test", password_hash="none", timezone="Asia/Shanghai"
        )
        other = models.User(email="other-review@example.test", password_hash="none")
        db.add_all([user, other])
        db.flush()
        session = models.ConversationSession(user_id=user.id, title="review")
        db.add(session)
        db.commit()
        provider = ModelProvider(Settings(llm_provider="offline", embedding_provider="offline"))
        service = CoachAgentService(db, provider)
        yield db, user, other, session, service
    engine.dispose()


def chat(state, text, key=None):
    _, user, _, session, service = state
    return asyncio.run(service.handle_chat_message(session.id, user.id, text, idempotency_key=key))


def _pending_followup(state):
    from fast_api.app.services.decision_evaluation import DecisionEvaluationService
    from fast_api.app.services.decision_logger import DecisionLogger

    db, user, _, _, _ = state
    decision = DecisionLogger(db).log_decision(
        user.id,
        {
            "decision_type": "nutrition_strategy",
            "decision_result": "synthetic",
            "reason": "test",
        },
    )
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    service.refresh_plan(plan, "test")
    db.commit()
    return db.scalar(
        select(models.DecisionFollowup).where(models.DecisionFollowup.evaluation_plan_id == plan.id)
    )


def test_chat_followup_refusal_persists_and_replays_without_guessing(state):
    db, user, _, _, _ = state
    followup = _pending_followup(state)
    result = chat(state, "这件事别再问了", "decline-once")
    repeat = chat(state, "这件事别再问了", "decline-once")
    assert result["state_updates"]["followup_status"] == "declined"
    assert str(repeat["agent_run_id"]) == str(result["agent_run_id"])
    db.refresh(followup)
    assert followup.status == "declined"
    from fast_api.app.services.decision_evaluation import DecisionEvaluationService

    assert DecisionEvaluationService(db).next_followup_for_delivery(user.id) is None
    assert result["runtime_route"]["reason"] == "explicit_followup_command"


def test_chat_followup_refusal_with_multiple_items_requires_target(state):
    db, _, _, _, _ = state
    first, second = _pending_followup(state), _pending_followup(state)
    result = chat(state, "这件事别再问了")
    assert result["state_updates"]["followup_status"] == "clarify"
    assert first.status == second.status == "pending"
    result = chat(state, f"停止跟进 {first.id}")
    assert result["state_updates"]["followup_status"] == "declined"
    db.refresh(second)
    assert second.status == "pending"


@pytest.mark.parametrize(
    "text",
    [
        "他说‘这件事别再问了’",
        "如果我说这件事别再问了会怎样？",
        "不要停止跟进",
        "这件事别再问了吗？",
    ],
)
def test_followup_quotes_questions_and_negation_are_not_commands(state, text):
    from fast_api.app.services.followup_chat import handle_followup_command

    db, user, _, _, _ = state
    followup = _pending_followup(state)
    assert handle_followup_command(db, user.id, text) is None
    assert followup.status == "pending"


def test_streamed_followup_refusal_commits_same_state_and_public_events(state):
    db, user, _, session, service = state
    followup = _pending_followup(state)

    async def collect():
        return [
            json.loads(entry)
            async for entry in service.stream_chat_events(
                session.id, user.id, "请不要再问这件事", idempotency_key="stream-decline"
            )
        ]

    events = asyncio.run(collect())
    done = next(entry for entry in events if entry["type"] == "done")
    assert done["state_updates"]["followup_status"] == "declined"
    assert any(entry["type"] == "execution_event" for entry in events)
    db.refresh(followup)
    assert followup.status == "declined"


def test_global_followup_refusal_clarifies_without_disabling_everything(state):
    followup = _pending_followup(state)
    result = chat(state, "以后别再追问我")
    assert result["state_updates"]["followup_status"] == "clarify"
    assert followup.status == "pending"
    assert result["state_updates"]["execution_events"][-1]["status"] == "blocked"


def test_chat_creation_persistence_idempotency_and_lifecycle(state):
    db, user, _, _, _ = state
    result = chat(state, "创建4周每周日18:00训练复盘，调整先问我", "create-once")
    repeat = chat(state, "创建4周每周日18:00训练复盘，调整先问我", "create-once")
    assert str(repeat["agent_run_id"]) == str(result["agent_run_id"])
    assert db.scalar(select(func.count()).select_from(models.AgentTaskState)) == 1
    task_id = result["state_updates"]["responsibility"]["id"]
    assert (
        chat(state, f"暂停训练复盘 {task_id}")["state_updates"]["responsibility"]["status"]
        == "paused"
    )
    assert (
        chat(state, f"恢复训练复盘 {task_id}")["state_updates"]["responsibility"]["status"]
        == "active"
    )
    assert len(chat(state, "查看训练复盘责任")["state_updates"]["responsibilities"]) == 1
    assert db.scalar(select(models.AgentTaskState)).user_id == user.id


def test_responsibility_commands_preserve_profile_completeness(state):
    db, user, _, _, _ = state
    initial = chat(state, "查看训练复盘责任")
    assert not initial["onboarding_complete"] and "age" in initial["missing_slots"]
    db.add(
        models.UserProfile(
            user_id=user.id,
            age=25,
            height_cm=175,
            weight_kg=70,
            goal="fitness",
            experience_level="beginner",
            equipment_available=["dumbbell"],
        )
    )
    db.commit()
    ready = chat(state, "查看训练复盘责任")
    assert ready["onboarding_complete"] and ready["missing_slots"] == []


@pytest.mark.parametrize(
    "text",
    [
        "创建0周每周日18:00训练复盘",
        "创建4周每周日25:00训练复盘",
        "创建一个每周训练复盘",
        "创建4周每周日18:00训练复盘，自动批准",
    ],
)
def test_incomplete_or_unbounded_request_never_creates(state, text):
    db = state[0]
    assert chat(state, text)["state_updates"]["responsibility_status"] == "clarify"
    assert db.scalar(select(func.count()).select_from(models.AgentTaskState)) == 0


@pytest.mark.parametrize(
    "text",
    [
        "如果创建4周每周日18:00训练复盘会怎样？",
        "不要创建4周每周日18:00训练复盘",
        "举例：创建4周每周日18:00训练复盘",
        "帮朋友创建4周每周日18:00训练复盘",
    ],
)
def test_question_example_and_negation_not_commands(state, text):
    db, user, _, _, _ = state
    assert handle_command(db, user.id, text) is None
    assert db.scalar(select(func.count()).select_from(models.AgentTaskState)) == 0


def test_foreign_session_cannot_create(state):
    db, user, other, _, service = state
    foreign = models.ConversationSession(user_id=other.id, title="foreign")
    db.add(foreign)
    db.commit()
    with pytest.raises(ValueError):
        asyncio.run(service.handle_chat_message(foreign.id, user.id, "创建4周每周日18:00训练复盘"))
    assert db.scalar(select(func.count()).select_from(models.AgentTaskState)) == 0


def test_stream_uses_same_creation_and_cached_events(state):
    db, user, _, session, service = state

    async def collect():
        return [
            raw
            async for raw in service.stream_chat_events(
                session.id, user.id, "创建4周每周日18:00训练复盘", "stream-create"
            )
        ]

    events = asyncio.run(collect())
    assert events == asyncio.run(collect())
    assert json.loads(events[-1])["type"] == "done"
    assert db.scalar(select(func.count()).select_from(models.AgentTaskState)) == 1
    journal = [json.loads(raw) for raw in events if json.loads(raw)["type"] == "execution_event"]
    assert [entry["name"] for entry in journal] == ["command.route", "command.result"]
    assert journal[0]["source"] == "rule"


def test_chat_history_recovers_saved_events_and_enforces_session_owner(state):
    db, user, other, session, _ = state
    chat(state, "创建4周每周日18:00训练复盘")
    # Windows clocks can produce equal timestamps for consecutive messages.
    messages = db.scalars(
        select(models.ChatMessage).where(models.ChatMessage.session_id == session.id)
    ).all()
    for message in messages:
        message.created_at = datetime(2026, 10, 2, 10, 0)
    db.commit()
    app = FastAPI()
    app.include_router(coach_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        rows = client.get(f"/v1/chat/sessions/{session.id}/messages").json()
        assert [row["role"] for row in rows] == ["user", "assistant"]
        assert rows[0]["execution_events"] == []
        assert rows[1]["execution_events"][0]["source"] == "rule"
        assert rows[1]["execution_events"][-1]["status"] == "completed"
        app.dependency_overrides[get_current_user] = lambda: other
        assert client.get(f"/v1/chat/sessions/{session.id}/messages").status_code == 403


def prepare_review(state, *, fatigue=8, count=3, symptoms=False, sets=4):
    db, user, _, _, _ = state
    task_id = chat(state, "创建4周每周日18:00训练复盘")["state_updates"]["responsibility"]["id"]
    task = db.get(models.AgentTaskState, uuid.UUID(task_id))
    due = datetime.fromisoformat(task.constraints["responsibility"]["next_wake_at"])
    # Simulate the first review with complete prior synthetic evidence.
    spec = dict(task.constraints["responsibility"])
    spec["starts_at"] = (due - timedelta(days=7)).isoformat()
    task.constraints = {"responsibility": spec}
    local_day = due.astimezone(ZoneInfo("Asia/Shanghai")).date()
    for index in range(count):
        db.add(
            models.RecoveryLog(
                user_id=user.id,
                log_date=local_day - timedelta(days=index + 1),
                fatigue_score=fatigue,
            )
        )
    if symptoms:
        db.add(
            models.SymptomLog(
                user_id=user.id, symptom_date=local_day - timedelta(days=1), symptom_type="test"
            )
        )
    baseline = {
        "training_days": [
            {
                "date": (local_day + timedelta(days=1)).isoformat(),
                "exercises": [{"name": "划船", "sets": sets, "reps": 10}],
            },
            {
                "date": (local_day + timedelta(days=2)).isoformat(),
                "exercises": [{"name": "深蹲", "sets": 3}],
            },
        ],
        "nutrition": {"protein": 120},
    }
    plan = models.TrainingPlan(user_id=user.id, status="active", plan_json=copy.deepcopy(baseline))
    db.add(plan)
    ResponsibilityService(db).scan_due(due)
    db.flush()
    job = db.scalar(
        select(models.BackgroundTask).where(
            models.BackgroundTask.task_type == "responsibility.weekly_review"
        )
    )
    return task, due, job, plan, baseline


def test_full_review_proposal_chat_approval_and_real_worker(state):
    db, user, other, _, _ = state
    task, due, job, plan, baseline = prepare_review(state)
    result = ResponsibilityService(db).execute_review(job, now=due)
    job.status = "completed"
    db.commit()
    proposal = result["adjustment_proposal"]
    assert proposal["status"] == "waiting_approval"
    assert plan.plan_json == baseline
    assert run_one_background_task(db) is None
    approval_id = proposal["approval_id"]
    assert (
        handle_command(db, other.id, f"批准调整 {approval_id}")[1]["approval_status"] == "rejected"
    )
    assert len(chat(state, "查看待审批调整")["state_updates"]["pending_approvals"]) == 1
    assert (
        chat(state, f"批准调整 {approval_id}")["state_updates"]["approval"]["status"] == "approved"
    )
    completed = run_one_background_task(db)
    assert completed.status == "completed" and completed.result_json["verified"]
    db.refresh(plan)
    assert plan.plan_json["training_days"][0]["exercises"][0]["sets"] == 3
    assert plan.plan_json["training_days"][1] == baseline["training_days"][1]
    assert plan.plan_json["nutrition"] == baseline["nutrition"]
    row = db.get(models.PendingApproval, uuid.UUID(approval_id))
    assert row.status == "executed" and len(row.context_json["review_signal"]["evidence"]) == 3
    journal = row.context_json["execution_events"]
    names = [entry["name"] for entry in journal]
    assert (
        names.index("review.decision")
        < names.index("approval.decision")
        < names.index("execution.recheck")
        < names.index("execution.tool")
        < names.index("execution.verify")
        < names.index("execution.complete")
    )
    assert {entry["source"] for entry in journal} == {"rule", "runtime", "user", "tool"}
    with Session(db.get_bind()) as fresh:
        assert (
            fresh.get(models.PendingApproval, uuid.UUID(approval_id)).context_json[
                "execution_events"
            ]
            == journal
        )
        from fast_api.app.services.execution_events import public_run_events

        run = fresh.scalar(
            select(models.AgentRun)
            .where(models.AgentRun.run_type == "responsibility_command")
            .order_by(models.AgentRun.started_at.desc())
        )
        recovered = public_run_events(fresh, run, user.id)
        assert any(entry["name"] == "execution.verify" for entry in recovered)
    assert run_one_background_task(db) is None
    assert chat(state, f"批准调整 {approval_id}")["state_updates"]["approval_status"] == "executed"


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"count": 2}, "insufficient_recovery_evidence"),
        ({"fatigue": 5}, "no_reduction_signal"),
        ({"symptoms": True}, "symptoms_require_manual_review"),
        ({"sets": 1}, "proposal_validation_failed"),
    ],
)
def test_no_unsafe_proposal_and_no_orphan_job(state, options, reason):
    db = state[0]
    _, due, job, plan, baseline = prepare_review(state, **options)
    result = ResponsibilityService(db).execute_review(job, now=due)
    db.commit()
    assert result["adjustment_proposal"]["reason"] == reason
    assert plan.plan_json == baseline
    assert db.scalar(select(func.count()).select_from(models.PendingApproval)) == 0
    assert db.scalar(select(func.count()).select_from(models.BackgroundTask)) == 1


def test_unresolved_proposal_suppresses_duplicates_and_late_review(state):
    db = state[0]
    task, due, job, _, _ = prepare_review(state)
    first = ResponsibilityService(db).execute_review(job, now=due)
    assert first["adjustment_proposal"]["status"] == "waiting_approval"
    second = ResponsibilityService(db).execute_review(job, now=due)
    assert second["adjustment_proposal"]["reason"] == "unresolved_adjustment_exists"
    late = ResponsibilityService(db).execute_review(job, now=due + timedelta(days=2))
    assert late["adjustment_proposal"]["reason"] == "review_evidence_stale"
    assert db.scalar(select(func.count()).select_from(models.PendingApproval)) == 1
