"""
Human-in-the-loop approval system — Claude Code-style permission enforcement.

When a tool has side_effects=True (write operations), the Agent Runtime
pauses and creates a PendingApproval record. The user must explicitly
approve or deny the action before execution continues.

Key design decisions (mirroring Claude Code):
- Read tools execute immediately (no approval needed)
- Write tools ALWAYS require approval unless the user has granted blanket permission
- Each approval has a TTL — if not acted on within 5 minutes, it auto-denies
- Approval decisions are stored and can inform future auto-approval (trusted patterns)
"""

import logging
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from fast_api.app.db import models

logger = logging.getLogger(__name__)

# Approvals expire after 5 minutes of no response
APPROVAL_TTL_SECONDS = 300
# Auto-approve trusted patterns if user has approved the same tool+intent 3+ times
AUTO_APPROVE_THRESHOLD = 3
# Max pending approvals per user at once
MAX_PENDING_PER_USER = 5


class ApprovalAction(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


class ToolPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    WRITE_CANDIDATE = "write_candidate"


@dataclass
class ApprovalRequest:
    """Represents a tool call that needs user confirmation before execution."""

    approval_id: str
    user_id: uuid.UUID
    session_id: uuid.UUID | None
    tool_name: str
    tool_description: str
    permission_level: str
    input_summary: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, approved, denied, expired
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    decided_at: str | None = None
    decided_by: str | None = None  # "user" | "auto_approved" | "expired"

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "user_id": str(self.user_id),
            "session_id": str(self.session_id) if self.session_id else None,
            "tool_name": self.tool_name,
            "tool_description": self.tool_description,
            "permission_level": self.permission_level,
            "input_summary": self.input_summary,
            "context": self.context,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
        }

    def is_expired(self) -> bool:
        if self.status != "pending":
            return False
        created = datetime.fromisoformat(self.created_at)
        return datetime.utcnow() - created > timedelta(seconds=APPROVAL_TTL_SECONDS)


class ApprovalManager:
    """Manages the approval lifecycle for write tools.

    In-memory storage for pending approvals (fast, session-scoped).
    Persisted to AgentDecision table for completed decisions (audit trail).
    """

    def __init__(self, db: Session):
        self.db = db
        self._pending: dict[str, ApprovalRequest] = {}

    def requires_approval(self, tool_name: str, permission_level: str, side_effects: bool) -> bool:
        """Determine if a tool call needs user approval.

        Logic:
        - Read-only tools: never need approval
        - Write tools with side_effects: always need approval
        - write_candidate tools (like profile.extract outputs): need approval
          unless the user has a history of approving this tool+intent
        """
        if permission_level == ToolPermission.READ:
            return False
        if not side_effects:
            # write_candidate without side_effects still needs approval
            # because its outputs feed into write operations
            return permission_level != ToolPermission.READ
        return True

    def create_approval(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None,
        tool_name: str,
        tool_description: str,
        permission_level: str,
        input_summary: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Create a pending approval request. Blocks the tool until resolved."""
        # Clean up expired approvals
        self._cleanup_expired()

        # Check count limit
        user_pending = [
            a for a in self._pending.values()
            if a.user_id == user_id and a.status == "pending"
        ]
        if len(user_pending) >= MAX_PENDING_PER_USER:
            # Deny the oldest pending to make room
            oldest = min(user_pending, key=lambda a: a.created_at)
            oldest.status = "denied"
            oldest.decided_at = datetime.utcnow().isoformat()
            oldest.decided_by = "auto_denied_max_pending"
            logger.warning(
                "Auto-denied approval %s for user %s (max pending reached)",
                oldest.approval_id, user_id,
            )

        approval = ApprovalRequest(
            approval_id=str(uuid.uuid4()),
            user_id=user_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_description=tool_description,
            permission_level=permission_level,
            input_summary=input_summary,
            context=context or {},
        )
        self._pending[approval.approval_id] = approval
        logger.info(
            "Created pending approval %s for tool=%s user=%s",
            approval.approval_id, tool_name, user_id,
        )
        return approval

    def check_auto_approve(self, user_id: uuid.UUID, tool_name: str, intent: str) -> bool:
        """Check if this user+tool+intent pattern can be auto-approved.

        Returns True if the user has approved this pattern >= AUTO_APPROVE_THRESHOLD times
        in the last 30 days.
        """
        cutoff = datetime.utcnow() - timedelta(days=30)
        count = (
            self.db.query(models.AgentDecision)
            .filter(
                models.AgentDecision.user_id == user_id,
                models.AgentDecision.decision_type == f"approve_tool:{tool_name}",
                models.AgentDecision.input_summary.contains(intent),
                models.AgentDecision.accepted_by_user == True,
                models.AgentDecision.created_at >= cutoff,
            )
            .count()
        )
        return count >= AUTO_APPROVE_THRESHOLD

    def approve(self, approval_id: str, auto: bool = False) -> ApprovalRequest | None:
        """Approve a pending tool call."""
        approval = self._pending.get(approval_id)
        if approval is None:
            return None
        if approval.status != "pending":
            return None
        approval.status = "approved"
        approval.decided_at = datetime.utcnow().isoformat()
        approval.decided_by = "auto_approved" if auto else "user"

        # Persist to AgentDecision for audit trail and pattern learning
        self._persist_decision(approval, True)

        return approval

    def deny(self, approval_id: str, reason: str = "") -> ApprovalRequest | None:
        """Deny a pending tool call."""
        approval = self._pending.get(approval_id)
        if approval is None:
            return None
        if approval.status != "pending":
            return None
        approval.status = "denied"
        approval.decided_at = datetime.utcnow().isoformat()
        approval.decided_by = "user"
        approval.context["deny_reason"] = reason

        self._persist_decision(approval, False)
        return approval

    def get_pending(self, user_id: uuid.UUID) -> list[ApprovalRequest]:
        """Get all pending approvals for a user."""
        self._cleanup_expired()
        return [
            a for a in self._pending.values()
            if a.user_id == user_id and a.status == "pending"
        ]

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._pending.get(approval_id)

    def _cleanup_expired(self) -> None:
        expired = [
            aid for aid, a in self._pending.items()
            if a.status == "pending" and a.is_expired()
        ]
        for aid in expired:
            approval = self._pending[aid]
            approval.status = "expired"
            approval.decided_at = datetime.utcnow().isoformat()
            approval.decided_by = "expired"
            self._persist_decision(approval, False)

    def _persist_decision(self, approval: ApprovalRequest, accepted: bool) -> None:
        try:
            self.db.add(models.AgentDecision(
                user_id=approval.user_id,
                decision_type=f"approve_tool:{approval.tool_name}",
                input_summary=(
                    f"Tool: {approval.tool_name}, "
                    f"Permission: {approval.permission_level}, "
                    f"Input: {approval.input_summary}"
                ),
                context_used=approval.context,
                decision_result=approval.status,
                reason=f"Decided by {approval.decided_by or 'unknown'}",
                confidence_score=1.0,
                accepted_by_user=accepted,
            ))
            self.db.commit()
        except Exception as exc:
            logger.warning("Failed to persist approval decision: %s", exc)
            self.db.rollback()


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
