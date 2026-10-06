"""Actual approval worker in an isolated file database; no model calls."""

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.approval_manager import ApprovalManager
from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.execution_trace import recorded_execution_trace
from fast_api.app.services.responsibilities import ResponsibilityService
from fast_api.app.services.write_receipts import approved_plan_write_receipt


@pytest.fixture
def plan_state(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'plan.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(
            email="plan-receipt@example.test", password_hash="synthetic", timezone="UTC"
        )
        other = models.User(email="other-plan-receipt@example.test", password_hash="synthetic")
        db.add_all([user, other])
        db.flush()
        task = ResponsibilityService(db).create_weekly(user.id, "改计划先问")
        day = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
        plan = models.TrainingPlan(
            user_id=user.id,
            plan_json={
                "training_days": [
                    {"date": day, "exercises": [{"name": "划船", "sets": 4, "reps": 10}]}
                ]
            },
        )
        db.add(plan)
        db.commit()
        proposal = ApprovedPlanAdjustmentService(db).propose(
            user.id, task.id, plan.id, day, 1, "合成疲劳复盘"
        )
        db.commit()
        approval = db.get(models.PendingApproval, uuid.UUID(proposal["approval"]["approval_id"]))
        yield db, user, other, plan, approval
    engine.dispose()


def execute(state):
    db, user, _, _, approval = state
    ApprovalManager(db).approve(str(approval.id))
    db.commit()
    job = run_one_background_task(db, user_id=user.id)
    assert job.status == "completed" and job.result_json["status"] == "adjusted"
    return job


def test_approval_alone_is_not_a_commit_and_actual_worker_is_confirmed(plan_state):
    db, user, _, plan, approval = plan_state
    ApprovalManager(db).approve(str(approval.id))
    db.commit()
    before = approved_plan_write_receipt(db, user.id, approval.id)
    assert before["state"] == "unconfirmed" and before["approval_status"] == "approved"
    job = run_one_background_task(db, user_id=user.id)
    receipt = approved_plan_write_receipt(db, user.id, approval.id)
    assert receipt["state"] == "committed"
    assert receipt["record_id"] == str(plan.id)
    assert receipt["job_id"] == str(job.id)
    trace = recorded_execution_trace(db, uuid.UUID(receipt["agent_run_id"]), user.id)
    assert receipt in trace["write_receipts"]
    assert receipt["may_repeat_writes"] is False
    assert not db.new and not db.dirty


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_owner",
        "job_pending",
        "job_target",
        "run_owner",
        "missing_completed_at",
        "plan_changed",
        "task_owner",
        "event_link",
        "unknown_execution",
    ],
)
def test_incomplete_or_mismatched_commit_chain_is_not_confirmed(plan_state, failure):
    db, user, other, plan, approval = plan_state
    job = execute(plan_state)
    owner = user.id
    if failure == "foreign_owner":
        owner = other.id
    elif failure == "job_pending":
        job.status = "running"
    elif failure == "job_target":
        job.result_json = {**job.result_json, "plan_id": str(uuid.uuid4())}
    elif failure == "run_owner":
        db.get(models.AgentRun, uuid.UUID(job.result_json["agent_run_id"])).user_id = other.id
    elif failure == "missing_completed_at":
        job.completed_at = None
    elif failure == "task_owner":
        db.get(
            models.AgentTaskState, uuid.UUID(approval.context_json["responsibility_id"])
        ).user_id = other.id
    elif failure == "event_link":
        event = db.scalar(
            select(models.AgentTaskEvent).where(
                models.AgentTaskEvent.event_type == "responsibility.plan_adjusted"
            )
        )
        event.payload_json = {**event.payload_json, "approval_id": str(uuid.uuid4())}
    elif failure == "unknown_execution":
        approval.status = "outcome_unknown"
    else:
        changed = copy.deepcopy(plan.plan_json)
        changed["training_days"][0]["exercises"][0]["sets"] = 5
        plan.plan_json = changed
    db.commit()
    receipt = approved_plan_write_receipt(db, owner, approval.id)
    assert receipt["state"] == "unconfirmed"
    assert receipt["may_repeat_writes"] is False
    if failure == "unknown_execution":
        assert receipt["reason"] == "execution_not_confirmed"


def test_flushed_but_uncommitted_plan_commit_chain_is_not_visible(plan_state):
    db, user, _, _, approval = plan_state
    ApprovalManager(db).approve(str(approval.id))
    db.commit()
    job = db.get(models.BackgroundTask, approval.job_id)
    result = ApprovedPlanAdjustmentService(db).execute(job)
    job.status = "completed"
    job.result_json = result
    job.completed_at = datetime.now(timezone.utc)
    db.flush()
    receipt = approved_plan_write_receipt(db, user.id, approval.id)
    assert receipt["state"] == "unconfirmed"
    assert receipt["approval_status"] == "approved"
    assert db.in_transaction()
    db.rollback()
