"""Coach platform API — all endpoints require authentication via JWT Bearer token."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, desc, select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.core.config import get_settings
from fast_api.app.core.rate_limit import limiter
from fast_api.app.core.redis_client import get_json_cache, set_json_cache
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.schemas.agent import (
    AgentRunResponse,
    BackgroundTaskResponse,
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
    WorkoutCorrectionRequest,
    WorkoutLogRequest,
)
from fast_api.app.services.agent_task_state import AgentTaskStateService
from fast_api.app.services.background_tasks import BackgroundTaskQueue
from fast_api.app.services.chat_request_status import get_chat_request_status
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.plan_reviewer import PlanReviewer

coach_router = APIRouter()
settings = get_settings()


def get_service(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> CoachAgentService:
    return CoachAgentService(
        db,
        ModelProvider(user_id=current_user.id, endpoint="coach"),
    )


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
        .order_by(
            desc(models.ConversationSession.updated_at), desc(models.ConversationSession.created_at)
        )
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
            .order_by(
                desc(models.ChatMessage.created_at),
                desc(case((models.ChatMessage.role == "assistant", 1), else_=0)),
                desc(models.ChatMessage.id),
            )
            .limit(capped_limit)
        )
    )
    messages.reverse()
    from fast_api.app.services.execution_events import public_run_events

    runs = db.scalars(
        select(models.AgentRun)
        .where(
            models.AgentRun.user_id == current_user.id,
            models.AgentRun.session_id == session_id,
            models.AgentRun.run_type.in_(
                ["responsibility_command", "chat", "chat_stream", "chat_llm_agent"]
            ),
        )
        .order_by(desc(models.AgentRun.started_at))
        .limit(capped_limit)
    ).all()
    message_ids = {str(message.id) for message in messages}
    journals = {}
    run_ids = {}
    for run in runs:
        for node in run.nodes:
            if (
                node.get("type") in {"ResponsibilityCommand", "ChatExecutionJournal"}
                and node.get("message_id") in message_ids
            ):
                journals[node["message_id"]] = public_run_events(db, run, current_user.id)
                run_ids.setdefault(node["message_id"], run.id)
    return [
        ChatHistoryMessageResponse(
            id=message.id,
            session_id=message.session_id,
            user_id=message.user_id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            execution_events=journals.get(str(message.id), []),
            agent_run_id=run_ids.get(str(message.id)),
        )
        for message in messages
        if message.role in {"user", "assistant"}
    ]


@coach_router.get("/chat/requests/status", response_model=dict[str, Any])
def chat_request_status(
    session_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        return get_chat_request_status(db, current_user.id, session_id, idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@coach_router.get("/chat/sessions/{session_id}/subagents", response_model=list[dict[str, Any]])
def list_session_subagents(
    session_id: UUID,
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.subagent_journal import SubagentJournal

    try:
        return SubagentJournal(db).list_catalogs(current_user.id, session_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Conversation session not found.") from exc


class SubagentReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    confirm_no_retry: Literal[True]

    @field_validator("confirm_no_retry", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit no-retry confirmation is required")
        return value


class SubagentStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["training", "nutrition", "recovery", "evidence_analysis", "plan_planning"]
    message: str = Field(min_length=1, max_length=4000)


@coach_router.post("/chat/sessions/{session_id}/subagents/{parent_id}/stop")
def stop_subagent_tree(
    session_id: UUID,
    parent_id: UUID,
    payload: SubagentReconcileRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.subagent_tree_control import request_tree_stop

    try:
        return request_tree_stop(
            db, parent_id, current_user.id, session_id, payload.expected_revision
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404 if str(exc) in {"scope_mismatch", "catalog_not_found"} else 409,
            detail="Cannot stop this scoped tree; refresh its catalog.",
        ) from exc


@coach_router.post("/chat/sessions/{session_id}/subagent-requests/reconcile")
def reconcile_subagent_request(
    session_id: UUID,
    payload: SubagentReconcileRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.subagent_sessions import SubagentSessions

    try:
        return SubagentSessions(db, None).reconcile_request(
            current_user.id, session_id, idempotency_key, payload.expected_revision
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404 if str(exc) in {"scope_mismatch", "request_not_found"} else 409,
            detail="Receipt cannot be reconciled; inspect the catalog and current request state.",
        ) from exc


class SubagentContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_catalog_revision: int = Field(ge=1, strict=True)
    message: str = Field(min_length=1, max_length=4000)


class SubagentCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_no_retry: Literal[True]

    @field_validator("confirm_no_retry", mode="before")
    @classmethod
    def strict_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean confirmation required")
        return value


@coach_router.post("/chat/sessions/{session_id}/subagent-requests/cancel")
def cancel_subagent_request(
    session_id: UUID,
    payload: SubagentCancelRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.subagent_sessions import SubagentSessions

    try:
        return SubagentSessions(db, None).request_cancel(
            current_user.id, session_id, idempotency_key
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409 if str(exc) == "independent_journal_backend_unsupported" else 404,
            detail="Scoped cancellation request unavailable.",
        ) from exc


async def _subagent_turn(service, current_user, session_id, payload, key, parent_id=None):
    from fast_api.app.services.subagent_sessions import SubagentSessions

    try:
        return await SubagentSessions(service.db, service.model_provider).execute(
            current_user.id,
            session_id,
            payload.message,
            key,
            role=getattr(payload, "role", None),
            parent_id=parent_id,
            expected_catalog_revision=getattr(payload, "expected_catalog_revision", None),
        )
    except ValueError as exc:
        service.db.rollback()
        raise HTTPException(
            status_code=404 if str(exc) == "scope_mismatch" else 409,
            detail="Conversation session not found."
            if str(exc) == "scope_mismatch"
            else "Subagent request cannot be started; inspect its recorded status.",
        ) from exc


@coach_router.post("/chat/sessions/{session_id}/subagents")
async def start_subagent_session(
    session_id: UUID,
    payload: SubagentStartRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    return await _subagent_turn(service, current_user, session_id, payload, idempotency_key)


@coach_router.post("/chat/sessions/{session_id}/subagents/{parent_id}/continue")
async def continue_subagent_session(
    session_id: UUID,
    parent_id: UUID,
    payload: SubagentContinueRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    return await _subagent_turn(
        service, current_user, session_id, payload, idempotency_key, parent_id
    )


@coach_router.get("/chat/sessions/{session_id}/subagent-requests/status")
def subagent_request_status(
    session_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.subagent_sessions import SubagentSessions

    try:
        return SubagentSessions(db, None).status(current_user.id, session_id, idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Conversation session not found.") from exc


@coach_router.post("/chat/sessions/{session_id}/subagents/{parent_id}/reconcile")
def reconcile_subagent_catalog(
    session_id: UUID,
    parent_id: UUID,
    payload: SubagentReconcileRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.subagent_recovery import SubagentRecovery

    try:
        result = SubagentRecovery(db).reconcile(
            parent_id, current_user.id, session_id, payload.expected_revision
        )
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        raise HTTPException(
            status_code=404 if code in {"scope_mismatch", "catalog_not_found"} else 409,
            detail=code
            if code
            in {
                "scope_mismatch",
                "catalog_not_found",
                "execution_busy",
                "checkpoint_conflict",
                "unsupported_liveness_evidence",
            }
            else "reconciliation_failed",
        ) from exc


@coach_router.post("/chat/messages", response_model=ChatMessageResponse)
@limiter.limit(settings.rate_limit_chat)
async def send_chat_message(
    request: Request,
    payload: ChatMessageRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    try:
        return await service.handle_chat_message(
            payload.session_id,
            current_user.id,
            payload.message,
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@coach_router.post("/chat/messages/stream")
@limiter.limit(settings.rate_limit_chat)
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
            idempotency_key=payload.idempotency_key,
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
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ] = None,
):
    if idempotency_key and request.idempotency_key and idempotency_key != request.idempotency_key:
        raise HTTPException(
            status_code=409,
            detail="Body and header idempotency keys do not match.",
        )
    request.user_id = current_user.id
    request.idempotency_key = idempotency_key or request.idempotency_key
    return service.record_daily_checkin(request)


@coach_router.post("/workouts/logs", response_model=dict[str, Any])
def record_workout_log(
    request: WorkoutLogRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ] = None,
):
    if idempotency_key and request.idempotency_key and idempotency_key != request.idempotency_key:
        raise HTTPException(
            status_code=409,
            detail="Body and header idempotency keys do not match.",
        )
    request.user_id = current_user.id
    request.idempotency_key = idempotency_key or request.idempotency_key
    log = service.record_workout_log(request)
    return {
        "status": "recorded",
        "workout_log_id": str(log.id),
        "idempotent_replay": bool(getattr(log, "_idempotent_replay", False)),
    }


@coach_router.post("/workouts/logs/{log_id}/corrections", response_model=dict[str, Any])
def correct_workout_log(
    log_id: UUID,
    request: WorkoutCorrectionRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.workout_corrections import correct_workout

    return correct_workout(service, current_user.id, log_id, request)


@coach_router.get("/workouts/logs", response_model=list[dict[str, Any]])
def list_workout_logs(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.workout_corrections import list_workouts

    return list_workouts(service.db, current_user.id, limit)


@coach_router.post("/plans/generate", response_model=PlanResponse)
@limiter.limit(settings.rate_limit_plan)
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


@coach_router.post("/plans/generate/async", response_model=BackgroundTaskResponse, status_code=202)
@limiter.limit(settings.rate_limit_plan)
def enqueue_generate_plan(
    request: Request,
    payload: PlanGenerateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    queue = BackgroundTaskQueue(db)
    task = queue.enqueue(
        user_id=current_user.id,
        task_type="plan.generate",
        payload=payload.model_dump(mode="json", exclude={"user_id"}),
    )
    return _task_to_response(task)


@coach_router.get("/tasks/{task_id}", response_model=BackgroundTaskResponse)
def get_background_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    task = BackgroundTaskQueue(db).get_for_user(task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Background task not found.")
    return _task_to_response(task)


class TaskReconcileRequest(BaseModel):
    expected_attempt: int = Field(ge=1, le=10000)
    confirm_no_retry: Literal[True]


@coach_router.post("/tasks/{task_id}/reconcile")
def reconcile_background_task(
    task_id: UUID,
    body: TaskReconcileRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.task_recovery import TaskRecoveryBusyError, TaskRecoveryService

    try:
        result = TaskRecoveryService(db).reconcile(task_id, current_user.id, body.expected_attempt)
        db.commit()
        return result
    except TaskRecoveryBusyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404 if str(exc) == "Task not found" else 409, detail=str(exc)
        ) from exc


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
    cache_key = f"dashboard:{current_user.id}"
    cached = get_json_cache(cache_key)
    if isinstance(cached, dict):
        return cached
    dashboard = service.dashboard(user_id)
    set_json_cache(cache_key, dashboard, ttl_seconds=30)
    return dashboard


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


def _task_to_response(task: models.BackgroundTask) -> BackgroundTaskResponse:
    return BackgroundTaskResponse(
        task_id=task.id,
        task_type=task.task_type,
        status=task.status,
        attempts=task.attempts,
        max_attempts=task.max_attempts,
        result=task.result_json or {},
        error=task.error,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


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


@coach_router.get("/agent-runs/{run_id}/trace", response_model=dict[str, Any])
def agent_execution_trace(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.execution_trace import recorded_execution_trace

    try:
        return recorded_execution_trace(db, run_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent run not found") from exc


@coach_router.get("/agent-runs/{run_id}/events", response_model=dict[str, Any])
def agent_stream_events(
    run_id: UUID,
    cursor: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fast_api.app.services.stream_event_pages import stream_event_page

    try:
        return stream_event_page(db, run_id, current_user.id, cursor, limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Recorded stream unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Invalid recorded stream position") from exc


@coach_router.get("/agent/tasks", response_model=list[dict[str, Any]])
def list_agent_tasks(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return AgentTaskStateService(db).list_active(current_user.id, limit=limit)


@coach_router.post("/evals/run", response_model=EvalRunResponse)
@limiter.limit(settings.rate_limit_plan)
def run_evals(
    request: Request,
    payload: EvalRunRequest,
    service: CoachAgentService = Depends(get_service),
    current_user: models.User = Depends(get_current_user),
):
    return service.run_evals(payload.suite_name, payload.persist_cases)


@coach_router.post("/evals/run/async", response_model=BackgroundTaskResponse, status_code=202)
@limiter.limit(settings.rate_limit_plan)
def enqueue_run_evals(
    request: Request,
    payload: EvalRunRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    task = BackgroundTaskQueue(db).enqueue(
        user_id=current_user.id,
        task_type="eval.run",
        payload={"suite_name": payload.suite_name, "persist_cases": payload.persist_cases},
    )
    return _task_to_response(task)


@coach_router.post("/plans/review", response_model=dict[str, Any])
def review_plan(
    period_days: int = 7,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate a periodic progress review (7 days = weekly, 30 days = monthly)."""
    reviewer = PlanReviewer(db)
    return reviewer.review(current_user.id, period_days=period_days)
