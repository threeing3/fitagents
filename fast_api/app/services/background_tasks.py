"""Database-backed background task queue for expensive workloads."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import get_settings
from fast_api.app.core.metrics import (
    background_task_latency_seconds,
    background_task_queue_depth,
    background_tasks_total,
)
from fast_api.app.db import models
from fast_api.app.schemas.agent import PlanGenerateRequest
from fast_api.app.services.coach_agent import CoachAgentService


class BackgroundTaskQueue:
    """Small persistent queue that can later be replaced by Redis/Celery."""

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def enqueue(
        self,
        user_id: uuid.UUID,
        task_type: str,
        payload: dict[str, Any],
        max_attempts: int | None = None,
    ) -> models.BackgroundTask:
        task = models.BackgroundTask(
            user_id=user_id,
            task_type=task_type,
            status="queued",
            payload_json=payload,
            max_attempts=max_attempts or self.settings.background_task_max_attempts,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        background_tasks_total.inc(task_type=task.task_type, status="queued")
        self.update_queue_depth_metrics()
        return task

    def get_for_user(self, task_id: uuid.UUID, user_id: uuid.UUID) -> models.BackgroundTask | None:
        task = self.db.get(models.BackgroundTask, task_id)
        if task is None or task.user_id != user_id:
            return None
        return task

    def claim_next(self) -> models.BackgroundTask | None:
        statement = (
            select(models.BackgroundTask)
            .where(
                models.BackgroundTask.status == "queued",
                models.BackgroundTask.attempts < models.BackgroundTask.max_attempts,
            )
            .order_by(models.BackgroundTask.created_at)
            .limit(1)
        )
        if self.settings.database_url.startswith("postgresql"):
            statement = statement.with_for_update(skip_locked=True)
        task = self.db.scalars(statement).first()
        if task is None:
            return None
        task.status = "running"
        task.attempts += 1
        task.started_at = datetime.utcnow()
        task.error = None
        self.db.commit()
        self.db.refresh(task)
        background_tasks_total.inc(task_type=task.task_type, status="running")
        self.update_queue_depth_metrics()
        return task

    def mark_success(self, task: models.BackgroundTask, result: dict[str, Any], elapsed_seconds: float) -> None:
        task.status = "completed"
        task.result_json = result
        task.completed_at = datetime.utcnow()
        task.error = None
        self.db.commit()
        background_tasks_total.inc(task_type=task.task_type, status="completed")
        background_task_latency_seconds.observe(elapsed_seconds, task_type=task.task_type)
        self.update_queue_depth_metrics()

    def mark_failure(self, task: models.BackgroundTask, error: str, elapsed_seconds: float) -> None:
        task.status = "failed" if task.attempts >= task.max_attempts else "queued"
        task.error = error[:4000]
        task.completed_at = datetime.utcnow() if task.status == "failed" else None
        self.db.commit()
        background_tasks_total.inc(task_type=task.task_type, status=task.status)
        background_task_latency_seconds.observe(elapsed_seconds, task_type=task.task_type)
        self.update_queue_depth_metrics()

    def update_queue_depth_metrics(self) -> None:
        rows = self.db.execute(
            select(models.BackgroundTask.status, func.count(models.BackgroundTask.id))
            .group_by(models.BackgroundTask.status)
        ).all()
        seen = set()
        for status, count in rows:
            seen.add(status)
            background_task_queue_depth.set(count, status=status)
        for status in {"queued", "running", "completed", "failed"} - seen:
            background_task_queue_depth.set(0, status=status)


def run_one_background_task(db: Session) -> models.BackgroundTask | None:
    """Claim and execute one queued task. Returns the task if work was found."""
    queue = BackgroundTaskQueue(db)
    task = queue.claim_next()
    if task is None:
        return None

    start = time.perf_counter()
    try:
        result = _execute_task(db, task)
    except Exception as exc:
        queue.mark_failure(task, str(exc), time.perf_counter() - start)
    else:
        queue.mark_success(task, result, time.perf_counter() - start)
    return task


def _execute_task(db: Session, task: models.BackgroundTask) -> dict[str, Any]:
    if task.task_type == "plan.generate":
        service = CoachAgentService(db)
        payload = task.payload_json or {}
        plan = service.generate_plan(
            PlanGenerateRequest(
                user_id=task.user_id,
                force=bool(payload.get("force", False)),
                plan_days=int(payload.get("plan_days", 7)),
            )
        )
        return {
            "plan_id": str(plan.id),
            "status": plan.status,
            "user_id": str(plan.user_id),
        }
    if task.task_type == "eval.run":
        service = CoachAgentService(db)
        payload = task.payload_json or {}
        result = service.run_evals(
            suite_name=str(payload.get("suite_name", "mvp")),
            persist_cases=bool(payload.get("persist_cases", True)),
        )
        return {
            "suite_name": result.suite_name,
            "total": result.total,
            "passed": result.passed,
            "score": result.score,
            "log_path": result.log_path,
        }
    raise ValueError(f"Unsupported background task type: {task.task_type}")
