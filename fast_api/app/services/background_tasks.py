"""Database-backed background task queue for expensive workloads."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
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
from fast_api.app.services.durable_stream_journal import DurableStreamJournal
from fast_api.app.services.model_call_records import model_origin, model_recording

logger = logging.getLogger(__name__)


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
            max_attempts=1
            if task_type
            in {"plan.generate", "responsibility.plan_adjustment", "responsibility.weekly_review"}
            else max_attempts or self.settings.background_task_max_attempts,
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

    def claim_next(self, *, user_id: uuid.UUID | None = None) -> models.BackgroundTask | None:
        statement = (
            select(models.BackgroundTask)
            .where(
                models.BackgroundTask.status == "queued",
                models.BackgroundTask.attempts < models.BackgroundTask.max_attempts,
            )
            .order_by(models.BackgroundTask.created_at)
            .limit(1)
        )
        if user_id is not None:
            statement = statement.where(models.BackgroundTask.user_id == user_id)
        if self.db.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        task = self.db.scalars(statement).first()
        if task is None:
            return None
        claimed = self.db.execute(
            update(models.BackgroundTask)
            .where(
                models.BackgroundTask.id == task.id,
                models.BackgroundTask.status == "queued",
                models.BackgroundTask.attempts < models.BackgroundTask.max_attempts,
            )
            .values(
                status="running",
                attempts=models.BackgroundTask.attempts + 1,
                started_at=datetime.utcnow(),
                error=None,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            return None
        self.db.commit()
        self.db.refresh(task)
        background_tasks_total.inc(task_type=task.task_type, status="running")
        self.update_queue_depth_metrics()
        return task

    def mark_success(
        self, task: models.BackgroundTask, result: dict[str, Any], elapsed_seconds: float
    ) -> None:
        task.status = "completed"
        task.result_json = result
        task.completed_at = datetime.utcnow()
        task.error = None
        self.db.commit()
        background_tasks_total.inc(task_type=task.task_type, status="completed")
        background_task_latency_seconds.observe(elapsed_seconds, task_type=task.task_type)
        self.update_queue_depth_metrics()

    def mark_failure(self, task: models.BackgroundTask, error: str, elapsed_seconds: float) -> None:
        task.status = (
            "outcome_unknown"
            if task.task_type == "plan.generate"
            else "failed"
            if task.task_type.startswith("responsibility.") or task.attempts >= task.max_attempts
            else "queued"
        )
        task.error = error[:4000]
        task.completed_at = (
            datetime.utcnow() if task.status in {"failed", "outcome_unknown"} else None
        )
        self.db.commit()
        background_tasks_total.inc(task_type=task.task_type, status=task.status)
        background_task_latency_seconds.observe(elapsed_seconds, task_type=task.task_type)
        self.update_queue_depth_metrics()

    def update_queue_depth_metrics(self) -> None:
        rows = self.db.execute(
            select(models.BackgroundTask.status, func.count(models.BackgroundTask.id)).group_by(
                models.BackgroundTask.status
            )
        ).all()
        seen = set()
        for status, count in rows:
            seen.add(status)
            background_task_queue_depth.set(count, status=status)
        for status in {"queued", "running", "completed", "failed"} - seen:
            background_task_queue_depth.set(0, status=status)


def run_one_background_task(
    db: Session, *, user_id: uuid.UUID | None = None
) -> models.BackgroundTask | None:
    """Claim and execute one queued task. Returns the task if work was found."""
    queue = BackgroundTaskQueue(db)
    task = queue.claim_next(user_id=user_id)
    if task is None:
        return None

    # claim_next already commits queue ownership. Persist diagnostics before any
    # handler or execution lock, never inside the business write transaction.
    identity = uuid.uuid4()
    journal = DurableStreamJournal(identity, task.user_id, None)
    run = models.AgentRun(
        id=identity,
        user_id=task.user_id,
        run_type="background.task",
        status="running",
        nodes=[
            {"type": "DurableStreamJournal", "journal_id": str(identity)},
            {"type": "BackgroundTaskReference", "task_id": str(task.id), "attempt": task.attempts},
        ],
    )
    try:
        db.add(run)
        task.payload_json = {**(task.payload_json or {}), "execution_trace_run_id": str(identity)}
        db.commit()
        journal.append(
            {"type": "background.start", "task_id": str(task.id), "attempt": task.attempts}
        )
        with model_recording(journal), model_origin({"step_id": str(identity)}):
            result = _run_claimed_task(db, queue, task)
        state = result.status if result is not None else "unconfirmed"
        # The handler's commit has already finished; this is diagnostic metadata.
        run.status = state
        run.completed_at = datetime.utcnow()
        db.commit()
        journal.append(
            {
                "type": "journal.end",
                "state": state,
                "task_id": str(task.id),
                "may_repeat_writes": False,
                "business_commit_verification": "not_independently_checked",
            }
        )
        return result
    except BaseException as exc:
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.warning("Background rollback unavailable: %s", type(rollback_error).__name__)
        try:
            saved_run = db.get(models.AgentRun, identity)
            if saved_run is not None:
                saved_run.status = "interrupted"
                saved_run.error = type(exc).__name__
                saved_run.completed_at = datetime.utcnow()
                db.commit()
            journal.append(
                {
                    "type": "journal.end",
                    "state": "interrupted",
                    "error_type": type(exc).__name__,
                    "may_repeat_writes": False,
                    "business_commit_verification": "unconfirmed",
                }
            )
        except Exception as recording_error:
            logger.warning(
                "Background trace terminal unavailable: %s", type(recording_error).__name__
            )
        raise
    finally:
        journal.close()


def _run_claimed_task(db, queue, task):
    start = time.perf_counter()
    try:
        from fast_api.app.services.plan_writes import lock_plan_owner

        # Claim commits separately. Recheck after taking the execution gate so a
        # user may reconcile a claimed-but-not-started job without a late write.
        if task.task_type in {
            "plan.generate",
            "responsibility.plan_adjustment",
            "responsibility.weekly_review",
        }:
            lock_plan_owner(db, task.user_id)
        current = db.scalar(
            select(models.BackgroundTask)
            .where(models.BackgroundTask.id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None or current.status != "running":
            db.rollback()
            return current
        task = current
        result = _execute_task(db, task)
    except Exception as exc:
        db.rollback()
        if task.task_type == "responsibility.plan_adjustment":
            # This handler has no external effects and shares the DB transaction.
            # Rolled-back writes are known not to have committed; never requeue them.
            approval_id = (task.payload_json or {}).get("approval_id")
            if approval_id:
                approval = db.get(models.PendingApproval, uuid.UUID(approval_id))
                if approval is not None and approval.status == "approved":
                    approval.status = "failed"
                    from fast_api.app.services.execution_events import append_approval_event

                    append_approval_event(
                        approval,
                        "execution.rollback",
                        "failed",
                        "执行失败，本地数据库写入已回滚，不自动重试。",
                        details={"error": str(exc)},
                    )
        queue.mark_failure(task, str(exc), time.perf_counter() - start)
    else:
        queue.mark_success(task, result, time.perf_counter() - start)
    return task


def run_due_decision_evaluations(db: Session) -> dict[str, Any]:
    from fast_api.app.services.decision_evaluation import DecisionEvaluationService

    return _run_periodic_scan(
        db, "decision.evaluation.scan", DecisionEvaluationService(db).scan_due, "processed"
    )


def run_due_responsibilities(db: Session) -> dict[str, Any]:
    from fast_api.app.services.responsibilities import ResponsibilityService

    return _run_periodic_scan(db, "responsibility.scan", ResponsibilityService(db).scan_due, None)


def _run_periodic_scan(db, operation, scan, commit_if):
    """System-only aggregate journal: never expose cross-user scan data via APIs."""
    identity = uuid.uuid4()
    journal = DurableStreamJournal(identity, "background-worker", None)
    logger.debug("Periodic scan trace operation=%s run=%s", operation, identity)
    try:
        journal.append({"type": "scan.start", "operation": operation})
        result = scan()
        counts = {
            key: value
            for key, value in result.items()
            if key in {"processed", "queued", "expired"}
            and isinstance(value, int)
            and not isinstance(value, bool)
        }
        should_commit = commit_if is None or bool(result.get(commit_if))
        journal.append(
            {"type": "scan.observed", "counts": counts, "commit_requested": should_commit}
        )
        if should_commit:
            db.commit()
            journal.append({"type": "scan.commit_returned", "independent_verification": False})
        journal.append({"type": "journal.end", "state": "completed", "may_repeat_writes": False})
        return result
    except BaseException as exc:
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.warning("Scan rollback unavailable: %s", type(rollback_error).__name__)
        try:
            journal.append(
                {
                    "type": "journal.end",
                    "state": "interrupted",
                    "error_type": type(exc).__name__,
                    "business_commit_verification": "unconfirmed",
                    "may_repeat_writes": False,
                }
            )
        except Exception as recording_error:
            logger.warning("Scan terminal unavailable: %s", type(recording_error).__name__)
        logger.warning("Periodic scan interrupted operation=%s run=%s", operation, identity)
        raise
    finally:
        journal.close()


def _execute_task(db: Session, task: models.BackgroundTask) -> dict[str, Any]:
    if task.task_type == "responsibility.plan_adjustment":
        from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService

        return ApprovedPlanAdjustmentService(db).execute(task)
    if task.task_type == "responsibility.weekly_review":
        from fast_api.app.services.responsibilities import ResponsibilityService

        return ResponsibilityService(db).execute_review(task)
    if task.task_type == "plan.generate":
        service = CoachAgentService(db)
        payload = task.payload_json or {}
        plan = service.generate_plan(
            PlanGenerateRequest.model_validate(
                {
                    **{
                        key: value
                        for key, value in payload.items()
                        if key != "execution_trace_run_id"
                    },
                    "user_id": task.user_id,
                }
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
    if task.task_type == "decision.evaluation.scan":
        from fast_api.app.services.decision_evaluation import DecisionEvaluationService

        return DecisionEvaluationService(db).scan_due(task.user_id)
    raise ValueError(f"Unsupported background task type: {task.task_type}")
