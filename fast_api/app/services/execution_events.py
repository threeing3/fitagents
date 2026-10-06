"""Public execution journal: evidence summaries, never hidden model reasoning."""

import uuid
from datetime import datetime, timezone
from typing import Any


def execution_event(
    name: str,
    status: str,
    summary: str,
    *,
    source: str = "runtime",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {
        "pending",
        "running",
        "completed",
        "failed",
        "blocked",
        "skipped",
        "outcome_unknown",
    }:
        raise ValueError("Invalid public execution status")
    if source not in {"runtime", "rule", "tool", "user", "model_summary"}:
        raise ValueError("Invalid public execution source")
    return {
        "type": "execution_event",
        "event_id": str(uuid.uuid4()),
        "name": name,
        "status": status,
        "summary": summary,
        "source": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
    }


def append_approval_event(row, name, status, summary, *, source="runtime", details=None):
    entry = execution_event(name, status, summary, source=source, details=details)
    row.context_json = {
        **row.context_json,
        "execution_events": [*row.context_json.get("execution_events", []), entry],
    }
    return entry


def command_events(updates):
    approval = updates.get("approval", {})
    task = updates.get("responsibility", {})
    awaiting_correction = updates.get("workout_correction_status") == "awaiting_confirmation"
    blocked = (
        updates.get("responsibility_status") in {"clarify", "rejected"}
        or updates.get("approval_status") == "rejected"
        or updates.get("followup_status") in {"clarify", "rejected"}
        or updates.get("workout_correction_status")
        in {"clarify", "rejected", "awaiting_confirmation"}
    )
    return [
        execution_event(
            "command.route", "completed", "按明确指令模板处理；未调用模型推断授权。", source="rule"
        ),
        execution_event(
            "command.result",
            "pending" if awaiting_correction else "blocked" if blocked else "completed",
            "已核对原值、新值及版本，等待用户确认；尚未更正训练。"
            if awaiting_correction
            else "指令需要澄清或已被拒绝，未按请求执行变更。"
            if blocked
            else "指令结果已保存；审批结果与实际执行结果分别记录。",
            details={
                "responsibility_id": task.get("id"),
                "approval_id": approval.get("approval_id"),
                "approval_status": approval.get("status") or updates.get("approval_status"),
                "followup_id": (updates.get("followup") or {}).get("id"),
                "followup_status": updates.get("followup_status"),
                "workout_correction_status": updates.get("workout_correction_status"),
                "correction_audit_id": (updates.get("correction") or {}).get("audit_id"),
            },
        ),
    ]


def public_run_events(db, run, user_id):
    """Replay saved command events, plus current linked approval journal."""
    import uuid

    from fast_api.app.db import models

    if run.user_id != user_id:
        return []
    entries = [node for node in run.nodes if node.get("type") == "execution_event"]
    linked = {node.get("details", {}).get("approval_id") for node in entries} - {None}
    for approval_id in linked:
        try:
            row = db.get(models.PendingApproval, uuid.UUID(approval_id))
        except (ValueError, TypeError):
            continue
        if row is not None and row.user_id == user_id:
            entries.extend(row.context_json.get("execution_events", []))
    return sorted(entries, key=lambda node: node.get("recorded_at", ""))
