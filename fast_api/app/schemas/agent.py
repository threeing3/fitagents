from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class UserProfileInput(BaseModel):
    user_id: UUID | None = None
    display_name: str = "Fitness User"
    age: int | None = None
    sex: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    activity_level: str = "moderate"
    goal: str | None = None
    experience_level: str | None = None
    workout_frequency: int | None = 3
    workout_duration: int | None = 60
    dietary_preferences: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    equipment_available: list[str] = Field(default_factory=list)
    injuries: list[str] = Field(default_factory=list)


class ChatSessionCreate(BaseModel):
    user_id: UUID | None = None
    display_name: str = "Fitness User"
    title: str = "AI Coach Session"


class ChatSessionResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    title: str
    created_at: datetime


class ChatHistoryMessageResponse(BaseModel):
    id: UUID
    session_id: UUID
    user_id: UUID
    role: str
    content: str
    created_at: datetime


class ChatMessageRequest(BaseModel):
    session_id: UUID
    user_id: UUID | None = None
    message: str


class ChatMessageResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    assistant_message: str
    agent_run_id: UUID
    onboarding_complete: bool
    missing_slots: list[str]
    memories_written: list[str]
    tool_calls: list[dict[str, Any]]
    state_updates: dict[str, Any]


class DailyCheckinRequest(BaseModel):
    user_id: UUID | None = None
    checkin_date: date | None = None
    sleep_hours: float | None = None
    fatigue: int | None = Field(default=None, ge=1, le=10)
    soreness: int | None = Field(default=None, ge=1, le=10)
    stress: int | None = Field(default=None, ge=1, le=10)
    mood: str | None = None
    nutrition_adherence: int | None = Field(default=None, ge=0, le=100)
    workout_completion: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = None


class WorkoutLogRequest(BaseModel):
    user_id: UUID | None = None
    performed_at: datetime | None = None
    workout_name: str = "Workout"
    exercises: list[dict[str, Any]] = Field(default_factory=list)
    duration_minutes: int | None = None
    rpe: int | None = Field(default=None, ge=1, le=10)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


class PlanGenerateRequest(BaseModel):
    user_id: UUID | None = None
    force: bool = False
    plan_days: int = Field(default=7, ge=1, le=14)


class PlanAdjustRequest(BaseModel):
    user_id: UUID | None = None
    reason: str | None = None


class PlanResponse(BaseModel):
    user_id: UUID
    plan_id: UUID
    status: str
    plan: dict[str, Any]
    rationale: str


class DashboardResponse(BaseModel):
    user_id: UUID
    profile_complete: bool
    profile: dict[str, Any]
    missing_slots: list[str]
    today_plan: dict[str, Any]
    latest_checkin: dict[str, Any] | None
    recent_memories: list[dict[str, Any]]
    progress: dict[str, Any]
    coach_suggestions: list[str]


class AgentRunResponse(BaseModel):
    id: UUID
    user_id: UUID
    session_id: UUID | None
    run_type: str
    status: str
    nodes: list[dict[str, Any]]
    summary: str | None
    error: str | None
    log_path: str | None = None
    tool_calls: list[dict[str, Any]]
    started_at: datetime
    completed_at: datetime | None


class EvalRunRequest(BaseModel):
    suite_name: str = "mvp"
    persist_cases: bool = True


class EvalRunResponse(BaseModel):
    suite_name: str
    total: int
    passed: int
    score: float
    log_path: str
    results: list[dict[str, Any]]


class MemoryItemCreate(BaseModel):
    user_id: UUID | None = None
    memory_type: str = "episodic"
    category: str | None = None
    content: str
    summary: str | None = None
    importance_score: float = Field(default=0.6, ge=0, le=1)
    confidence_score: float = Field(default=0.75, ge=0, le=1)
    source_type: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryItemResponse(BaseModel):
    id: UUID
    user_id: UUID
    memory_type: str
    category: str | None
    content: str
    summary: str | None
    importance_score: float
    confidence_score: float
    source_type: str
    metadata: dict[str, Any]
    created_at: datetime


class MemorySearchRequest(BaseModel):
    user_id: UUID | None = None
    query: str
    category: str | None = None
    memory_type: str | None = None
    top_k: int = Field(default=6, ge=1, le=20)


class ContextBuildRequest(BaseModel):
    user_id: UUID | None = None
    user_message: str
    intent: str | None = None


class AgentDecisionCreate(BaseModel):
    user_id: UUID | None = None
    decision_type: str
    input_summary: str
    context_used: dict[str, Any] = Field(default_factory=dict)
    decision_result: str
    reason: str
    confidence_score: float = Field(default=0.75, ge=0, le=1)


class AgentDecisionResponse(BaseModel):
    id: UUID
    user_id: UUID
    decision_type: str
    input_summary: str
    context_used: dict[str, Any]
    decision_result: str
    reason: str
    confidence_score: float
    accepted_by_user: bool | None
    created_at: datetime


# ---- User Feedback ----

class FeedbackSubmitRequest(BaseModel):
    message_id: UUID
    rating: int = Field(ge=1, le=5, description="1-5 star rating")
    category: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    id: UUID
    user_id: UUID
    session_id: UUID | None
    message_id: UUID | None
    rating: int
    category: str | None
    comment: str | None
    created_at: datetime


class FeedbackStatsResponse(BaseModel):
    total_feedback: int
    average_rating: float
    rating_distribution: dict[int, int]
    top_categories: list[dict[str, Any]]
    recent_feedback: list[FeedbackResponse]
