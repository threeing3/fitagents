"""Real database lifecycle and worker replay with synthetic plans and owners."""

import asyncio
import copy
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fast_api.app.api.approval_api import approval_router
from fast_api.app.api.responsibility_api import responsibility_router
from fast_api.app.core.auth import get_current_user
from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db
from fast_api.app.schemas.agent import DailyCheckinRequest, PlanAdjustRequest, PlanGenerateRequest
from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.approval_manager import ApprovalManager
from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.memory_conflict_resolver import MemoryConflictResolver
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.plan_adjustment_policy import PlanAdjustmentPolicy
from fast_api.app.services.plan_writes import StalePlanWriteError
from fast_api.app.services.responsibilities import ResponsibilityService


@pytest.fixture
def state():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        user = models.User(
            email="approval-owner@example.com", password_hash="test", timezone="Asia/Shanghai"
        )
        other = models.User(email="approval-other@example.com", password_hash="test")
        db.add_all([user, other])
        db.flush()
        task = ResponsibilityService(db).create_weekly(user.id, "复盘自动，改计划先问")
        tomorrow = datetime.now(timezone.utc).astimezone(
            ZoneInfo(user.timezone)
        ).date() + timedelta(days=1)
        baseline = {
            "training_days": [
                {
                    "date": tomorrow.isoformat(),
                    "exercises": [{"name": "划船", "sets": 4, "reps": 10}],
                },
                {
                    "date": (tomorrow + timedelta(days=1)).isoformat(),
                    "exercises": [{"name": "深蹲", "sets": 3, "reps": 8}],
                },
            ],
            "nutrition": {"protein": 120},
        }
        plan = models.TrainingPlan(user_id=user.id, status="active", plan_json=baseline)
        db.add(plan)
        db.commit()
        yield db, factory, user, other, task, plan
    engine.dispose()


def proposal(state):
    db, _, user, _, task, plan = state
    result = ApprovedPlanAdjustmentService(db).propose(
        user.id, task.id, plan.id, plan.plan_json["training_days"][0]["date"], 1, "观察疲劳后减量"
    )
    db.commit()
    return result


def approve(state, result):
    db = state[0]
    manager = ApprovalManager(db)
    decision = manager.approve(result["approval"]["approval_id"])
    db.commit()
    return decision


def checkin_service(db):
    return CoachAgentService(
        db,
        ModelProvider(
            Settings(
                _env_file=None,
                llm_provider="offline",
                embedding_provider="offline",
                use_pgvector=False,
                jwt_secret_key="isolated-adjustment-policy-test",
            )
        ),
    )


def future_plan(state):
    db, _, user, _, _, plan = state
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(user.timezone)).date()
    plan.plan_json = {
        "training_days": [
            {
                "date": (today + timedelta(days=1)).isoformat(),
                "exercises": [{"name": "划船", "sets": 4}],
            },
            {
                "date": (today + timedelta(days=2)).isoformat(),
                "exercises": [{"name": "深蹲", "sets": 3}],
            },
        ],
        "nutrition": {"protein": 120},
    }
    db.commit()
    return copy.deepcopy(plan.plan_json)


def test_checkin_drafts_only_then_approval_changes_one_day_and_registers_evaluation(state):
    db, factory, user, _, _, plan = state
    baseline = future_plan(state)
    service = checkin_service(db)
    request = DailyCheckinRequest(
        user_id=user.id, fatigue=9, sleep_hours=4, idempotency_key="checkin-proposal-once"
    )
    response = service.record_daily_checkin(request)
    assert response["auto_adjusted"] is False
    assert response["adjustment_proposal"]["status"] == "waiting_approval"
    assert (
        "本次打卡"
        in ApprovalManager(db)
        .get(response["adjustment_proposal"]["approval_id"])
        .input_summary["reason"]
    )
    assert plan.plan_json == baseline and run_one_background_task(db) is None
    assert service.record_daily_checkin(request)["idempotent_replay"] is True
    assert len(ApprovalManager(db).get_pending(user.id)) == 1
    assert db.scalars(select(models.DecisionEvaluationPlan)).all() == []
    ApprovalManager(db).approve(response["adjustment_proposal"]["approval_id"])
    db.commit()
    with factory() as fresh:
        job = run_one_background_task(fresh)
        assert job.status == "completed" and job.result_json["verified"]
        persisted = fresh.get(models.TrainingPlan, plan.id)
        assert persisted.plan_json["training_days"][0]["exercises"][0]["sets"] == 3
        assert persisted.plan_json["training_days"][1] == baseline["training_days"][1]
        assert persisted.plan_json["nutrition"] == baseline["nutrition"]
        evaluation = fresh.scalar(
            select(models.DecisionEvaluationPlan).where(
                models.DecisionEvaluationPlan.decision_id
                == uuid.UUID(job.result_json["decision_id"])
            )
        )
        assert evaluation.implementation_status == "unknown"
        assert evaluation.expected_action["requires_user_confirmation"] is True
        assert run_one_background_task(fresh) is None


@pytest.mark.parametrize("approved", [False, True])
def test_checkin_correction_invalidates_pending_or_queued_proposal(state, approved):
    db, _, user, _, _, plan = state
    baseline = future_plan(state)
    service = checkin_service(db)
    first = service.record_daily_checkin(DailyCheckinRequest(user_id=user.id, fatigue=9))
    approval_id = first["adjustment_proposal"]["approval_id"]
    if approved:
        ApprovalManager(db).approve(approval_id)
        db.commit()
    corrected = service.record_daily_checkin(DailyCheckinRequest(user_id=user.id, fatigue=2))
    assert approval_id in corrected["invalidated_approvals"]
    assert ApprovalManager(db).get(approval_id).status == "stale"
    assert run_one_background_task(db) is None
    assert plan.plan_json == baseline


@pytest.mark.parametrize("change", ["goal", "equipment", "risk", "recovery", "profile_created"])
def test_worker_rechecks_dependencies_even_without_invalidation_hook(state, change):
    db, _, user, _, _, plan = state
    result = proposal(state)
    approve(state, result)
    baseline = copy.deepcopy(plan.plan_json)
    if change in {"goal", "equipment", "profile_created"}:
        profile = models.UserProfile(user_id=user.id, goal="muscle_gain")
        if change == "equipment":
            profile.equipment_available = ["bodyweight"]
        db.add(profile)
    elif change == "risk":
        db.add(models.RiskNote(user_id=user.id, risk_type="pain", description="new evidence"))
    else:
        db.add(models.RecoveryLog(user_id=user.id, fatigue_score=3))
    db.commit()
    job = run_one_background_task(db)
    assert job.result_json == {"status": "skipped", "reason": "dependencies_changed"}
    assert plan.plan_json == baseline
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "stale"


def test_goal_correction_eagerly_invalidates_without_deleting_history(state):
    db, _, user, _, _, plan = state
    profile = models.UserProfile(user_id=user.id, goal="fat_loss")
    db.add(profile)
    db.commit()
    result = proposal(state)
    profile.goal = "muscle_gain"
    MemoryConflictResolver(db).apply_corrections(
        user.id, [{"field": "goal", "action": "set", "value": "muscle_gain"}], "改成增肌"
    )
    db.commit()
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "stale"
    assert db.get(models.TrainingPlan, plan.id) is not None


def test_unrelated_user_change_does_not_invalidate_owner(state):
    db, _, user, other, _, _ = state
    result = proposal(state)
    db.add(models.UserProfile(user_id=other.id, goal="muscle_gain"))
    db.commit()
    assert PlanAdjustmentPolicy(db).invalidate_changed(user.id) == []
    approve(state, result)
    assert run_one_background_task(db).result_json["verified"]


def test_legacy_whole_plan_adjustment_cannot_bypass_ask_contract(state):
    db, _, user, _, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    with pytest.raises(ValueError, match="separate reviewed proposal"):
        checkin_service(db).adjust_plan(PlanAdjustRequest(user_id=user.id, reason="rebuild"))
    db.rollback()
    assert plan.status == "active" and plan.plan_json == baseline


def test_force_generation_cannot_bypass_existing_plan_ask_contract(state):
    db, _, user, _, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    with pytest.raises(ValueError, match="separate reviewed proposal"):
        checkin_service(db).generate_plan(PlanGenerateRequest(user_id=user.id, force=True))
    db.rollback()
    assert plan.status == "active" and plan.plan_json == baseline


def test_checkin_failure_rolls_back_proposal_and_preserves_original_plan(state, monkeypatch):
    from fast_api.app.services.agent_task_state import AgentTaskStateService

    db, _, user, _, _, plan = state
    baseline = future_plan(state)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected after proposal before checkin commit")

    monkeypatch.setattr(AgentTaskStateService, "update_from_checkin", fail)
    with pytest.raises(RuntimeError, match="after proposal"):
        checkin_service(db).record_daily_checkin(DailyCheckinRequest(user_id=user.id, fatigue=9))
    db.rollback()
    assert db.scalars(select(models.PendingApproval)).all() == []
    assert db.scalars(select(models.BackgroundTask)).all() == []
    assert db.scalars(select(models.DailyCheckin)).all() == []
    assert plan.plan_json == baseline


def test_current_risk_blocks_new_proposal_not_checkin_record(state):
    db, _, user, _, _, plan = state
    baseline = future_plan(state)
    db.add(models.SymptomLog(user_id=user.id, symptom_type="pain", status="active"))
    db.commit()
    response = checkin_service(db).record_daily_checkin(
        DailyCheckinRequest(user_id=user.id, fatigue=9)
    )
    assert response["adjustment_proposal"]["reason"] == "risk_requires_separate_review"
    assert response["checkin_id"] and plan.plan_json == baseline


def test_pending_survives_process_style_session_recreation(state):
    result = proposal(state)
    _, factory, user, _, _, _ = state
    with factory() as fresh:
        pending = ApprovalManager(fresh).get_pending(user.id)
        assert len(pending) == 1
        assert pending[0].approval_id == result["approval"]["approval_id"]
        assert pending[0].status == "pending"


def test_history_exposes_durable_execution_not_just_approval_and_is_owner_scoped(state):
    db, _, user, other, _, _ = state
    result = proposal(state)
    app = FastAPI()
    app.include_router(approval_router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/v1/approvals/history").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: user
        pending = client.get("/v1/approvals/pending").json()[0]
        assert pending["expires_at"]
        row = client.get("/v1/approvals/history").json()[0]
        assert row["status"] == "pending" and row["job_status"] == "waiting_approval"
        approve(state, result)
        approved = client.get("/v1/approvals/history").json()[0]
        assert approved["status"] == "approved" and approved["job_status"] == "queued"
        assert not approved["result"].get("verified")
        run_one_background_task(db)
        executed = client.get("/v1/approvals/history").json()[0]
        assert executed["status"] == "executed" and executed["job_status"] == "completed"
        assert executed["result"]["verified"] is True
        identity = uuid.UUID(executed["execution_trace_run_id"])
        run = db.get(models.AgentRun, identity)
        assert run.user_id == user.id and run.run_type == "background.task"
        app.dependency_overrides[get_current_user] = lambda: other
        assert client.get("/v1/approvals/history").json() == []


def test_history_expires_pending_and_keeps_failed_and_stale_actions(state):
    db, _, user, _, _, _ = state
    result = proposal(state)
    row = db.get(models.PendingApproval, uuid.UUID(result["approval"]["approval_id"]))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    app = FastAPI()
    app.include_router(approval_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        item = client.get("/v1/approvals/history").json()[0]
        assert item["status"] == "expired" and item["job_status"] == "cancelled"
        row.status = "failed"
        job = db.get(models.BackgroundTask, uuid.UUID(result["job_id"]))
        job.status = "failed"
        job.error = "synthetic write rolled back"
        db.commit()
        failed = client.get("/v1/approvals/history").json()[0]
        assert failed["error"] == "synthetic write rolled back"
        row.status = "stale"
        job.result_json = {"status": "skipped", "reason": "responsibility_or_plan_changed"}
        db.commit()
        stale = client.get("/v1/approvals/history").json()[0]
        assert stale["status"] == "stale" and stale["result"]["status"] == "skipped"


def test_no_write_before_approval_then_same_job_executes_once(state):
    db, factory, user, _, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    result = proposal(state)
    assert run_one_background_task(db) is None
    assert plan.plan_json == baseline
    assert approve(state, result).status == "approved"
    with factory() as fresh:
        job = run_one_background_task(fresh)
        assert str(job.id) == result["job_id"]
        assert job.status == "completed"
        assert job.result_json["verified"] is True
        assert run_one_background_task(fresh) is None
        persisted = fresh.get(models.TrainingPlan, plan.id)
        assert persisted.plan_json["training_days"][0]["exercises"][0]["sets"] == 3
        assert persisted.plan_json["training_days"][1] == baseline["training_days"][1]
        assert persisted.plan_json["nutrition"] == baseline["nutrition"]
        approval = ApprovalManager(fresh).get(result["approval"]["approval_id"])
        assert approval.status == "executed"
        assert (
            len(
                fresh.scalars(
                    select(models.AgentRun).where(
                        models.AgentRun.user_id == user.id,
                        models.AgentRun.run_type != "background.task",
                    )
                ).all()
            )
            == 1
        )
        assert ApprovalManager(fresh).approve(approval.approval_id) is None


@pytest.mark.parametrize(
    "change", ["pause", "cancel", "plan_changed", "expired", "authority_changed"]
)
def test_changed_context_blocks_previously_approved_action(state, change):
    db, _, user, _, task, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    result = proposal(state)
    approve(state, result)
    if change in {"pause", "cancel"}:
        ResponsibilityService(db).transition(task.id, user.id, change)
    elif change == "plan_changed":
        plan.plan_json = {**plan.plan_json, "nutrition": {"protein": 130}}
        baseline = copy.deepcopy(plan.plan_json)
    else:
        spec = dict(task.constraints["responsibility"])
        if change == "expired":
            spec["ends_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        else:
            spec["authority"] = {**spec["authority"], "adjust_plan": "deny"}
        task.constraints = {**task.constraints, "responsibility": spec}
    db.commit()
    job = run_one_background_task(db)
    assert job.result_json["status"] == "skipped"
    assert plan.plan_json == baseline
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "stale"


def test_denial_cancels_job_without_write(state):
    db, _, _, _, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    result = proposal(state)
    ApprovalManager(db).deny(result["approval"]["approval_id"], "不改")
    db.commit()
    assert run_one_background_task(db) is None
    assert plan.plan_json == baseline
    assert db.get(models.BackgroundTask, uuid.UUID(result["job_id"])).status == "cancelled"


def test_expired_approval_cannot_be_approved(state):
    db = state[0]
    result = proposal(state)
    row = db.get(models.PendingApproval, uuid.UUID(result["approval"]["approval_id"]))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert ApprovalManager(db).approve(str(row.id)) is None
    db.commit()
    assert row.status == "expired"
    assert run_one_background_task(db) is None


def test_foreign_user_api_cannot_see_or_decide_approval(state):
    db, _, _, other, _, _ = state
    result = proposal(state)
    app = FastAPI()
    app.include_router(approval_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: other
    with TestClient(app) as client:
        assert client.get("/v1/approvals/pending").json() == []
        assert (
            client.post(
                "/v1/approvals/decide",
                json={"approval_id": result["approval"]["approval_id"], "action": "approve"},
            ).status_code
            == 403
        )


def test_unified_owner_isolation_read_approve_execute_and_journal(state):
    from types import SimpleNamespace

    from fast_api.app.api.coach_platform import coach_router
    from fast_api.app.services.execution_events import public_run_events

    db, factory, user, other, responsibility, plan = state
    baseline = future_plan(state)
    day_date = baseline["training_days"][0]["date"]
    result = ApprovedPlanAdjustmentService(db).propose(
        user.id, responsibility.id, plan.id, day_date, 1, "合成隔离回放"
    )
    service = checkin_service(db)
    session = service.create_session(user.id, "隔离回放", "合成账号")
    assistant = service._save_message(
        session.id, user.id, "assistant", "受限变更待批准，尚未执行。"
    )
    run = models.AgentRun(
        user_id=user.id,
        session_id=session.id,
        run_type="responsibility_command",
        status="completed",
        nodes=[
            {"type": "ResponsibilityCommand", "message_id": str(assistant.id)},
            {
                "type": "execution_event",
                "name": "owner.boundary",
                "status": "completed",
                "source": "rule",
                "details": {},
            },
        ],
    )
    db.add(run)
    db.commit()
    assert public_run_events(db, run, other.id) == []
    app = FastAPI()
    app.include_router(approval_router)
    app.include_router(coach_router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: other
    with TestClient(app) as client:
        assert client.get(f"/v1/chat/sessions/{session.id}/messages").status_code == 403
        assert client.get(f"/v1/tasks/{result['job_id']}").status_code == 404
        assert client.get("/v1/approvals/pending").json() == []
        assert client.get("/v1/approvals/history").json() == []
        assert (
            client.post(
                "/v1/approvals/decide",
                json={"approval_id": result["approval"]["approval_id"], "action": "approve"},
            ).status_code
            == 403
        )
        forged_job = SimpleNamespace(
            user_id=other.id, payload_json={"approval_id": result["approval"]["approval_id"]}
        )
        with pytest.raises(ValueError, match="owner does not match"):
            ApprovedPlanAdjustmentService(db).execute(forged_job)
        db.rollback()
        with factory() as fresh:
            assert fresh.get(models.TrainingPlan, plan.id).plan_json == baseline
            approval = fresh.get(
                models.PendingApproval, uuid.UUID(result["approval"]["approval_id"])
            )
            assert approval.status == "pending"
            assert (
                fresh.get(models.BackgroundTask, uuid.UUID(result["job_id"])).status
                == "waiting_approval"
            )
        app.dependency_overrides[get_current_user] = lambda: user
        history = client.get(f"/v1/chat/sessions/{session.id}/messages").json()
        assert history[0]["execution_events"][0]["name"] == "owner.boundary"
        assert (
            client.post(
                "/v1/approvals/decide",
                json={"approval_id": result["approval"]["approval_id"], "action": "approve"},
            ).status_code
            == 200
        )
        completed = run_one_background_task(db, user_id=user.id)
        assert completed.status == "completed"
        db.refresh(plan)
        assert plan.plan_json["training_days"][0]["exercises"][0]["sets"] == 3
        assert plan.plan_json["training_days"][1] == baseline["training_days"][1]
        assert plan.plan_json["nutrition"] == baseline["nutrition"]


def test_runtime_rejects_parameter_change_and_missing_owner(state):
    db, _, user, _, _, _ = state
    manager = ApprovalManager(db)
    called = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="test.write",
            description="test",
            permission_level="write",
            side_effects=True,
            idempotency_key_fields=["request_key"],
        ),
        lambda payload: called.append(payload) or {"ok": True},
    )
    with pytest.raises(ValueError, match="owner"):
        asyncio.run(registry.execute_awaiting_approval("test.write", {"request_key": "x"}, manager))
    result, executed = asyncio.run(
        registry.execute_awaiting_approval(
            "test.write", {"request_key": "x"}, manager, user_id=user.id
        )
    )
    assert not executed
    approval_id = result.output_json["approval_id"]
    manager.approve(approval_id)
    db.commit()
    with pytest.raises(ValueError, match="parameters"):
        asyncio.run(
            registry.execute_awaiting_approval(
                "test.write",
                {"request_key": "changed"},
                manager,
                user_id=user.id,
                approval_id=approval_id,
            )
        )
    assert called == []


def test_api_owner_approval_requeues_durable_job(state):
    db, _, user, _, _, _ = state
    result = proposal(state)
    app = FastAPI()
    app.include_router(approval_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        assert len(client.get("/v1/approvals/pending").json()) == 1
        assert (
            client.post(
                "/v1/approvals/decide",
                json={"approval_id": result["approval"]["approval_id"], "action": "approve"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/approvals/decide",
                json={"approval_id": result["approval"]["approval_id"], "action": "approve"},
            ).status_code
            == 409
        )
        assert client.get("/v1/approvals/stats").json()["auto_approved_tools"] == []
    assert db.get(models.BackgroundTask, uuid.UUID(result["job_id"])).status == "queued"


def test_proposal_rejects_unscoped_or_excessive_change(state):
    db, _, user, _, task, plan = state
    service = ApprovedPlanAdjustmentService(db)
    with pytest.raises(ValueError, match="dated"):
        absent = (
            datetime.fromisoformat(plan.plan_json["training_days"][1]["date"]).date()
            + timedelta(days=1)
        ).isoformat()
        service.propose(user.id, task.id, plan.id, absent, 1, "test")
    with pytest.raises(ValueError, match="at least"):
        service.propose(
            user.id, task.id, plan.id, plan.plan_json["training_days"][1]["date"], 3, "test"
        )
    assert not db.scalars(select(models.PendingApproval)).all()


def test_proposal_api_to_approval_api_to_worker(state):
    db, _, user, _, task, plan = state
    app = FastAPI()
    app.include_router(approval_router)
    app.include_router(responsibility_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        response = client.post(
            f"/v1/responsibilities/{task.id}/plan-proposal",
            json={
                "plan_id": str(plan.id),
                "day_date": plan.plan_json["training_days"][0]["date"],
                "reduce_by": 1,
                "reason": "复盘后减量",
            },
        )
        assert response.status_code == 200
        assert (
            client.post(
                "/v1/approvals/decide",
                json={
                    "approval_id": response.json()["approval"]["approval_id"],
                    "action": "approve",
                },
            ).status_code
            == 200
        )
    assert run_one_background_task(db).result_json["status"] == "adjusted"


def test_save_time_conflict_invalidates_approval_without_unknown_result(state, monkeypatch):
    db, _, user, _, _, plan = state
    result = proposal(state)
    approve(state, result)
    before = copy.deepcopy(plan.plan_json)
    calls = []

    def reject(*_args):
        calls.append("conditional_write")
        raise StalePlanWriteError("Injected save-time conflict")

    monkeypatch.setattr(
        "fast_api.app.services.approved_plan_adjustments.replace_plan_content", reject
    )
    job = db.get(models.BackgroundTask, uuid.UUID(result["job_id"]))
    execution = ApprovedPlanAdjustmentService(db).execute(job)
    db.commit()
    assert execution == {"status": "skipped", "reason": "plan_changed_before_write"}
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "stale"
    assert plan.plan_json == before
    assert calls == ["conditional_write"]
    assert not db.scalar(
        select(models.AgentDecision).where(
            models.AgentDecision.user_id == user.id,
            models.AgentDecision.decision_type == "approved_plan_adjustment",
        )
    )


def test_failure_after_tool_write_rolls_back_all_and_marks_failed(state, monkeypatch):
    db, _, _, _, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    result = proposal(state)
    approve(state, result)
    original = ToolRegistry.execute

    async def lose_result(self, name, payload):
        outcome = await original(self, name, payload)
        outcome.status = "outcome_unknown"
        outcome.error = "injected lost output after transactional write"
        return outcome

    monkeypatch.setattr(ToolRegistry, "execute", lose_result)
    job = run_one_background_task(db)
    assert job.status == "failed"
    db.refresh(plan)
    assert plan.plan_json == baseline
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "failed"
    events = ApprovalManager(db).get(result["approval"]["approval_id"]).context["execution_events"]
    assert events[-1]["name"] == "execution.rollback" and events[-1]["status"] == "failed"
    assert not any(entry["name"] in {"execution.verify", "execution.complete"} for entry in events)
    assert run_one_background_task(db) is None
    runs = db.scalars(select(models.AgentRun)).all()
    assert len(runs) == 1
    assert runs[0].run_type == "background.task" and runs[0].status == "failed"


def test_interrupted_atomic_adjustment_reconciles_without_retry(state):
    from fast_api.app.services.background_tasks import BackgroundTaskQueue
    from fast_api.app.services.task_recovery import TaskRecoveryService

    result = proposal(state)
    approve(state, result)
    db, _, user, other, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    job = BackgroundTaskQueue(db).claim_next(user_id=user.id)
    with pytest.raises(ValueError, match="Task not found"):
        TaskRecoveryService(db).reconcile(job.id, other.id, 1)
    with pytest.raises(ValueError, match="attempt changed"):
        TaskRecoveryService(db).reconcile(job.id, user.id, 2)
    db.rollback()
    outcome = TaskRecoveryService(db).reconcile(job.id, user.id, 1)
    db.commit()
    assert outcome == {
        "status": "failed",
        "changed": True,
        "requeued": False,
        "known_uncommitted": True,
    }
    assert plan.plan_json == baseline
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "failed"
    assert run_one_background_task(db, user_id=user.id) is None
    assert TaskRecoveryService(db).reconcile(job.id, user.id, 1)["changed"] is False


def test_interrupted_adjustment_with_changed_plan_remains_unknown(state):
    from fast_api.app.services.background_tasks import BackgroundTaskQueue
    from fast_api.app.services.task_recovery import TaskRecoveryService

    result = proposal(state)
    approve(state, result)
    db, _, user, _, _, plan = state
    job = BackgroundTaskQueue(db).claim_next(user_id=user.id)
    plan.plan_json = {"independent_new_content": True}
    db.commit()
    outcome = TaskRecoveryService(db).reconcile(job.id, user.id, 1)
    db.commit()
    assert outcome["status"] == "outcome_unknown" and not outcome["requeued"]
    assert plan.plan_json == {"independent_new_content": True}
    assert ApprovalManager(db).get(result["approval"]["approval_id"]).status == "outcome_unknown"
    assert run_one_background_task(db, user_id=user.id) is None


def test_reconciliation_does_not_downgrade_completed_execution(state):
    from fast_api.app.services.task_recovery import TaskRecoveryService

    result = proposal(state)
    approve(state, result)
    db, _, user, _, _, plan = state
    job = run_one_background_task(db, user_id=user.id)
    saved = copy.deepcopy(plan.plan_json)
    outcome = TaskRecoveryService(db).reconcile(job.id, user.id, 1)
    assert outcome == {"status": "completed", "changed": False, "requeued": False}
    assert plan.plan_json == saved


def test_claimed_job_rechecks_reconciled_status_before_execution(state, monkeypatch):
    from fast_api.app.services import background_tasks

    result = proposal(state)
    approve(state, result)
    db, _, user, _, _, plan = state
    baseline = copy.deepcopy(plan.plan_json)
    original = background_tasks.BackgroundTaskQueue.claim_next

    def claim_then_reconcile(queue, **kwargs):
        job = original(queue, **kwargs)
        job.status = "failed"
        queue.db.commit()
        return job

    executed = []
    monkeypatch.setattr(background_tasks.BackgroundTaskQueue, "claim_next", claim_then_reconcile)
    monkeypatch.setattr(background_tasks, "_execute_task", lambda *_: executed.append(True))
    job = run_one_background_task(db, user_id=user.id)
    assert job.status == "failed" and executed == []
    assert plan.plan_json == baseline
