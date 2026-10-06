"""Authenticated, structured creation and lifecycle control; no LLM parsing."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.services.responsibilities import ResponsibilityService

responsibility_router = APIRouter(prefix="/v1/responsibilities", tags=["responsibilities"])


class WeeklyReviewRequest(BaseModel):
    source_instruction: str = Field(min_length=1, max_length=2000)
    weeks: int = Field(default=4, ge=1, le=52)
    weekday: int = Field(default=6, ge=0, le=6)
    hour: int = Field(default=18, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class LifecycleRequest(BaseModel):
    action: Literal["pause", "resume", "cancel"]


class SetReductionRequest(BaseModel):
    plan_id: UUID
    day_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reduce_by: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=500)


@responsibility_router.post("/{task_id}/plan-proposal")
def propose_set_reduction(
    task_id: UUID,
    body: SetReductionRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService

    try:
        result = ApprovedPlanAdjustmentService(db).propose(
            user.id, task_id, body.plan_id, body.day_date, body.reduce_by, body.reason
        )
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@responsibility_router.post("/weekly-review")
def create_weekly_review(
    body: WeeklyReviewRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    service = ResponsibilityService(db)
    try:
        task = service.create_weekly(
            user.id,
            body.source_instruction,
            weeks=body.weeks,
            weekday=body.weekday,
            hour=body.hour,
            minute=body.minute,
        )
        db.commit()
        return service.snapshot(task)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@responsibility_router.get("")
def list_responsibilities(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
) -> list[dict[str, Any]]:
    return ResponsibilityService(db).list_for_user(user.id)


@responsibility_router.post("/{task_id}/lifecycle")
def change_lifecycle(
    task_id: UUID,
    body: LifecycleRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    service = ResponsibilityService(db)
    try:
        task = service.transition(task_id, user.id, body.action)
        db.commit()
        return service.snapshot(task)
    except ValueError as exc:
        db.rollback()
        code = 404 if str(exc) == "Responsibility not found" else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
