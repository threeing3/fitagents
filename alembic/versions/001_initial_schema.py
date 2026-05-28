"""Initial schema — all core tables for AI Fitness Coach.

Revision ID: 001
Revises: None
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Users & Profiles ----
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False, default="Fitness User"),
        sa.Column("email", sa.String(255), unique=True),
        sa.Column("password_hash", sa.String(255), default="migrate_required"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("age", sa.Integer()),
        sa.Column("sex", sa.String(20)),
        sa.Column("height_cm", sa.Float()),
        sa.Column("weight_kg", sa.Float()),
        sa.Column("activity_level", sa.String(40)),
        sa.Column("goal", sa.String(40)),
        sa.Column("experience_level", sa.String(40)),
        sa.Column("workout_frequency", sa.Integer()),
        sa.Column("workout_duration", sa.Integer()),
        sa.Column("dietary_preferences", sa.JSON(), default=list),
        sa.Column("allergies", sa.JSON(), default=list),
        sa.Column("equipment_available", sa.JSON(), default=list),
        sa.Column("injuries", sa.JSON(), default=list),
        sa.Column("target_calories", sa.Float()),
        sa.Column("target_protein_g", sa.Float()),
        sa.Column("target_carbs_g", sa.Float()),
        sa.Column("target_fat_g", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    op.create_table(
        "body_metrics",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("weight_kg", sa.Float()),
        sa.Column("body_fat_pct", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "fitness_goals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal_type", sa.String(40)),
        sa.Column("target_value", sa.Float()),
        sa.Column("deadline", sa.Date()),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---- Conversations ----
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), default="Fitness Chat"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---- Training Plans ----
    op.create_table(
        "training_plans",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("week_start", sa.Date()),
        sa.Column("plan_json", sa.JSON(), default=dict),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_training_plans_user_status", "training_plans", ["user_id", "status"])

    # ---- Workouts ----
    op.create_table(
        "workout_logs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True)),
        sa.Column("workout_name", sa.String(200)),
        sa.Column("exercises", sa.JSON(), default=list),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("rpe", sa.Integer()),
        sa.Column("completion_rate", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "workout_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_date", sa.Date()),
        sa.Column("session_name", sa.String(200)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completion_score", sa.Float()),
        sa.Column("fatigue_score", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "exercise_logs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exercise_name", sa.String(200)),
        sa.Column("set_index", sa.Integer(), default=1),
        sa.Column("reps", sa.Integer()),
        sa.Column("weight", sa.Float()),
        sa.Column("rpe", sa.Integer()),
        sa.Column("completed", sa.Boolean(), default=True),
        sa.Column("pain_score", sa.Integer()),
        sa.Column("pain_location", sa.String(80)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_exercise_logs_user_exercise", "exercise_logs", ["user_id", "exercise_name"])

    # ---- Nutrition ----
    op.create_table(
        "meal_logs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("meal_type", sa.String(40)),
        sa.Column("food_items", sa.JSON(), default=list),
        sa.Column("total_calories", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "nutrition_logs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("food_name", sa.String(200)),
        sa.Column("estimated_amount", sa.String(100)),
        sa.Column("calories", sa.Float()),
        sa.Column("protein_g", sa.Float()),
        sa.Column("carbs_g", sa.Float()),
        sa.Column("fat_g", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("source_type", sa.String(40), default="manual"),
        sa.Column("corrected_by_user", sa.Boolean(), default=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "nutrition_daily_summaries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("total_calories", sa.Float(), default=0),
        sa.Column("total_protein_g", sa.Float(), default=0),
        sa.Column("total_carbs_g", sa.Float(), default=0),
        sa.Column("total_fat_g", sa.Float(), default=0),
        sa.Column("summary_text", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_nutrition_daily_user_date", "nutrition_daily_summaries", ["user_id", "summary_date"], unique=True)

    op.create_table(
        "food_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("nutrition_log_id", sa.UUID(), sa.ForeignKey("nutrition_logs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("amount", sa.String(100)),
        sa.Column("calories", sa.Float()),
        sa.Column("protein_g", sa.Float()),
        sa.Column("carbs_g", sa.Float()),
        sa.Column("fat_g", sa.Float()),
        sa.Column("confidence", sa.Float()),
    )

    # ---- Check-ins & Recovery ----
    op.create_table(
        "daily_checkins",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checkin_date", sa.Date()),
        sa.Column("sleep_hours", sa.Float()),
        sa.Column("fatigue", sa.Integer()),
        sa.Column("soreness", sa.Integer()),
        sa.Column("stress", sa.Integer()),
        sa.Column("workout_completion", sa.Float()),
        sa.Column("nutrition_adherence", sa.Float()),
        sa.Column("weight_change_kg", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "recovery_logs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("log_date", sa.Date()),
        sa.Column("sleep_hours", sa.Float()),
        sa.Column("fatigue_score", sa.Integer()),
        sa.Column("soreness_score", sa.Integer()),
        sa.Column("stress_score", sa.Integer()),
        sa.Column("motivation_score", sa.Integer()),
        sa.Column("consecutive_bad_sleep_days", sa.Integer(), default=0),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "symptom_logs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symptom_date", sa.Date()),
        sa.Column("symptom_name", sa.String(120)),
        sa.Column("severity", sa.Integer()),
        sa.Column("body_location", sa.String(80)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_symptom_logs_user_date", "symptom_logs", ["user_id", "symptom_date"])

    # ---- Memory System ----
    op.create_table(
        "long_term_memories",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_type", sa.String(60)),
        sa.Column("category", sa.String(80)),
        sa.Column("content", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("source", sa.String(60)),
        sa.Column("importance", sa.Float(), default=0.5),
        sa.Column("recency_score", sa.Float(), default=0.5),
        sa.Column("confidence", sa.Float(), default=0.75),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("memory_metadata", sa.JSON(), default=dict),
        sa.Column("parent_memory_id", sa.UUID()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.Column("access_count", sa.Integer(), default=0),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_long_term_memories_user_status", "long_term_memories", ["user_id", "status"])
    op.create_index("ix_long_term_memories_type_status", "long_term_memories", ["memory_type", "status"])
    op.create_index("ix_long_term_memories_user_category", "long_term_memories", ["user_id", "category"])

    op.create_table(
        "memory_blocks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_type", sa.String(60)),
        sa.Column("block_key", sa.String(200)),
        sa.Column("memory_ids", sa.JSON(), default=list),
        sa.Column("summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_blocks_user_type", "memory_blocks", ["user_id", "block_type"])

    op.create_table(
        "memory_catalog",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(80)),
        sa.Column("entity_count", sa.Integer(), default=0),
        sa.Column("last_updated", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_catalog_user_category", "memory_catalog", ["user_id", "category"])

    op.create_table(
        "memory_exports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("export_type", sa.String(40)),
        sa.Column("format", sa.String(20)),
        sa.Column("file_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---- Agent & Decisions ----
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_type", sa.String(40)),
        sa.Column("status", sa.String(32), default="completed"),
        sa.Column("nodes", sa.JSON(), default=list),
        sa.Column("summary", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("log_path", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("agent_run_id", sa.UUID(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(120)),
        sa.Column("output_json", sa.JSON(), default=dict),
        sa.Column("status", sa.String(32), default="success"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_type", sa.String(60)),
        sa.Column("input_summary", sa.Text()),
        sa.Column("context_used", sa.JSON(), default=dict),
        sa.Column("decision_result", sa.String(200)),
        sa.Column("reason", sa.Text()),
        sa.Column("confidence_score", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_decisions_user_type", "agent_decisions", ["user_id", "decision_type"])

    # ---- Risks ----
    op.create_table(
        "risk_notes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body_part", sa.String(80)),
        sa.Column("risk_type", sa.String(60)),
        sa.Column("description", sa.Text()),
        sa.Column("severity_score", sa.Float(), default=0.5),
        sa.Column("confidence_score", sa.Float(), default=0.75),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_risk_notes_user_status", "risk_notes", ["user_id", "status"])

    # ---- User Preferences ----
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("language", sa.String(20), default="zh"),
        sa.Column("coach_style", sa.String(40), default="balanced"),
        sa.Column("notification_enabled", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    # ---- Prompt Versions ----
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("prompt_id", sa.String(120), nullable=False),
        sa.Column("version", sa.String(20)),
        sa.Column("content", sa.Text()),
        sa.Column("model_used", sa.String(80)),
        sa.Column("performance_score", sa.Float()),
        sa.Column("is_active", sa.Boolean(), default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_prompt_versions_prompt_id", "prompt_versions", ["prompt_id"])

    # ---- Eval Framework ----
    op.create_table(
        "eval_cases",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(200), unique=True),
        sa.Column("category", sa.String(80)),
        sa.Column("input_json", sa.JSON(), default=dict),
        sa.Column("expected_json", sa.JSON(), default=dict),
        sa.Column("eval_dimensions", sa.JSON(), default=list),
        sa.Column("expected_scores", sa.JSON(), default=dict),
        sa.Column("ground_truth", sa.Text()),
        sa.Column("must_include", sa.JSON(), default=list),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("suite_name", sa.String(200)),
        sa.Column("model_used", sa.String(80)),
        sa.Column("prompt_version", sa.String(20)),
        sa.Column("total_cases", sa.Integer(), default=0),
        sa.Column("passed_count", sa.Integer(), default=0),
        sa.Column("average_score", sa.Float()),
        sa.Column("dimension_averages", sa.JSON(), default=dict),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("eval_case_id", sa.UUID(), sa.ForeignKey("eval_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("eval_run_id", sa.UUID(), sa.ForeignKey("eval_runs.id", ondelete="CASCADE")),
        sa.Column("score", sa.Float()),
        sa.Column("passed", sa.Boolean(), default=False),
        sa.Column("details", sa.JSON(), default=dict),
        sa.Column("dimension_scores_json", sa.JSON(), default=dict),
        sa.Column("judge_model", sa.String(80)),
        sa.Column("judge_raw_response", sa.Text()),
        sa.Column("rule_checks_json", sa.JSON(), default=dict),
        sa.Column("input_message", sa.Text()),
        sa.Column("response_text", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ---- Knowledge Base ----
    op.create_table(
        "explanation_knowledge",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("topic", sa.String(200)),
        sa.Column("title", sa.String(300)),
        sa.Column("content", sa.Text()),
        sa.Column("keywords", sa.JSON(), default=list),
        sa.Column("intent_tags", sa.JSON(), default=list),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_explanation_knowledge_topic", "explanation_knowledge", ["topic"])

    op.create_table(
        "fitness_decision_rules",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("rule_id", sa.String(120), unique=True),
        sa.Column("title", sa.String(300)),
        sa.Column("description", sa.Text()),
        sa.Column("condition", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("intent", sa.String(80)),
        sa.Column("enabled", sa.Boolean(), default=True),
        sa.Column("version", sa.String(40), default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_fitness_decision_rules_intent_enabled", "fitness_decision_rules", ["intent", "enabled"])

    op.create_table(
        "plan_templates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("template_id", sa.String(120), unique=True),
        sa.Column("name", sa.String(200)),
        sa.Column("goal", sa.String(80)),
        sa.Column("level", sa.String(40)),
        sa.Column("description", sa.Text()),
        sa.Column("plan_schema", sa.JSON(), default=dict),
        sa.Column("version", sa.String(40), default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_plan_templates_goal_level", "plan_templates", ["goal", "level"])

    op.create_table(
        "coaching_cases",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("case_id", sa.String(120), unique=True),
        sa.Column("title", sa.String(300)),
        sa.Column("case_type", sa.String(80)),
        sa.Column("scenario", sa.Text()),
        sa.Column("approach", sa.Text()),
        sa.Column("tags", sa.JSON(), default=list),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_coaching_cases_case_type", "coaching_cases", ["case_type"])


def downgrade() -> None:
    op.drop_table("coaching_cases")
    op.drop_table("plan_templates")
    op.drop_table("fitness_decision_rules")
    op.drop_table("explanation_knowledge")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
    op.drop_table("prompt_versions")
    op.drop_table("user_preferences")
    op.drop_table("risk_notes")
    op.drop_table("agent_decisions")
    op.drop_table("tool_calls")
    op.drop_table("agent_runs")
    op.drop_table("memory_exports")
    op.drop_table("memory_catalog")
    op.drop_table("memory_blocks")
    op.drop_table("long_term_memories")
    op.drop_table("symptom_logs")
    op.drop_table("recovery_logs")
    op.drop_table("daily_checkins")
    op.drop_table("food_items")
    op.drop_table("nutrition_daily_summaries")
    op.drop_table("nutrition_logs")
    op.drop_table("meal_logs")
    op.drop_table("exercise_logs")
    op.drop_table("workout_sessions")
    op.drop_table("workout_logs")
    op.drop_table("training_plans")
    op.drop_table("chat_messages")
    op.drop_table("conversation_sessions")
    op.drop_table("fitness_goals")
    op.drop_table("body_metrics")
    op.drop_table("user_profiles")
    op.drop_table("users")
