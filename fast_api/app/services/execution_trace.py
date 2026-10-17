"""Read-only, owner-scoped projection of recorded execution sources."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.agent_trace_analysis import PHASE_BY_NODE
from fast_api.app.services.execution_events import public_run_events

PRIVATE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "token",
    "credentials",
    "cookie",
    "cookies",
    "system_prompt",
    "prompt",
    "messages",
    "raw_context",
    "raw_output",
    "reasoning_content",
    "chain_of_thought",
    "log_path",
    "_continuation",
}


def trace_detail(value: Any, depth: int = 0) -> Any:
    """Bound diagnostic data, exclude credentials and private model reasoning."""
    if depth > 8:
        return "[depth truncated]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            lowered = str(key).lower()
            if lowered in PRIVATE_KEYS or any(
                secret in lowered for secret in ("api_key", "password", "secret", "authorization")
            ):
                result[str(key)] = "[excluded]"
            else:
                result[str(key)] = trace_detail(item, depth + 1)
        if len(value) > 100:
            result["_truncated_keys"] = len(value) - 100
        return result
    if isinstance(value, list):
        result = [trace_detail(item, depth + 1) for item in value[:100]]
        if len(value) > 100:
            result.append({"_truncated_items": len(value) - 100})
        return result
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + " [text truncated]"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def recorded_execution_trace(db: Session, run_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    run = db.scalar(
        select(models.AgentRun).where(
            models.AgentRun.id == run_id, models.AgentRun.user_id == user_id
        )
    )
    if run is None:
        return interrupted_request_trace(db, run_id, user_id)
    root = str(run.id)
    events = []
    seen = set()
    parents = set()

    def add(item, source, index):
        identity = str(item.get("event_id") or f"{root}:{source}:{index}")
        if identity in seen:
            return
        seen.add(identity)
        output = item.get("output") or item.get("details") or {}
        if not isinstance(output, dict):
            output = {"value": output}
        parent = output.get("parent_id")
        if parent:
            parents.add(str(parent))
        name = str(item.get("node") or item.get("name") or item.get("type") or "event")
        status = output.get("status") or item.get("status") or "unknown"
        if item.get("error"):
            status = "failed"
        events.append(
            {
                "event_id": identity,
                "run_id": root,
                "parent_id": str(parent) if parent else root,
                "child_id": output.get("child_id"),
                "step_id": output.get("step_id"),
                "name": name,
                "phase": PHASE_BY_NODE.get(name, "execute" if source == "tool" else "observe"),
                "status": str(status),
                "source": source,
                "recorded_at": str(item.get("timestamp_utc") or item.get("recorded_at") or ""),
                "latency_ms": item.get("latency_ms", 0),
                "recorded_sequence": item.get("sequence"),
                "stream_sequence": item.get("stream_sequence"),
                "summary": str(item.get("summary") or output.get("summary") or name)[:400],
                "input": trace_detail(item.get("input_summary") or {}),
                "output": trace_detail(output),
                "error": trace_detail(item.get("error")),
            }
        )

    for index, node in enumerate(run.nodes or []):
        add(node, node.get("trace_source") or "node", index)
        if node.get("type") == "DurableStreamJournal":
            from fast_api.app.services.durable_stream_journal import read_stream_journal

            journal = read_stream_journal(node["journal_id"], user_id, run.session_id)
            if journal:
                for index, entry in enumerate(journal["entries"]):
                    add(stream_trace_node(entry), "stream", index)
    replay = db.scalar(
        select(models.AgentRunReplay).where(
            models.AgentRunReplay.agent_run_id == run.id,
            models.AgentRunReplay.user_id == user_id,
        )
    )
    if replay:
        updates = (replay.state_snapshot or {}).get("state_updates") or {}
        for index, entry in enumerate(updates.get("execution_events") or []):
            add(entry, "journal", index)
    for index, entry in enumerate(public_run_events(db, run, user_id)):
        add(entry, "journal", index)
    approval_ids = {
        entry["output"].get("approval_id")
        for entry in events
        if isinstance(entry["output"], dict) and entry["output"].get("approval_id")
    }
    write_receipts = []
    if run.run_type == "background.task":
        from fast_api.app.services.background_trace_reference import background_trace_reference
        from fast_api.app.services.write_receipts import approved_plan_write_receipt

        for node in run.nodes or []:
            if node.get("type") != "BackgroundTaskReference":
                continue
            try:
                task_id = uuid.UUID(str(node.get("task_id")))
            except (ValueError, TypeError):
                continue
            job = db.get(models.BackgroundTask, task_id)
            if (
                job is None
                or job.user_id != user_id
                or background_trace_reference(db, job) != str(run.id)
                or job.task_type != "responsibility.plan_adjustment"
            ):
                continue
            try:
                approval_id = uuid.UUID(str((job.payload_json or {}).get("approval_id")))
            except (ValueError, TypeError):
                continue
            approval = db.get(models.PendingApproval, approval_id)
            if (
                approval is not None
                and approval.user_id == user_id
                and approval.job_id == job.id
                and approval.tool_name == "plan.reduce_sets"
            ):
                write_receipts.append(approved_plan_write_receipt(db, user_id, approval.id))
    for approval_id in sorted(approval_ids):
        try:
            parsed_id = uuid.UUID(str(approval_id))
        except ValueError:
            continue
        approval = db.scalar(
            select(models.PendingApproval).where(
                models.PendingApproval.id == parsed_id,
                models.PendingApproval.user_id == user_id,
            )
        )
        if approval is not None:
            if approval.session_id is not None and approval.session_id != run.session_id:
                continue
            if approval.tool_name == "plan.reduce_sets":
                from fast_api.app.services.write_receipts import approved_plan_write_receipt

                write_receipts.append(
                    approved_plan_write_receipt(db, user_id, approval.id, session_id=run.session_id)
                )
            for index, entry in enumerate(
                (approval.context_json or {}).get("execution_events") or []
            ):
                add(entry, f"approval:{approval_id}", index)
    calls = db.scalars(
        select(models.ToolCall)
        .where(models.ToolCall.agent_run_id == run.id)
        .order_by(models.ToolCall.created_at, models.ToolCall.id)
    ).all()
    for call in calls:
        receipt = None
        if call.tool_name == "training.log.write":
            from fast_api.app.services.write_receipts import workout_write_receipt

            receipt = workout_write_receipt(
                db,
                user_id,
                (call.input_json or {}).get("request_key"),
                (call.output_json or {}).get("workout_log_id"),
                session_id=run.session_id,
            )
        add(
            {
                "event_id": str(call.id),
                "name": call.tool_name,
                "status": call.status,
                "recorded_at": call.created_at.isoformat(),
                "input_summary": call.input_json,
                "output": {
                    **(call.output_json or {}),
                    **({"write_receipt": receipt} if receipt else {}),
                },
                "latency_ms": call.latency_ms,
            },
            "tool",
            0,
        )
    # Join only explicitly referenced trees: never guess by same session or time.
    for parent in sorted(parents):
        try:
            catalog_id = uuid.UUID(parent)
        except ValueError:
            continue
        catalog = db.scalar(
            select(models.AgentRun).where(
                models.AgentRun.id == catalog_id,
                models.AgentRun.user_id == user_id,
                models.AgentRun.session_id == run.session_id,
                models.AgentRun.run_type == "subagent_catalog",
            )
        )
        if catalog:
            for index, node in enumerate(catalog.nodes or []):
                # Catalog is a current-state checkpoint, not fabricated lifecycle history.
                add(
                    {
                        "event_id": f"{catalog.id}:checkpoint:{index}",
                        "name": "SubagentCheckpoint",
                        "status": catalog.status,
                        "output": node,
                        "recorded_at": catalog.updated_at.isoformat(),
                    },
                    "checkpoint",
                    index,
                )
    # Stable display order is NOT a claim of a globally recorded execution sequence.
    events.sort(key=lambda entry: entry["recorded_at"] or run.started_at.isoformat())
    for order, entry in enumerate(events, 1):
        entry["order"] = order
    return {
        "schema_version": "execution-trace/v1",
        "run_id": root,
        "status": run.status,
        "write_receipts": write_receipts,
        "events": events,
        "snapshot": trace_detail(
            {
                "state": replay.state_snapshot if replay else {},
                "tool_plan": replay.tool_plan_json if replay else {},
                "config": replay.config_snapshot if replay else {},
            }
        ),
        "coverage": {
            "saved_nodes": len(run.nodes or []),
            "saved_tool_calls": len(calls),
            "snapshot_available": replay is not None,
            "exact_model_requests": False,
            "ordering": "recorded_time_projection_not_global_sequence",
            "limitations": [
                "Only recorded sources can be inspected; missing events are not reconstructed.",
                "Checkpoints describe current child state, not every past transition.",
                "Requests and context may be truncated; this is not exact model replay.",
                "Reading this trace never retries a tool or executes a write.",
            ],
        },
    }


def stream_trace_node(entry):
    return {
        "event_id": entry.get("event_id"),
        "node": entry.get("name") or entry.get("type"),
        "status": entry.get("status")
        or (
            entry.get("state") or "outcome_unknown"
            if entry.get("type") == "journal.end"
            else "unknown"
        ),
        "stream_sequence": entry.get("stream_sequence"),
        "timestamp_utc": entry.get("recorded_at"),
        "summary": entry.get("summary") or entry.get("text") or entry.get("state") or "",
        "output": entry.get("details") or entry.get("metadata") or entry,
        "input_summary": entry.get("input_summary") or {},
        "latency_ms": entry.get("latency_ms", 0),
    }


def interrupted_request_trace(db, identity, user_id):
    from fast_api.app.services.durable_stream_journal import read_stream_journal
    from fast_api.app.services.write_receipts import chat_workout_receipt

    record = db.scalar(
        select(models.IdempotencyRecord).where(
            models.IdempotencyRecord.id == identity,
            models.IdempotencyRecord.user_id == user_id,
            models.IdempotencyRecord.operation == "chat",
        )
    )
    if record is None:
        raise ValueError("Agent run not found")
    session_id = (record.request_json or {}).get("session_id")
    session = db.scalar(
        select(models.ConversationSession).where(
            models.ConversationSession.id == uuid.UUID(str(session_id)),
            models.ConversationSession.user_id == user_id,
        )
    )
    if session is None:
        raise ValueError("Agent run not found")
    journal = read_stream_journal(identity, user_id, session.id)
    if journal is None:
        raise ValueError("Agent run not found")
    events = []
    for index, raw in enumerate(journal["entries"]):
        node = stream_trace_node(raw)
        output = node["output"]
        events.append(
            {
                "event_id": node.get("event_id") or f"{identity}:stream:{index}",
                "order": index + 1,
                "parent_id": str(identity),
                "child_id": output.get("child_id"),
                "step_id": output.get("step_id"),
                "name": node["node"],
                "status": node["status"],
                "source": "durable_stream",
                "phase": "observe",
                "recorded_at": node.get("timestamp_utc") or "",
                "stream_sequence": node.get("stream_sequence"),
                "latency_ms": node["latency_ms"],
                "summary": node["summary"],
                "input": trace_detail(node.get("input_summary") or {}),
                "output": trace_detail(output),
                "error": None,
            }
        )
    return {
        "schema_version": "execution-trace/v1",
        "run_id": str(identity),
        "status": journal["state"],
        "write_receipts": [chat_workout_receipt(db, user_id, record.id, session.id)],
        "events": events,
        "snapshot": {},
        "coverage": {
            "saved_nodes": len(events),
            "saved_tool_calls": 0,
            "snapshot_available": False,
            "exact_model_requests": False,
            "ordering": "request_stream_sequence",
            "liveness": "not_checked",
            "damaged_tail": journal["damaged_tail"],
            "limitations": [
                "Persisted public events, not proof of business completion.",
                "No terminal record means unconfirmed, not confirmed process death.",
            ],
        },
    }
