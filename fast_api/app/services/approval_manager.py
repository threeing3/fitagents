"""Database-backed approvals bound to one action; caller controls transactions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fast_api.app.db import models

APPROVAL_TTL_SECONDS = 300
MAX_PENDING_PER_USER = 5


class ApprovalAction(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


class ToolPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    WRITE_CANDIDATE = "write_candidate"


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


@dataclass
class ApprovalRequest:
    approval_id: str
    user_id: uuid.UUID
    session_id: uuid.UUID | None
    tool_name: str
    tool_description: str
    permission_level: str
    input_summary: dict[str, Any]
    context: dict[str, Any]
    status: str
    created_at: str
    expires_at: str
    decided_at: str | None = None
    decided_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        result = asdict(self)
        result["user_id"] = str(self.user_id)
        result["session_id"] = str(self.session_id) if self.session_id else None
        return result

    def is_expired(self) -> bool:
        return self.status == "pending" and datetime.now(timezone.utc) >= datetime.fromisoformat(
            self.expires_at
        )


class ApprovalManager:
    """No process-local pending state and no history-based privilege escalation."""

    def __init__(self, db: Session):
        self.db = db

    def requires_approval(self, tool_name: str, permission_level: str, side_effects: bool) -> bool:
        return side_effects or permission_level != ToolPermission.READ

    def check_auto_approve(self, user_id: uuid.UUID, tool_name: str, intent: str) -> bool:
        # Repeated approval is not a future authorization grant.
        return False

    def create_approval(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None,
        tool_name: str,
        tool_description: str,
        permission_level: str,
        input_summary: dict[str, Any],
        context: dict[str, Any] | None = None,
        *,
        input_json: dict[str, Any] | None = None,
        job_id: uuid.UUID | None = None,
        ttl_seconds: int = APPROVAL_TTL_SECONDS,
    ) -> ApprovalRequest:
        # Lock owner to serialize the pending limit on PostgreSQL.
        self.db.scalar(
            select(models.User).where(models.User.id == user_id).with_for_update(key_share=True)
        )
        if len(self.get_pending(user_id)) >= MAX_PENDING_PER_USER:
            raise ValueError("Pending approval limit reached; decide existing requests first")
        now = datetime.now(timezone.utc)
        row = models.PendingApproval(
            user_id=user_id,
            session_id=session_id,
            job_id=job_id,
            tool_name=tool_name,
            tool_description=tool_description,
            permission_level=permission_level,
            input_summary=input_summary,
            input_json=input_json or {},
            context_json=context or {},
            status="pending",
            expires_at=now + timedelta(seconds=max(1, min(ttl_seconds, 604800))),
        )
        self.db.add(row)
        self.db.flush()
        from fast_api.app.services.execution_events import append_approval_event

        append_approval_event(
            row,
            "approval.wait",
            "blocked",
            "草案已保存，等待用户批准；尚未执行工具。",
            details={
                "approval_id": str(row.id),
                "tool": tool_name,
                "expires_at": _utc(row.expires_at).isoformat(),
            },
        )
        return self._snapshot(row)

    def _row(self, approval_id: str) -> models.PendingApproval | None:
        try:
            key = uuid.UUID(approval_id)
        except ValueError:
            return None
        return self.db.scalar(
            select(models.PendingApproval)
            .where(models.PendingApproval.id == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get(self, approval_id: str) -> ApprovalRequest | None:
        row = self._row(approval_id)
        if row is None:
            return None
        self._expire(row)
        return self._snapshot(row)

    def get_pending(self, user_id: uuid.UUID) -> list[ApprovalRequest]:
        rows = self.db.scalars(
            select(models.PendingApproval)
            .where(
                models.PendingApproval.user_id == user_id,
                models.PendingApproval.status == "pending",
            )
            .with_for_update()
        ).all()
        for row in rows:
            self._expire(row)
        self.db.flush()
        return [self._snapshot(row) for row in rows if row.status == "pending"]

    def approve(self, approval_id: str, auto: bool = False) -> ApprovalRequest | None:
        if auto:
            raise ValueError("Implicit automatic approval is not permitted")
        return self._decide(approval_id, "approved", "")

    def deny(self, approval_id: str, reason: str = "") -> ApprovalRequest | None:
        return self._decide(approval_id, "denied", reason)

    def _decide(self, approval_id: str, status: str, reason: str) -> ApprovalRequest | None:
        row = self._row(approval_id)
        if row is None:
            return None
        self._expire(row)
        if row.status != "pending":
            return None
        changed = self.db.execute(
            update(models.PendingApproval)
            .where(models.PendingApproval.id == row.id, models.PendingApproval.status == "pending")
            .values(status=status, decided_at=datetime.now(timezone.utc), decided_by="user")
        )
        if changed.rowcount != 1:
            return None
        self.db.refresh(row)
        if reason:
            row.context_json = {**row.context_json, "deny_reason": reason}
        from fast_api.app.services.execution_events import append_approval_event

        append_approval_event(
            row,
            "approval.decision",
            "completed",
            "用户批准了这一动作；批准不代表执行成功。"
            if status == "approved"
            else "用户拒绝了这一动作；不执行工具。",
            source="user",
            details={"decision": status, "approval_id": str(row.id)},
        )
        if row.job_id:
            job = self.db.get(models.BackgroundTask, row.job_id)
            if job is None or job.user_id != row.user_id or job.status != "waiting_approval":
                raise ValueError("Approval execution job is missing or no longer waiting")
            job.status = "queued" if status == "approved" else "cancelled"
        self.db.add(
            models.AgentDecision(
                user_id=row.user_id,
                decision_type="approve_tool:" + row.tool_name,
                input_summary=str(row.input_summary),
                context_used=row.context_json,
                decision_result=status,
                reason=reason or "explicit user decision",
                confidence_score=1.0,
                accepted_by_user=status == "approved",
            )
        )
        self.db.flush()
        return self._snapshot(row)

    def claim_action(
        self,
        approval_id: str,
        user_id: uuid.UUID,
        tool_name: str,
        input_json: dict[str, Any],
        job_id: uuid.UUID | None = None,
    ) -> models.PendingApproval:
        row = self._row(approval_id)
        if row is None or row.user_id != user_id or row.tool_name != tool_name:
            raise ValueError("Approval action does not match")
        self._expire(row)
        if (
            row.input_json != input_json
            or row.job_id != job_id
            or row.status != "approved"
            or datetime.now(timezone.utc) >= _utc(row.expires_at)
        ):
            raise ValueError("Approval is expired, consumed, or action parameters changed")
        claimed = self.db.execute(
            update(models.PendingApproval)
            .where(models.PendingApproval.id == row.id, models.PendingApproval.status == "approved")
            .values(status="executing")
        )
        if claimed.rowcount != 1:
            raise ValueError("Approval already claimed")
        self.db.refresh(row)
        return row

    def _expire(self, row: models.PendingApproval) -> None:
        if row.status in {"pending", "approved"} and datetime.now(timezone.utc) >= _utc(
            row.expires_at
        ):
            row.status = "expired"
            row.decided_at = datetime.now(timezone.utc)
            row.decided_by = "expired"
            from fast_api.app.services.execution_events import append_approval_event

            append_approval_event(row, "approval.expired", "blocked", "审批已过期，阻止工具执行。")
            if row.job_id:
                job = self.db.get(models.BackgroundTask, row.job_id)
                if job and job.status in {"waiting_approval", "queued"}:
                    job.status = "cancelled"

    @staticmethod
    def _snapshot(row: models.PendingApproval) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=str(row.id),
            user_id=row.user_id,
            session_id=row.session_id,
            tool_name=row.tool_name,
            tool_description=row.tool_description,
            permission_level=row.permission_level,
            input_summary=row.input_summary,
            context=row.context_json,
            status=row.status,
            created_at=_utc(row.created_at).isoformat(),
            expires_at=_utc(row.expires_at).isoformat(),
            decided_at=_utc(row.decided_at).isoformat() if row.decided_at else None,
            decided_by=row.decided_by,
        )


def summarize_tool_for_approval(tool_name: str, input_json: dict[str, Any]) -> dict[str, Any]:
    """Create a human-readable summary of what the tool will do.

    This is shown to the user in the approval prompt.
    """
    summaries: dict[str, str] = {
        "profile.extract": "Extract and update your fitness profile from your message",
        "memory.write": "Save new information to your long-term fitness memory",
        "plan.generate": "Generate a new training plan for you",
        "plan.repair": "Fix issues found in the generated training plan",
        "response.persist": "Save the coach's response and execution trace",
        "memory.verify": "Verify memory candidates before saving",
        "context.build": "Build context for understanding your request",
        "plan.decide": "Decide whether to generate a training plan",
        "plan.verify": "Verify the training plan against safety constraints",
        "response.verify": "Verify the coach's response against policies",
        "response.repair": "Fix issues found in the coach's response",
        "guardrail.check": "Run safety checks on the response",
    }

    description = summaries.get(tool_name, f"Execute tool: {tool_name}")

    # Create a safe input summary (truncate long values)
    safe_input: dict[str, Any] = {}
    for k, v in input_json.items():
        if isinstance(v, str) and len(v) > 200:
            safe_input[k] = v[:200] + "..."
        elif isinstance(v, dict):
            safe_input[k] = f"<object with {len(v)} keys>"
        elif isinstance(v, list):
            safe_input[k] = f"<list with {len(v)} items>"
        else:
            safe_input[k] = v

    return {
        "tool_name": tool_name,
        "description": description,
        "input_preview": safe_input,
    }
