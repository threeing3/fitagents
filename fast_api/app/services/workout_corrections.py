"""Owned canonical fact correction with durable audit and no inferred source links."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from fast_api.app.core.errors import IdempotencyConflictError, ResourceNotFoundError
from fast_api.app.db import models
from fast_api.app.schemas.agent import WorkoutCorrectionRequest
from fast_api.app.services.decision_dependencies import DecisionDependencyService
from fast_api.app.services.memory_dependencies import invalidate_derived_memories
from fast_api.app.services.plan_writes import lock_plan_owner


def list_workouts(db, user_id: uuid.UUID, limit: int = 30, log_id: uuid.UUID | None = None):
    query = select(models.WorkoutLog).where(models.WorkoutLog.user_id == user_id)
    if log_id is not None:
        query = query.where(models.WorkoutLog.id == log_id)
    logs = db.scalars(
        query.order_by(models.WorkoutLog.performed_at.desc(), models.WorkoutLog.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    ).all()
    sources = db.scalars(
        select(models.LongTermMemory).where(
            models.LongTermMemory.user_id == user_id,
            models.LongTermMemory.source == "workout_log",
            models.LongTermMemory.memory_metadata["canonical_workout_source"]
            .as_boolean()
            .is_(True),
            models.LongTermMemory.memory_metadata["workout_log_id"]
            .as_string()
            .in_([str(log.id) for log in logs]),
        )
    ).all()
    by_log = {}
    for source in sources:
        by_log.setdefault(source.memory_metadata["workout_log_id"], []).append(source)
    result = []
    for log in logs:
        linked = by_log.get(str(log.id), [])
        revision = (
            linked[0].memory_metadata.get("current", {}).get("revision")
            if len(linked) == 1
            else None
        )
        result.append(
            {
                "id": str(log.id),
                "performed_at": log.performed_at.isoformat(),
                "workout_name": log.workout_name,
                "duration_minutes": log.duration_minutes,
                "rpe": log.rpe,
                "completion_rate": log.completion_rate,
                "revision": revision,
                "correction_available": isinstance(revision, int),
            }
        )
    return result


def correct_workout(
    service, user_id: uuid.UUID, log_id: uuid.UUID, request: WorkoutCorrectionRequest
):
    db = service.db
    try:
        lock_plan_owner(db, user_id)
        log = db.scalar(
            select(models.WorkoutLog)
            .where(models.WorkoutLog.id == log_id, models.WorkoutLog.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if log is None:
            raise ResourceNotFoundError("Workout record not found")
        payload = {
            "workout_log_id": str(log_id),
            **request.model_dump(mode="json", exclude_unset=True),
        }
        audit, replay = service._begin_idempotent_operation(
            user_id,
            operation="workout_correction",
            idempotency_key=request.idempotency_key,
            request_json=payload,
        )
        if replay is not None:
            return replay
        sources = db.scalars(
            select(models.LongTermMemory)
            .where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.source == "workout_log",
                models.LongTermMemory.memory_metadata["workout_log_id"].as_string() == str(log_id),
                models.LongTermMemory.memory_metadata["canonical_workout_source"]
                .as_boolean()
                .is_(True),
            )
            .execution_options(populate_existing=True)
        ).all()
        if len(sources) != 1:
            raise IdempotencyConflictError("Historical source association is not verified")
        source = sources[0]
        links = source.memory_metadata["current"]
        if links.get("revision") != request.expected_revision:
            raise IdempotencyConflictError("Workout revision changed; reload before correcting")
        session = db.get(
            models.WorkoutSession, uuid.UUID(links["session_id"]), populate_existing=True
        )
        memory = db.get(
            models.LongTermMemory, uuid.UUID(links["memory_id"]), populate_existing=True
        )
        if (
            session is None
            or memory is None
            or session.user_id != user_id
            or memory.user_id != user_id
        ):
            raise IdempotencyConflictError("Canonical source association is unavailable")
        before = request.expected.model_dump(exclude_unset=True)
        after = request.changes.model_dump(exclude_unset=True)
        if any(getattr(log, field) != value for field, value in before.items()):
            raise IdempotencyConflictError("Workout facts changed; reload before correcting")
        if before == after:
            raise IdempotencyConflictError("Correction contains no changed fact")
        # Do not overwrite an already inconsistent canonical view.
        if session.fatigue_score != log.rpe or session.completion_score != log.completion_rate:
            raise IdempotencyConflictError(
                "Canonical workout facts disagree; manual review required"
            )
        for field, value in after.items():
            setattr(log, field, value)
        session.fatigue_score = log.rpe
        session.completion_score = log.completion_rate
        memory.status = "superseded"
        memory.valid_until = datetime.now(timezone.utc)
        memory.memory_metadata = {
            **(memory.memory_metadata or {}),
            "correction_audit_id": str(audit.id),
        }
        old_ids = [str(log.id), str(session.id), str(memory.id)]
        revoked = invalidate_derived_memories(
            db,
            user_id,
            [
                {"table": "workout_logs", "id": str(log.id)},
                {"table": "workout_sessions", "id": str(session.id)},
                {"table": "long_term_memories", "id": str(memory.id)},
            ],
            "workout_fact_corrected",
        )
        invalidated = DecisionDependencyService(db).invalidate_changed(user_id, old_ids + revoked)
        new_memory_id = service._write_memory(
            user_id,
            "training_performance",
            f"Recorded {log.workout_name}; duration={log.duration_minutes}; "
            f"RPE={log.rpe}; completion={log.completion_rate}; notes={log.notes or ''}",
            "workout_log",
            0.65,
            memory_metadata={
                "workout_log_id": str(log.id),
                "revision": request.expected_revision + 1,
                "correction_audit_id": str(audit.id),
            },
        )
        source.memory_metadata = {
            **source.memory_metadata,
            "current": {
                **links,
                "memory_id": str(new_memory_id),
                "revision": request.expected_revision + 1,
            },
        }
        result = {
            "status": "corrected",
            "workout_log_id": str(log_id),
            "revision": request.expected_revision + 1,
            "audit_id": str(audit.id),
            "before": before,
            "after": after,
            "invalidated_decision_ids": invalidated,
            "idempotent_replay": False,
        }
        audit.response_json = result
        audit.status = "completed"
        audit.completed_at = datetime.now(timezone.utc)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
