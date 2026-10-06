"""Synthetic state-level replay; no live models or existing business databases."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fast_api.app.api.responsibility_api import responsibility_router
from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.responsibilities import (
    JOB_TYPE,
    ResponsibilityService,
    next_weekly,
)

UTC = timezone.utc
START = datetime(2026, 9, 30, 8, tzinfo=UTC)
DUE = datetime(2026, 10, 4, 10, tzinfo=UTC)


@pytest.fixture
def business():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        user = models.User(
            email="responsibility@example.com", password_hash="test", timezone="Asia/Shanghai"
        )
        db.add(user)
        db.commit()
        yield db, user, factory
    engine.dispose()


def create(db, user, **kwargs):
    task = ResponsibilityService(db).create_weekly(
        user.id, "四周训练跟踪，每周日复盘；修改计划先问我", now=START, **kwargs
    )
    db.commit()
    return task


def enqueue(db, when=DUE):
    result = ResponsibilityService(db).scan_due(when)
    db.commit()
    return result


def test_configuration_survives_new_session(business):
    db, user, factory = business
    task = create(db, user)
    with factory() as fresh:
        state = ResponsibilityService(fresh).get(task.id, user.id)
        spec = state.constraints["responsibility"]
        assert spec["next_wake_at"] == DUE.isoformat()
        assert spec["ends_at"] == (START + timedelta(weeks=4)).isoformat()
        assert spec["authority"]["adjust_plan"] == "ask"
        assert spec["authority"]["delete_record"] == "deny"


def test_scan_enqueues_once_and_advances_atomically(business):
    db, user, _ = business
    create(db, user)
    assert enqueue(db) == {"queued": 1, "expired": 0}
    assert enqueue(db)["queued"] == 0
    jobs = db.scalars(select(models.BackgroundTask)).all()
    assert len(jobs) == 1
    assert jobs[0].max_attempts == 1
    assert jobs[0].task_type == JOB_TYPE


def test_rolled_back_scan_can_enqueue_again_without_duplicates(business):
    db, user, _ = business
    create(db, user)
    ResponsibilityService(db).scan_due(DUE)
    db.rollback()
    assert enqueue(db)["queued"] == 1
    assert len(db.scalars(select(models.BackgroundTask)).all()) == 1


def test_late_scan_coalesces_to_latest_due_slot(business):
    db, user, _ = business
    create(db, user)
    assert enqueue(db, DUE + timedelta(days=15))["queued"] == 1
    job = db.scalar(select(models.BackgroundTask))
    assert job.payload_json["scheduled_at"] == (DUE + timedelta(days=14)).isoformat()


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_inactive_responsibility_blocks_queued_job(business, action):
    db, user, _ = business
    task = create(db, user)
    enqueue(db)
    service = ResponsibilityService(db)
    service.transition(task.id, user.id, action, now=DUE)
    db.commit()
    job = db.scalar(select(models.BackgroundTask))
    result = service.execute_review(job, now=DUE)
    assert result["status"] == "skipped"
    assert not db.scalars(select(models.LongTermMemory)).all()


def test_resume_invalidates_old_job_and_sets_future_wake(business):
    db, user, _ = business
    task = create(db, user)
    enqueue(db)
    service = ResponsibilityService(db)
    service.transition(task.id, user.id, "pause", now=DUE)
    service.transition(task.id, user.id, "resume", now=DUE + timedelta(hours=1))
    db.commit()
    job = db.scalar(select(models.BackgroundTask))
    assert service.execute_review(job, now=DUE + timedelta(hours=1))["status"] == "skipped"
    assert (
        task.constraints["responsibility"]["next_wake_at"] == (DUE + timedelta(days=7)).isoformat()
    )


def test_expiry_also_terminates_paused_responsibility(business):
    db, user, _ = business
    task = create(db, user)
    ResponsibilityService(db).transition(task.id, user.id, "pause", now=START)
    db.commit()
    assert enqueue(db, START + timedelta(weeks=4))["expired"] == 1
    assert task.status == "expired"
    assert task.constraints["responsibility"]["next_wake_at"] is None
    assert enqueue(db, START + timedelta(weeks=5))["expired"] == 0


def test_foreign_user_cannot_control_responsibility(business):
    db, user, _ = business
    task = create(db, user)
    with pytest.raises(ValueError, match="not found"):
        ResponsibilityService(db).transition(task.id, uuid.uuid4(), "cancel", now=START)


def test_schedule_handles_dst_gap_and_ambiguous_hour():
    gap = next_weekly(datetime(2026, 3, 7, tzinfo=UTC), "America/New_York", 6, 2, 30)
    assert gap == datetime(2026, 3, 15, 6, 30, tzinfo=UTC)
    ambiguous = next_weekly(datetime(2026, 10, 31, tzinfo=UTC), "America/New_York", 6, 1, 30)
    assert ambiguous == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_worker_executes_actual_reflection_and_persists_result(business):
    db, user, factory = business
    # Historical task ensures the actual worker's present clock is inside its duration.
    now = datetime.now(UTC)
    task = ResponsibilityService(db).create_weekly(user.id, "每周复盘", now=now - timedelta(days=8))
    db.commit()
    assert enqueue(db, now)["queued"] == 1
    job = run_one_background_task(db)
    assert job.status == "completed"
    assert job.result_json["status"] == "reviewed"
    with factory() as fresh:
        state = ResponsibilityService(fresh).get(task.id, user.id)
        assert state.progress_json["reviews_completed"] == 1
        assert "last_review" in state.progress_json
        assert (
            len(
                fresh.scalars(
                    select(models.AgentTaskEvent).where(
                        models.AgentTaskEvent.event_type == "responsibility.reviewed"
                    )
                ).all()
            )
            == 1
        )
    assert run_one_background_task(db) is None
    assert not db.scalars(select(models.TrainingPlan)).all()


def test_authority_denial_prevents_reflection(business):
    db, user, _ = business
    task = create(db, user)
    enqueue(db)
    spec = dict(task.constraints["responsibility"])
    spec["authority"] = {**spec["authority"], "create_summary": "deny"}
    task.constraints = {"responsibility": spec}
    db.commit()
    job = db.scalar(select(models.BackgroundTask))
    assert (
        ResponsibilityService(db).execute_review(job, now=DUE)["reason"] == "authority_not_granted"
    )


def test_api_creation_list_and_pause_use_authenticated_owner(business):
    db, user, _ = business
    app = FastAPI()
    app.include_router(responsibility_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        response = client.post(
            "/v1/responsibilities/weekly-review",
            json={"source_instruction": "每周复盘，不改计划", "weeks": 4},
        )
        assert response.status_code == 200
        task_id = response.json()["id"]
        assert len(client.get("/v1/responsibilities").json()) == 1
        assert (
            client.post(
                f"/v1/responsibilities/{task_id}/lifecycle", json={"action": "pause"}
            ).json()["status"]
            == "paused"
        )
        assert (
            client.post(
                f"/v1/responsibilities/{task_id}/lifecycle", json={"action": "pause"}
            ).status_code
            == 409
        )


def test_api_does_not_accept_invalid_duration(business):
    db, user, _ = business
    app = FastAPI()
    app.include_router(responsibility_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/responsibilities/weekly-review",
                json={"source_instruction": "复盘", "weeks": 0},
            ).status_code
            == 422
        )


def test_review_window_uses_local_dates_and_excludes_future_records(business):
    db, user, _ = business
    task = create(db, user)
    for when, name in [
        (START - timedelta(seconds=1), "before_responsibility"),
        (DUE - timedelta(hours=1), "inside_window"),
        (DUE + timedelta(hours=1), "future_record"),
    ]:
        db.add(models.WorkoutLog(user_id=user.id, performed_at=when, workout_name=name))
    db.commit()
    enqueue(db)
    job = db.scalar(select(models.BackgroundTask))
    result = ResponsibilityService(db).execute_review(job, now=DUE)
    db.commit()
    assert result["status"] == "reviewed"
    memories = db.scalars(select(models.LongTermMemory)).all()
    assert memories
    serialized = str([memory.evidence for memory in memories])
    inside = db.scalar(
        select(models.WorkoutLog).where(models.WorkoutLog.workout_name == "inside_window")
    )
    future = db.scalar(
        select(models.WorkoutLog).where(models.WorkoutLog.workout_name == "future_record")
    )
    assert str(inside.id) in serialized
    assert str(future.id) not in serialized
    assert task.progress_json["reviews_completed"] == 1


def test_failed_reflection_rolls_back_partial_state_and_is_not_retried(business, monkeypatch):
    from fast_api.app.services.reflection_service import ReflectionService

    db, user, _ = business
    now = datetime.now(UTC)
    task = ResponsibilityService(db).create_weekly(user.id, "复盘", now=now - timedelta(days=8))
    db.commit()
    enqueue(db, now)

    def fail(self, *args, **kwargs):
        self.db.add(
            models.AgentTaskEvent(
                task_id=task.id,
                user_id=user.id,
                event_type="partial.review",
                summary="must rollback",
                payload_json={},
            )
        )
        self.db.flush()
        raise RuntimeError("injected review failure")

    monkeypatch.setattr(ReflectionService, "reflect_weekly", fail)
    job = run_one_background_task(db)
    assert job.status == "failed"
    assert job.attempts == 1
    assert run_one_background_task(db) is None
    assert not db.scalars(
        select(models.AgentTaskEvent).where(models.AgentTaskEvent.event_type == "partial.review")
    ).all()


def test_invalid_timezone_rejected_without_creating_responsibility(business):
    db, user, _ = business
    user.timezone = "Invalid/Timezone"
    db.commit()
    with pytest.raises(ValueError, match="timezone"):
        create(db, user)
    assert not db.scalars(select(models.AgentTaskState)).all()


def test_sunday_evening_record_is_in_next_continuous_window(business):
    db, user, _ = business
    create(db, user)
    log = models.WorkoutLog(
        user_id=user.id, performed_at=DUE + timedelta(hours=1), workout_name="after_sunday_review"
    )
    db.add(log)
    db.commit()
    enqueue(db, DUE + timedelta(days=7))
    job = db.scalar(select(models.BackgroundTask))
    result = ResponsibilityService(db).execute_review(job, now=DUE + timedelta(days=7))
    assert result["created_count"] > 0
    assert str(log.id) in str(
        [memory.evidence for memory in db.scalars(select(models.LongTermMemory)).all()]
    )
