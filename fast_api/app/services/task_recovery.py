"""Explicit owner-scoped reconciliation, never a retry or inferred cancellation."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fast_api.app.db import models


class TaskRecoveryBusyError(ValueError):
    pass


class TaskRecoveryService:
    def __init__(self, db: Session):
        self.db = db

    def reconcile(self, task_id: uuid.UUID, user_id: uuid.UUID, expected_attempt: int) -> dict:
        # No lock or existence leak for another user's task.
        task = self.db.scalar(
            select(models.BackgroundTask).where(
                models.BackgroundTask.id == task_id, models.BackgroundTask.user_id == user_id
            )
        )
        if task is None:
            raise ValueError("Task not found")
        try:
            owner = self.db.scalar(
                select(models.User)
                .where(models.User.id == user_id)
                .with_for_update(key_share=True, nowait=True)
            )
            if owner is None:
                raise ValueError("Task not found")
            task = self.db.scalar(
                select(models.BackgroundTask)
                .where(
                    models.BackgroundTask.id == task_id, models.BackgroundTask.user_id == user_id
                )
                .with_for_update(nowait=True)
                .execution_options(populate_existing=True)
            )
        except OperationalError as exc:
            raise TaskRecoveryBusyError(
                "Execution still holds a lock; refresh rather than interrupt or retry"
            ) from exc
        if task.attempts != expected_attempt:
            raise ValueError("Task attempt changed; refresh before reconciliation")
        if task.status != "running":
            return {"status": task.status, "changed": False, "requeued": False}
        known_uncommitted = False
        approval = None
        if task.task_type == "responsibility.plan_adjustment":
            raw = (task.payload_json or {}).get("approval_id")
            try:
                approval = self.db.get(models.PendingApproval, uuid.UUID(str(raw)))
            except (ValueError, TypeError):
                approval = None
            if approval is not None and approval.user_id == user_id and approval.job_id == task.id:
                context = approval.context_json or {}
                try:
                    plan = self.db.get(
                        models.TrainingPlan,
                        uuid.UUID(str((approval.input_json or {}).get("plan_id"))),
                    )
                except (ValueError, TypeError):
                    plan = None
                # This handler commits its plan, consumed approval, run and task
                # together. An unconsumed approval plus unchanged baseline proves
                # that handler has not committed; a missing/different plan does not.
                known_uncommitted = (
                    approval.status == "approved"
                    and plan is not None
                    and plan.user_id == user_id
                    and plan.plan_json == context.get("baseline_plan")
                )
            else:
                approval = None
        outcome = "failed" if known_uncommitted else "outcome_unknown"
        task.status = outcome
        task.completed_at = datetime.utcnow()
        task.error = (
            "Interrupted execution reconciled; no committed adjustment"
            if known_uncommitted
            else "Interrupted execution has uncertain effects; inspect records, never automatically retry"
        )
        task.result_json = {
            **(task.result_json or {}),
            "recovery": {
                "status": outcome,
                "recorded_by": "authenticated_user",
                "expected_attempt": expected_attempt,
                "no_reexecution": True,
            },
        }
        if approval is not None and approval.status in {"approved", "executing"}:
            from fast_api.app.services.execution_events import append_approval_event

            approval.status = outcome
            append_approval_event(
                approval,
                "execution.reconcile",
                outcome,
                "已核对中断任务：原子调整尚未提交；关闭本次执行，不自动重跑。"
                if known_uncommitted
                else "中断任务的副作用未能确认，保留未知状态；不重新执行工具。",
                details={"no_reexecution": True},
            )
        self.db.flush()
        return {
            "status": outcome,
            "changed": True,
            "requeued": False,
            "known_uncommitted": known_uncommitted,
        }
