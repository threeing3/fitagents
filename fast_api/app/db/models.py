import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fast_api.app.core.config import get_settings
from fast_api.app.db.database import Base


settings = get_settings()
EmbeddingColumnType = Vector(settings.vector_dimension) if settings.use_pgvector else JSONB


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="Fitness User")
    avatar_url: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")

    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False)
    sessions: Mapped[list["ConversationSession"]] = relationship(back_populates="user")


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    age: Mapped[int | None] = mapped_column(Integer)
    sex: Mapped[str | None] = mapped_column(String(32))
    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[str] = mapped_column(String(32), default="moderate")
    goal: Mapped[str | None] = mapped_column(String(64))
    experience_level: Mapped[str | None] = mapped_column(String(64))
    workout_frequency: Mapped[int | None] = mapped_column(Integer)
    workout_duration: Mapped[int | None] = mapped_column(Integer)
    dietary_preferences: Mapped[list[str]] = mapped_column(JSONB, default=list)
    allergies: Mapped[list[str]] = mapped_column(JSONB, default=list)
    equipment_available: Mapped[list[str]] = mapped_column(JSONB, default=list)
    injuries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    target_calories: Mapped[int | None] = mapped_column(Integer)
    target_protein_g: Mapped[float | None] = mapped_column(Float)
    target_carbs_g: Mapped[float | None] = mapped_column(Float)
    target_fat_g: Mapped[float | None] = mapped_column(Float)

    user: Mapped[User] = relationship(back_populates="profile")


class BodyMetric(Base, TimestampMixin):
    __tablename__ = "body_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    body_fat_percent: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)


class FitnessGoal(Base, TimestampMixin):
    __tablename__ = "fitness_goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    goal_type: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")


class ConversationSession(Base, TimestampMixin):
    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="AI Coach Session")
    status: Mapped[str] = mapped_column(String(32), default="active")

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session")


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    session: Mapped[ConversationSession] = relationship(back_populates="messages")


class TrainingPlan(Base, TimestampMixin):
    __tablename__ = "training_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    week_start: Mapped[date] = mapped_column(Date, default=date.today)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")


class WorkoutLog(Base, TimestampMixin):
    __tablename__ = "workout_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    workout_name: Mapped[str] = mapped_column(String(200), default="Workout")
    exercises: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    rpe: Mapped[int | None] = mapped_column(Integer)
    completion_rate: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)


class MealLog(Base, TimestampMixin):
    __tablename__ = "meal_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    meals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    calories: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)


class DailyCheckin(Base, TimestampMixin):
    __tablename__ = "daily_checkins"
    __table_args__ = (UniqueConstraint("user_id", "checkin_date", name="uq_daily_checkins_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    checkin_date: Mapped[date] = mapped_column(Date, default=date.today)
    sleep_hours: Mapped[float | None] = mapped_column(Float)
    fatigue: Mapped[int | None] = mapped_column(Integer)
    soreness: Mapped[int | None] = mapped_column(Integer)
    stress: Mapped[int | None] = mapped_column(Integer)
    mood: Mapped[str | None] = mapped_column(String(64))
    nutrition_adherence: Mapped[int | None] = mapped_column(Integer)
    workout_completion: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class LongTermMemory(Base, TimestampMixin):
    __tablename__ = "long_term_memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    memory_type: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    recency_score: Mapped[float] = mapped_column(Float, default=0.5)
    source: Mapped[str] = mapped_column(String(80), default="chat")
    memory_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.75)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_memory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("long_term_memories.id", ondelete="SET NULL"))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingColumnType, nullable=True)


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    content: Mapped[str] = mapped_column(Text)
    strength_score: Mapped[float] = mapped_column(Float, default=0.6)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.75)
    source_type: Mapped[str] = mapped_column(String(80), default="chat")
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RiskNote(Base, TimestampMixin):
    __tablename__ = "risk_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body_part: Mapped[str | None] = mapped_column(String(80), index=True)
    risk_type: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text)
    severity_score: Mapped[float] = mapped_column(Float, default=0.5)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.75)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkoutSession(Base, TimestampMixin):
    __tablename__ = "workout_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("training_plans.id", ondelete="SET NULL"), index=True)
    session_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    session_name: Mapped[str] = mapped_column(String(200), default="Workout")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_score: Mapped[float | None] = mapped_column(Float)
    fatigue_score: Mapped[float | None] = mapped_column(Float)
    mood_score: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)


class ExerciseLog(Base, TimestampMixin):
    __tablename__ = "exercise_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    exercise_name: Mapped[str] = mapped_column(String(160), index=True)
    set_index: Mapped[int] = mapped_column(Integer, default=1)
    reps: Mapped[int | None] = mapped_column(Integer)
    weight: Mapped[float | None] = mapped_column(Float)
    rpe: Mapped[float | None] = mapped_column(Float)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    pain_score: Mapped[float | None] = mapped_column(Float)
    pain_location: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)


class NutritionLog(Base, TimestampMixin):
    __tablename__ = "nutrition_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    meal_type: Mapped[str | None] = mapped_column(String(80), index=True)
    food_name: Mapped[str] = mapped_column(Text)
    estimated_amount: Mapped[str | None] = mapped_column(String(160))
    calories: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    sodium_mg: Mapped[float | None] = mapped_column(Float)
    source_type: Mapped[str] = mapped_column(String(80), default="manual")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.75)
    image_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    corrected_by_user: Mapped[bool] = mapped_column(Boolean, default=False)


class NutritionDailySummary(Base, TimestampMixin):
    __tablename__ = "nutrition_daily_summaries"
    __table_args__ = (UniqueConstraint("user_id", "summary_date", name="uq_nutrition_daily_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    summary_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    total_calories: Mapped[float | None] = mapped_column(Float)
    total_protein_g: Mapped[float | None] = mapped_column(Float)
    total_carbs_g: Mapped[float | None] = mapped_column(Float)
    total_fat_g: Mapped[float | None] = mapped_column(Float)
    total_sodium_mg: Mapped[float | None] = mapped_column(Float)
    target_calories: Mapped[float | None] = mapped_column(Float)
    target_protein_g: Mapped[float | None] = mapped_column(Float)
    adherence_score: Mapped[float | None] = mapped_column(Float)
    summary_text: Mapped[str | None] = mapped_column(Text)


class RecoveryLog(Base, TimestampMixin):
    __tablename__ = "recovery_logs"
    __table_args__ = (UniqueConstraint("user_id", "log_date", name="uq_recovery_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    sleep_hours: Mapped[float | None] = mapped_column(Float)
    sleep_quality_score: Mapped[float | None] = mapped_column(Float)
    fatigue_score: Mapped[float | None] = mapped_column(Float)
    soreness_score: Mapped[float | None] = mapped_column(Float)
    stress_score: Mapped[float | None] = mapped_column(Float)
    resting_hr: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class SymptomLog(Base, TimestampMixin):
    __tablename__ = "symptom_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symptom_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    body_part: Mapped[str | None] = mapped_column(String(80), index=True)
    symptom_type: Mapped[str] = mapped_column(String(80), index=True)
    severity_score: Mapped[float | None] = mapped_column(Float)
    trigger_context: Mapped[str | None] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class MemoryBlock(Base, TimestampMixin):
    __tablename__ = "memory_blocks"
    __table_args__ = (UniqueConstraint("user_id", "block_type", name="uq_memory_blocks_user_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    block_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    token_budget: Mapped[int] = mapped_column(Integer, default=240)
    importance_score: Mapped[float] = mapped_column(Float, default=0.7)
    version: Mapped[int] = mapped_column(Integer, default=1)


class MemoryCatalog(Base, TimestampMixin):
    __tablename__ = "memory_catalog"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text)
    time_range_start: Mapped[date | None] = mapped_column(Date)
    time_range_end: Mapped[date | None] = mapped_column(Date)
    importance_score: Mapped[float] = mapped_column(Float, default=0.6)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    query_hints: Mapped[list[str]] = mapped_column(JSONB, default=list)
    child_table: Mapped[str | None] = mapped_column(String(120))
    child_filter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AgentDecision(Base, TimestampMixin):
    __tablename__ = "agent_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    decision_type: Mapped[str] = mapped_column(String(80), index=True)
    input_summary: Mapped[str] = mapped_column(Text)
    context_used: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decision_result: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.75)
    accepted_by_user: Mapped[bool | None] = mapped_column(Boolean)


class MemoryExport(Base, TimestampMixin):
    __tablename__ = "memory_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    export_type: Mapped[str] = mapped_column(String(80), default="compact")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    included_sections: Mapped[list[str]] = mapped_column(JSONB, default=list)
    schema_version: Mapped[str] = mapped_column(String(40), default="2026-05-24")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_sessions.id", ondelete="SET NULL"), index=True)
    run_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    log_path: Mapped[str | None] = mapped_column(Text)


class AgentTaskState(Base, TimestampMixin):
    __tablename__ = "agent_task_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    phase: Mapped[str] = mapped_column(String(80), default="observe")
    current_step: Mapped[str | None] = mapped_column(Text)
    success_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    next_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AgentTaskEvent(Base, TimestampMixin):
    __tablename__ = "agent_task_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_task_states.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AgentRunReplay(Base, TimestampMixin):
    __tablename__ = "agent_run_replays"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_sessions.id", ondelete="SET NULL"), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    tool_plan_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    response_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    replay_status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)


class ToolCall(Base, TimestampMixin):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="success")


class PromptVersion(Base, TimestampMixin):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_versions_name_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class EvalCase(Base, TimestampMixin):
    __tablename__ = "eval_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    category: Mapped[str] = mapped_column(String(80))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Extended fields for LLM output quality evaluation
    eval_dimensions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    expected_scores: Mapped[dict[str, float] | None] = mapped_column(JSONB, nullable=True)
    ground_truth: Mapped[str | None] = mapped_column(Text, nullable=True)
    must_include: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)


class EvalRun(Base, TimestampMixin):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suite_name: Mapped[str] = mapped_column(String(160), index=True)
    model_used: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    total_cases: Mapped[int] = mapped_column(default=0)
    passed_count: Mapped[int] = mapped_column(default=0)
    average_score: Mapped[float] = mapped_column(Float, default=0.0)
    dimension_averages: Mapped[dict[str, float] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalResult(Base, TimestampMixin):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    eval_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_cases.id", ondelete="SET NULL"), index=True)
    eval_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="SET NULL"), index=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Extended evaluation result fields
    dimension_scores_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    judge_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_checks_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    input_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class FoodItem(Base, TimestampMixin):
    __tablename__ = "food_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(240))
    category: Mapped[str | None] = mapped_column(String(160))
    nutrition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingColumnType, nullable=True)


class ExplanationKnowledge(Base, TimestampMixin):
    __tablename__ = "explanation_knowledge"
    __table_args__ = (UniqueConstraint("knowledge_id", name="uq_explanation_knowledge_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_id: Mapped[str] = mapped_column(String(120), index=True)
    topic: Mapped[str] = mapped_column(String(160), index=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(160), default="seed")
    safety_level: Mapped[str] = mapped_column(String(40), default="general")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingColumnType, nullable=True)


class FitnessDecisionRule(Base, TimestampMixin):
    __tablename__ = "fitness_decision_rules"
    __table_args__ = (UniqueConstraint("rule_id", name="uq_fitness_decision_rule_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[str] = mapped_column(String(120), index=True)
    rule_type: Mapped[str] = mapped_column(String(80), index=True)
    intent: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text)
    condition_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    action_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    safety_level: Mapped[str] = mapped_column(String(40), default="general")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    version: Mapped[str] = mapped_column(String(40), default="v1")


class PlanTemplate(Base, TimestampMixin):
    __tablename__ = "plan_templates"
    __table_args__ = (UniqueConstraint("template_id", name="uq_plan_template_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[str] = mapped_column(String(120), index=True)
    template_type: Mapped[str] = mapped_column(String(80), index=True)
    goal: Mapped[str | None] = mapped_column(String(80), index=True)
    level: Mapped[str | None] = mapped_column(String(80), index=True)
    days_per_week: Mapped[int | None] = mapped_column(Integer, index=True)
    equipment: Mapped[list[str]] = mapped_column(JSONB, default=list)
    template_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    version: Mapped[str] = mapped_column(String(40), default="v1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class CoachingCase(Base, TimestampMixin):
    __tablename__ = "coaching_cases"
    __table_args__ = (UniqueConstraint("case_id", name="uq_coaching_case_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[str] = mapped_column(String(120), index=True)
    case_type: Mapped[str] = mapped_column(String(80), default="general", index=True)
    title: Mapped[str] = mapped_column(String(200))
    profile_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario: Mapped[str] = mapped_column(Text)
    situation: Mapped[str] = mapped_column(Text, default="")
    approach: Mapped[str] = mapped_column(Text)
    coach_response_pattern: Mapped[str] = mapped_column(Text, default="")
    key_principles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(160), default="seed")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingColumnType, nullable=True)


class UserFeedback(Base, TimestampMixin):
    __tablename__ = "user_feedback"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_user_feedback_message"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation_sessions.id", ondelete="SET NULL"), index=True, nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(64), default=None)
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    coach_reply_snapshot: Mapped[str | None] = mapped_column(Text, default=None)
    user_message_snapshot: Mapped[str | None] = mapped_column(Text, default=None)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SemanticCache(Base, TimestampMixin):
    """pgvector-backed cache for semantically similar LLM responses."""

    __tablename__ = "semantic_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    system_prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingColumnType, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), default="unknown")
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    last_hit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=86400)
