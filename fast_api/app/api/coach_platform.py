"""Coach platform API — all endpoints require authentication via JWT Bearer token."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.schemas.agent import (
    AgentRunResponse,
    ChatHistoryMessageResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionResponse,
    DailyCheckinRequest,
    DashboardResponse,
    EvalRunRequest,
    EvalRunResponse,
    PlanAdjustRequest,
    PlanGenerateRequest,
    PlanResponse,
    UserProfileInput,
    WorkoutLogRequest,
)
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.agent_task_state import AgentTaskStateService
from fast_api.app.services.plan_reviewer import PlanReviewer

coach_router = APIRouter()


def get_service(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> CoachAgentService:
    return CoachAgentService(db)


@coach_router.post("/chat/sessions", response_model=ChatSessionResponse)
def create_chat_session(
    request: Request,
    payload: ChatSessionCreate,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    session = service.create_session(current_user.id, payload.display_name, payload.title)
    return ChatSessionResponse(
        session_id=session.id,
        user_id=session.user_id,
        title=session.title,
        created_at=session.created_at,
    )


@coach_router.get("/chat/sessions", response_model=list[ChatSessionResponse])
def list_chat_sessions(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    sessions = db.scalars(
        select(models.ConversationSession)
        .where(models.ConversationSession.user_id == current_user.id)
        .order_by(desc(models.ConversationSession.updated_at), desc(models.ConversationSession.created_at))
        .limit(max(1, min(limit, 100)))
    ).all()
    return [
        ChatSessionResponse(
            session_id=session.id,
            user_id=session.user_id,
            title=session.title,
            created_at=session.created_at,
        )
        for session in sessions
    ]


@coach_router.get(
    "/chat/sessions/{session_id}/messages",
    response_model=list[ChatHistoryMessageResponse],
)
def list_chat_messages(
    session_id: UUID,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    session = db.get(models.ConversationSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation session not found.")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot access another user's chat history.")

    capped_limit = max(1, min(limit, 500))
    messages = list(
        db.scalars(
            select(models.ChatMessage)
            .where(models.ChatMessage.session_id == session_id)
            .order_by(desc(models.ChatMessage.created_at))
            .limit(capped_limit)
        )
    )
    messages.reverse()
    return [
        ChatHistoryMessageResponse(
            id=message.id,
            session_id=message.session_id,
            user_id=message.user_id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )
        for message in messages
        if message.role in {"user", "assistant"}
    ]


@coach_router.post("/chat/messages", response_model=ChatMessageResponse)
async def send_chat_message(
    request: Request,
    payload: ChatMessageRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    try:
        return await service.handle_chat_message(
            payload.session_id, current_user.id, payload.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@coach_router.post("/chat/messages/stream")
async def stream_chat_message(
    request: Request,
    payload: ChatMessageRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    return StreamingResponse(
        service.stream_chat_events(
            payload.session_id,
            current_user.id,
            payload.message,
        ),
        media_type="application/x-ndjson; charset=utf-8",
    )


@coach_router.post("/profiles", response_model=dict[str, Any])
def upsert_profile(
    request: UserProfileInput,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    request.user_id = current_user.id
    profile = service.upsert_profile(request)
    return {
        "user_id": str(profile.user_id),
        "target_calories": profile.target_calories,
        "target_protein_g": profile.target_protein_g,
        "target_carbs_g": profile.target_carbs_g,
        "target_fat_g": profile.target_fat_g,
    }


@coach_router.post("/checkins/daily", response_model=dict[str, Any])
def record_daily_checkin(
    request: DailyCheckinRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    request.user_id = current_user.id
    return service.record_daily_checkin(request)


@coach_router.post("/workouts/logs", response_model=dict[str, Any])
def record_workout_log(
    request: WorkoutLogRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    request.user_id = current_user.id
    log = service.record_workout_log(request)
    return {"status": "recorded", "workout_log_id": str(log.id)}


@coach_router.post("/plans/generate", response_model=PlanResponse)
def generate_plan(
    request: Request,
    payload: PlanGenerateRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    payload.user_id = current_user.id
    plan = service.generate_plan(payload)
    return PlanResponse(
        user_id=plan.user_id,
        plan_id=plan.id,
        status=plan.status,
        plan=plan.plan_json,
        rationale=plan.rationale,
    )


@coach_router.post("/plans/adjust", response_model=PlanResponse)
def adjust_plan(
    request: PlanAdjustRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    request.user_id = current_user.id
    plan = service.adjust_plan(request)
    return PlanResponse(
        user_id=plan.user_id,
        plan_id=plan.id,
        status=plan.status,
        plan=plan.plan_json,
        rationale=plan.rationale,
    )


@coach_router.get("/users/{user_id}/dashboard", response_model=DashboardResponse)
def user_dashboard(
    user_id: UUID,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot access another user's dashboard.")
    return service.dashboard(user_id)


@coach_router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
def agent_run_detail(
    run_id: UUID,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    try:
        detail = service.agent_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if detail["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot access another user's agent run.")
    return detail


@coach_router.get("/agent-runs/{run_id}/replay", response_model=dict[str, Any])
def agent_run_replay_packet(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        return AgentTaskStateService(db).replay_packet(run_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@coach_router.get("/agent/tasks", response_model=list[dict[str, Any]])
def list_agent_tasks(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return AgentTaskStateService(db).list_active(current_user.id, limit=limit)


@coach_router.post("/evals/run", response_model=EvalRunResponse)
def run_evals(
    request: EvalRunRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    return service.run_evals(request.suite_name, request.persist_cases)


@coach_router.post("/plans/review", response_model=dict[str, Any])
def review_plan(
    period_days: int = 7,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate a periodic progress review (7 days = weekly, 30 days = monthly)."""
    reviewer = PlanReviewer(db)
    return reviewer.review(current_user.id, period_days=period_days)
