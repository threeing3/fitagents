"""Verify persisted business receipts with a separate read connection, never retry."""

import uuid

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fast_api.app.db import models


def approved_plan_write_receipt(db, owner_id, approval_id, *, session_id=None):
    """Confirm one consumed approval, completed job, run and matching plan."""
    unknown = {"kind": "plan_adjustment", "state": "unconfirmed", "may_repeat_writes": False}
    engine = db.get_bind()
    if not isinstance(engine, Engine) or (
        engine.dialect.name == "sqlite" and engine.url.database in {None, "", ":memory:"}
    ):
        return {**unknown, "reason": "independent_read_unavailable"}
    try:
        identity = uuid.UUID(str(approval_id))
    except (ValueError, TypeError):
        return {**unknown, "reason": "invalid_reference"}
    try:
        with Session(engine, autoflush=False) as reader:
            approval = reader.scalar(
                select(models.PendingApproval).where(
                    models.PendingApproval.id == identity,
                    models.PendingApproval.user_id == owner_id,
                    models.PendingApproval.tool_name == "plan.reduce_sets",
                )
            )
            if approval is None:
                return {**unknown, "reason": "approval_unavailable"}
            if (
                session_id is not None
                and approval.session_id is not None
                and str(approval.session_id) != str(session_id)
            ):
                return {**unknown, "reason": "approval_session_mismatch"}
            if approval.status != "executed":
                return {
                    **unknown,
                    "reason": "approval_not_executed"
                    if approval.status in {"pending", "approved", "denied", "expired", "stale"}
                    else "execution_not_confirmed",
                    "approval_status": approval.status,
                }
            job = reader.scalar(
                select(models.BackgroundTask).where(
                    models.BackgroundTask.id == approval.job_id,
                    models.BackgroundTask.user_id == owner_id,
                    models.BackgroundTask.task_type == "responsibility.plan_adjustment",
                    models.BackgroundTask.status == "completed",
                )
            )
            if (
                job is None
                or job.completed_at is None
                or str((job.payload_json or {}).get("approval_id")) != str(identity)
            ):
                return {**unknown, "reason": "completed_job_unavailable"}
            result = job.result_json or {}
            if result.get("status") != "adjusted" or result.get("verified") is not True:
                return {**unknown, "reason": "job_result_unconfirmed"}
            try:
                run_id = uuid.UUID(str(result.get("agent_run_id")))
                plan_id = uuid.UUID(str((approval.input_json or {}).get("plan_id")))
                task_id = uuid.UUID(str((approval.context_json or {}).get("responsibility_id")))
            except (ValueError, TypeError):
                return {**unknown, "reason": "invalid_execution_reference"}
            run = reader.scalar(
                select(models.AgentRun).where(
                    models.AgentRun.id == run_id,
                    models.AgentRun.user_id == owner_id,
                    models.AgentRun.run_type == "responsibility.plan_adjustment",
                    models.AgentRun.status == "completed",
                )
            )
            event = reader.scalar(
                select(models.AgentTaskEvent)
                .join(
                    models.AgentTaskState, models.AgentTaskState.id == models.AgentTaskEvent.task_id
                )
                .where(
                    models.AgentTaskEvent.agent_run_id == run_id,
                    models.AgentTaskEvent.user_id == owner_id,
                    models.AgentTaskEvent.event_type == "responsibility.plan_adjusted",
                    models.AgentTaskEvent.task_id == task_id,
                    models.AgentTaskState.user_id == owner_id,
                )
            )
            if (
                run is None
                or run.completed_at is None
                or event is None
                or str((event.payload_json or {}).get("approval_id")) != str(identity)
                or str((event.payload_json or {}).get("job_id")) != str(job.id)
            ):
                return {**unknown, "reason": "execution_link_unavailable"}
            if str(result.get("plan_id")) != str(plan_id) or result.get("changed_date") != (
                approval.input_json or {}
            ).get("day_date"):
                return {**unknown, "reason": "execution_target_mismatch"}
            plan = reader.scalar(
                select(models.TrainingPlan).where(
                    models.TrainingPlan.id == plan_id,
                    models.TrainingPlan.user_id == owner_id,
                )
            )
            candidate = (approval.context_json or {}).get("candidate_plan")
            if plan is None or not isinstance(candidate, dict) or plan.plan_json != candidate:
                return {**unknown, "reason": "current_plan_not_candidate"}
            return {
                "kind": "plan_adjustment",
                "state": "committed",
                "approval_id": str(identity),
                "job_id": str(job.id),
                "record_id": str(plan.id),
                "agent_run_id": str(run.id),
                "completed_at": job.completed_at.isoformat(),
                "verification": "independent_committed_read",
                "may_repeat_writes": False,
            }
    except SQLAlchemyError:
        return {**unknown, "reason": "verification_unavailable"}


def chat_workout_receipt(db, owner_id, request_id, session_id):
    """Resolve only the committed chat-to-message link, never journal assertions."""
    unknown = {"kind": "workout_log", "state": "unconfirmed", "may_repeat_writes": False}
    engine = db.get_bind()
    if not isinstance(engine, Engine) or (
        engine.dialect.name == "sqlite" and engine.url.database in {None, "", ":memory:"}
    ):
        return {**unknown, "reason": "independent_read_unavailable"}
    try:
        with Session(engine, autoflush=False) as reader:
            request = reader.scalar(
                select(models.IdempotencyRecord).where(
                    models.IdempotencyRecord.id == request_id,
                    models.IdempotencyRecord.user_id == owner_id,
                    models.IdempotencyRecord.operation == "chat",
                )
            )
            if request is None or (request.request_json or {}).get("session_id") != str(session_id):
                return {**unknown, "reason": "request_scope_unavailable"}
            message_id = (request.response_json or {}).get("execution", {}).get("user_message_id")
            try:
                message_id = uuid.UUID(str(message_id))
            except (ValueError, TypeError):
                return {**unknown, "reason": "source_message_unavailable"}
            write = reader.scalar(
                select(models.IdempotencyRecord).where(
                    models.IdempotencyRecord.user_id == owner_id,
                    models.IdempotencyRecord.operation == "workout_log",
                    models.IdempotencyRecord.idempotency_key == "chat-workout:" + str(message_id),
                    models.IdempotencyRecord.status == "completed",
                )
            )
            target = (write.response_json or {}).get("workout_log_id") if write else None
        return workout_write_receipt(db, owner_id, message_id, target, session_id=session_id)
    except SQLAlchemyError:
        return {**unknown, "reason": "verification_unavailable"}


def workout_write_receipt(db, owner_id, request_key, workout_id, *, session_id=None):
    try:
        return _verify_workout_receipt(db, owner_id, request_key, workout_id, session_id)
    except SQLAlchemyError:
        # Verification is best effort, not a second business operation. Never undo
        # or misreport an already committed write because the reader is unavailable.
        return {
            "kind": "workout_log",
            "state": "unconfirmed",
            "reason": "verification_unavailable",
            "may_repeat_writes": False,
        }


def _verify_workout_receipt(db, owner_id, request_key, workout_id, session_id):
    result = {"kind": "workout_log", "state": "unconfirmed", "may_repeat_writes": False}
    try:
        target = uuid.UUID(str(workout_id))
        message_id = uuid.UUID(str(request_key))
    except (ValueError, TypeError):
        return {**result, "reason": "invalid_reference"}
    engine = db.get_bind()
    if not isinstance(engine, Engine) or (
        engine.dialect.name == "sqlite" and engine.url.database in {None, "", ":memory:"}
    ):
        return {**result, "reason": "independent_read_unavailable"}
    # Never flush or commit the caller's session. A fresh connection observes committed state.
    with Session(engine, autoflush=False) as reader:
        message = reader.scalar(
            select(models.ChatMessage)
            .join(
                models.ConversationSession,
                models.ConversationSession.id == models.ChatMessage.session_id,
            )
            .where(
                models.ChatMessage.id == message_id,
                models.ChatMessage.user_id == owner_id,
                models.ChatMessage.role == "user",
                models.ConversationSession.user_id == owner_id,
            )
        )
        if message is None:
            return {**result, "reason": "source_message_unavailable"}
        if session_id is not None and str(message.session_id) != str(session_id):
            return {**result, "reason": "source_session_mismatch"}
        receipt = reader.scalar(
            select(models.IdempotencyRecord).where(
                models.IdempotencyRecord.user_id == owner_id,
                models.IdempotencyRecord.operation == "workout_log",
                models.IdempotencyRecord.idempotency_key == "chat-workout:" + str(message_id),
                models.IdempotencyRecord.status == "completed",
            )
        )
        if receipt is None or receipt.completed_at is None:
            return {**result, "reason": "completed_receipt_unavailable"}
        if str((receipt.response_json or {}).get("workout_log_id")) != str(target):
            return {**result, "reason": "receipt_target_mismatch"}
        record = reader.scalar(
            select(models.WorkoutLog).where(
                models.WorkoutLog.id == target,
                models.WorkoutLog.user_id == owner_id,
            )
        )
        if record is None:
            return {**result, "reason": "business_record_unavailable"}
        return {
            "kind": "workout_log",
            "state": "committed",
            "record_id": str(record.id),
            "workout_name": record.workout_name,
            "duration_minutes": record.duration_minutes,
            "receipt_id": str(receipt.id),
            "source_message_id": str(message.id),
            "session_id": str(message.session_id),
            "completed_at": receipt.completed_at.isoformat(),
            "verification": "independent_committed_read",
            "may_repeat_writes": False,
        }
