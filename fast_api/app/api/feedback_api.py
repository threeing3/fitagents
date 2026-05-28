"""User feedback API — submit and query coach reply feedback."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.schemas.agent import (
    FeedbackResponse,
    FeedbackStatsResponse,
    FeedbackSubmitRequest,
)

feedback_router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


@feedback_router.post("", response_model=FeedbackResponse, status_code=201)
def submit_feedback(
    body: FeedbackSubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> FeedbackResponse:
    """Submit a rating and optional comment for a coach reply."""
    message = db.get(models.ChatMessage, body.message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your message")
    if message.role != "assistant":
        raise HTTPException(status_code=400, detail="Can only rate assistant messages")

    # Find conversation session from the message
    session_id = message.session_id

    # Check for existing feedback on this message (upsert)
    existing = (
        db.query(models.UserFeedback)
        .filter_by(user_id=current_user.id, message_id=body.message_id)
        .first()
    )
    if existing:
        existing.rating = body.rating
        existing.category = body.category
        existing.comment = body.comment
        db.commit()
        db.refresh(existing)
        return _feedback_response(existing)

    feedback = models.UserFeedback(
        user_id=current_user.id,
        session_id=session_id,
        message_id=body.message_id,
        rating=body.rating,
        category=body.category,
        comment=body.comment,
        coach_reply_snapshot=message.content[:2000] if message.content else None,
        metadata_json={
            "submitted_via": "api",
            "client_ip": request.client.host if request.client else None,
        },
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return _feedback_response(feedback)


@feedback_router.get("/stats", response_model=FeedbackStatsResponse)
def get_feedback_stats(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> FeedbackStatsResponse:
    """Get feedback statistics for the current user."""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    base = (
        db.query(models.UserFeedback)
        .filter(
            models.UserFeedback.user_id == current_user.id,
            models.UserFeedback.created_at >= cutoff,
        )
    )
    total = base.count()
    avg_rating = base.with_entities(func.avg(models.UserFeedback.rating)).scalar() or 0.0

    # Rating distribution
    dist_query = (
        base.with_entities(models.UserFeedback.rating, func.count(models.UserFeedback.id))
        .group_by(models.UserFeedback.rating)
        .all()
    )
    distribution: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for rating, count in dist_query:
        distribution[rating] = count

    # Top categories
    cat_query = (
        base.with_entities(models.UserFeedback.category, func.count(models.UserFeedback.id))
        .filter(models.UserFeedback.category.isnot(None))
        .group_by(models.UserFeedback.category)
        .order_by(func.count(models.UserFeedback.id).desc())
        .limit(5)
        .all()
    )
    top_categories = [{"category": c, "count": n} for c, n in cat_query]

    # Recent feedback
    recent = (
        base.order_by(models.UserFeedback.created_at.desc())
        .limit(10)
        .all()
    )

    return FeedbackStatsResponse(
        total_feedback=total,
        average_rating=round(float(avg_rating), 2),
        rating_distribution=distribution,
        top_categories=top_categories,
        recent_feedback=[_feedback_response(f) for f in recent],
    )


@feedback_router.get("", response_model=list[FeedbackResponse])
def list_feedback(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    min_rating: int | None = Query(default=None, ge=1, le=5),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[FeedbackResponse]:
    """List feedback entries for the current user."""
    q = (
        db.query(models.UserFeedback)
        .filter(models.UserFeedback.user_id == current_user.id)
    )
    if min_rating is not None:
        q = q.filter(models.UserFeedback.rating >= min_rating)
    results = (
        q.order_by(models.UserFeedback.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_feedback_response(f) for f in results]


def _feedback_response(f: models.UserFeedback) -> FeedbackResponse:
    return FeedbackResponse(
        id=f.id,
        user_id=f.user_id,
        session_id=f.session_id,
        message_id=f.message_id,
        rating=f.rating,
        category=f.category,
        comment=f.comment,
        created_at=f.created_at,
    )
