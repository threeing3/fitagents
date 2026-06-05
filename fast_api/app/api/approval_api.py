"""Approval API — endpoints for the user to approve or deny pending tool calls.

This mirrors Claude Code's permission prompt: the agent pauses before executing
write operations, the user sees what will happen, and explicitly approves or denies.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.services.approval_manager import (
    ApprovalManager,
    ApprovalAction,
)

logger = logging.getLogger(__name__)

approval_router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


class ApprovalDecision(BaseModel):
    approval_id: str = Field(..., description="The pending approval ID to act on")
    action: str = Field(..., pattern="^(approve|deny)$")
    reason: str = Field(default="", max_length=500)


class PendingApprovalResponse(BaseModel):
    approval_id: str
    tool_name: str
    tool_description: str
    permission_level: str
    input_preview: dict[str, Any]
    context: dict[str, Any]
    status: str
    created_at: str


class ApprovalStatsResponse(BaseModel):
    pending_count: int
    auto_approved_tools: list[str]
    total_decisions: int


# ---- In-memory approval manager (per-request lifecycle) ----
# In production, this would be backed by Redis or a DB table for multi-process support.
_approval_managers: dict[str, ApprovalManager] = {}


def _get_manager(db: Session = Depends(get_db)) -> ApprovalManager:
    """Get or create an ApprovalManager for the current session context."""
    # Simple strategy: one manager per db session for the request lifecycle
    key = str(id(db))
    if key not in _approval_managers:
        _approval_managers[key] = ApprovalManager(db)
    return _approval_managers[key]


@approval_router.get("/pending", response_model=list[PendingApprovalResponse])
def list_pending_approvals(
    manager: ApprovalManager = Depends(_get_manager),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all pending approval requests for the current user."""
    pending = manager.get_pending(current_user.id)
    return [
        {
            "approval_id": a.approval_id,
            "tool_name": a.tool_name,
            "tool_description": a.tool_description,
            "permission_level": a.permission_level,
            "input_preview": a.input_summary,
            "context": a.context,
            "status": a.status,
            "created_at": a.created_at,
        }
        for a in pending
    ]


@approval_router.post("/decide", response_model=dict[str, str])
def decide_approval(
    body: ApprovalDecision,
    manager: ApprovalManager = Depends(_get_manager),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Approve or deny a pending tool call."""
    approval = manager.get(body.approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found or already expired")
    if approval.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your approval request")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

    if body.action == "approve":
        result = manager.approve(body.approval_id)
        if result is None:
            raise HTTPException(status_code=500, detail="Failed to approve")
        return {"status": "approved", "approval_id": body.approval_id}
    else:
        result = manager.deny(body.approval_id, reason=body.reason)
        if result is None:
            raise HTTPException(status_code=500, detail="Failed to deny")
        return {"status": "denied", "approval_id": body.approval_id}


@approval_router.get("/stats", response_model=ApprovalStatsResponse)
def approval_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    manager: ApprovalManager = Depends(_get_manager),
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Get approval statistics for the current user."""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    total = (
        db.query(models.AgentDecision)
        .filter(
            models.AgentDecision.user_id == current_user.id,
            models.AgentDecision.decision_type.like("approve_tool:%"),
            models.AgentDecision.created_at >= cutoff,
        )
        .count()
    )

    # Find tools that have been auto-approved
    approved_tools = (
        db.query(models.AgentDecision.decision_type)
        .filter(
            models.AgentDecision.user_id == current_user.id,
            models.AgentDecision.accepted_by_user == True,
            models.AgentDecision.decision_type.like("approve_tool:%"),
        )
        .distinct()
        .all()
    )
    auto_tools = [t[0].replace("approve_tool:", "") for t in approved_tools]

    pending_count = len(manager.get_pending(current_user.id))

    return {
        "pending_count": pending_count,
        "auto_approved_tools": auto_tools,
        "total_decisions": total,
    }
