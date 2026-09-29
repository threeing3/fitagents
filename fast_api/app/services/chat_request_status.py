"""Read-only recovery evidence; never restart or infer completion from elapsed time."""

import json
import uuid

from sqlalchemy import select

from fast_api.app.db import models


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def get_chat_request_status(db, user_id, session_id, key: str) -> dict:
    session = db.get(models.ConversationSession, session_id)
    if session is None or session.user_id != user_id:
        raise ValueError("Conversation session not found for current user")
    record = db.scalar(
        select(models.IdempotencyRecord).where(
            models.IdempotencyRecord.user_id == user_id,
            models.IdempotencyRecord.operation == "chat",
            models.IdempotencyRecord.idempotency_key == key.strip(),
        )
    )
    response = {"status": "not_found", "confirmed_writes": [], "may_repeat_writes": False}
    if record is None or record.request_json.get("session_id") != str(session_id):
        return response
    response["status"] = "unconfirmed"
    payload = record.response_json or {}
    execution = payload.get("execution", {})
    message_id = _uuid(execution.get("user_message_id"))
    message = db.get(models.ChatMessage, message_id) if message_id else None
    if (
        message is not None
        and message.user_id == user_id
        and message.session_id == session_id
        and message.role == "user"
    ):
        write = db.scalar(
            select(models.IdempotencyRecord).where(
                models.IdempotencyRecord.user_id == user_id,
                models.IdempotencyRecord.operation == "workout_log",
                models.IdempotencyRecord.idempotency_key == "chat-workout:" + str(message.id),
                models.IdempotencyRecord.status == "completed",
            )
        )
        log_id = _uuid((write.response_json or {}).get("workout_log_id")) if write else None
        log = db.get(models.WorkoutLog, log_id) if log_id else None
        if log is not None and log.user_id == user_id:
            response["confirmed_writes"].append(
                {
                    "kind": "workout_log",
                    "record_id": str(log.id),
                    "workout_name": log.workout_name,
                    "duration_minutes": log.duration_minutes,
                }
            )

    if record.status != "completed":
        return response
    result = payload.get("result")
    if result is None and isinstance(payload.get("events"), list):
        try:
            events = [json.loads(raw) for raw in payload["events"]]
        except (ValueError, TypeError):
            return response
        done = next((item for item in reversed(events) if item.get("type") == "done"), None)
        if done:
            result = {
                "assistant_message": "".join(
                    str(item.get("text", ""))
                    for item in events
                    if item.get("type") == "answer_delta"
                ).strip(),
                "agent_run_id": done.get("run_id"),
            }
    if not isinstance(result, dict):
        return response
    run_id = _uuid(result.get("agent_run_id"))
    run = db.get(models.AgentRun, run_id) if run_id else None
    if run is None or run.user_id != user_id or run.session_id != session_id:
        return response
    failed = run.status == "failed" or any(
        node.get("node") == "RuntimeError" for node in (run.nodes or [])
    )
    response.update(
        status="failed" if failed else "completed",
        assistant_message=result.get("assistant_message", ""),
        agent_run_id=str(run.id),
    )
    return response
