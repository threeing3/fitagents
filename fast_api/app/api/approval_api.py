"""Approval API — endpoints for the user to approve or deny pending tool calls.

This mirrors Claude Code's permission prompt: the agent pauses before executing
write operations, the user sees what will happen, and explicitly approves or denies.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.services.approval_manager import (
    ApprovalManager,
)
from fast_api.app.services.background_trace_reference import background_trace_reference

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
    expires_at: str


class ApprovalStatsResponse(BaseModel):
    pending_count: int
    auto_approved_tools: list[str]
    total_decisions: int


def _get_manager(db: Session = Depends(get_db)) -> ApprovalManager:
    """Each request reads the same durable approval records."""
    return ApprovalManager(db)


@approval_router.get("/pending", response_model=list[PendingApprovalResponse])
def list_pending_approvals(
    manager: ApprovalManager = Depends(_get_manager),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all pending approval requests for the current user."""
    pending = manager.get_pending(current_user.id)
    manager.db.commit()
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
            "expires_at": a.expires_at,
        }
        for a in pending
    ]


@approval_router.get("/history")
def approval_history(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Owner-scoped action history; approval is not execution confirmation."""
    manager = ApprovalManager(db)
    rows = db.scalars(
        select(models.PendingApproval)
        .where(models.PendingApproval.user_id == current_user.id)
        .order_by(models.PendingApproval.created_at.desc(), models.PendingApproval.id.desc())
        .limit(limit)
    ).all()
    result = []
    for row in rows:
        approval = manager.get(str(row.id))
        job = db.get(models.BackgroundTask, row.job_id) if row.job_id else None
        if job is not None and job.user_id != current_user.id:
            job = None
        result.append(
            {
                "approval_id": approval.approval_id,
                "tool_name": approval.tool_name,
                "tool_description": approval.tool_description,
                "input_preview": approval.input_summary,
                "context": approval.context,
                "status": approval.status,
                "created_at": approval.created_at,
                "expires_at": approval.expires_at,
                "job_id": str(job.id) if job else None,
                "job_status": job.status if job else None,
                "job_attempts": job.attempts if job else None,
                "execution_trace_run_id": background_trace_reference(db, job),
                "result": job.result_json if job else {},
                "error": job.error if job else None,
            }
        )
    db.commit()
    return result


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
        manager.db.commit()
        raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

    if body.action == "approve":
        result = manager.approve(body.approval_id)
        if result is None:
            raise HTTPException(status_code=500, detail="Failed to approve")
        manager.db.commit()
        return {"status": "approved", "approval_id": body.approval_id}
    else:
        result = manager.deny(body.approval_id, reason=body.reason)
        if result is None:
            raise HTTPException(status_code=500, detail="Failed to deny")
        manager.db.commit()
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

    # Past approvals do not confer standing permission.
    auto_tools: list[str] = []

    pending_count = len(manager.get_pending(current_user.id))
    db.commit()

    return {
        "pending_count": pending_count,
        "auto_approved_tools": auto_tools,
        "total_decisions": total,
    }
