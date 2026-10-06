"""Freeze missing domain columns; preserve legacy records without activating them.

This repair was discovered by booting the full migration history, not create_all.
Only explicit legacy-to-domain mappings are copied. Untranslatable rules/templates
remain disabled. Downgrade deliberately retains data and the compatible schema.
"""

from alembic import op

revision = "016_domain_schema_alignment"
down_revision = "015_pending_approvals"
branch_labels = None
depends_on = None

# Frozen definitions: never import evolving ORM metadata into a historical migration.
COLUMNS = {
    "coaching_cases": {
        "source": "varchar(160) NOT NULL DEFAULT 'legacy'",
        "status": "varchar(32) NOT NULL DEFAULT 'legacy_unreviewed'",
    },
    "explanation_knowledge": {
        "knowledge_id": "varchar(120)",
        "safety_level": "varchar(40) NOT NULL DEFAULT 'general'",
        "source": "varchar(160) NOT NULL DEFAULT 'legacy'",
        "status": "varchar(32) NOT NULL DEFAULT 'legacy_unreviewed'",
        "tags": "jsonb NOT NULL DEFAULT '[]'::jsonb",
    },
    "fitness_decision_rules": {
        "action_json": "jsonb NOT NULL DEFAULT '{}'::jsonb",
        "condition_json": "jsonb NOT NULL DEFAULT '{}'::jsonb",
        "priority": "integer NOT NULL DEFAULT 50",
        "rule_type": "varchar(80) NOT NULL DEFAULT 'legacy_unreviewed'",
        "safety_level": "varchar(40) NOT NULL DEFAULT 'general'",
    },
    "food_items": {
        "brand": "varchar(240)",
        "category": "varchar(160)",
        "created_at": "timestamptz NOT NULL DEFAULT now()",
        "description": "text NOT NULL DEFAULT ''",
        "external_id": "varchar(120)",
        "nutrition": "jsonb NOT NULL DEFAULT '{}'::jsonb",
    },
    "plan_templates": {
        "days_per_week": "integer",
        "equipment": "jsonb NOT NULL DEFAULT '[]'::jsonb",
        "template_json": "jsonb NOT NULL DEFAULT '{}'::jsonb",
        "template_type": "varchar(80) NOT NULL DEFAULT 'legacy_unreviewed'",
    },
    "prompt_versions": {"name": "varchar(120)"},
    "users": {"timezone": "varchar(64) NOT NULL DEFAULT 'Asia/Shanghai'"},
    "agent_decisions": {"accepted_by_user": "boolean"},
    "body_metrics": {"body_fat_percent": "double precision"},
    "conversation_sessions": {"status": "varchar(32) NOT NULL DEFAULT 'active'"},
    "daily_checkins": {"mood": "varchar(64)"},
    "fitness_goals": {"target": "text"},
    "meal_logs": {
        "calories": "double precision",
        "carbs_g": "double precision",
        "fat_g": "double precision",
        "meals": "jsonb NOT NULL DEFAULT '[]'::jsonb",
        "protein_g": "double precision",
    },
    "memory_blocks": {
        "content": "text",
        "importance_score": "double precision NOT NULL DEFAULT 0.7",
        "title": "varchar(160)",
        "token_budget": "integer NOT NULL DEFAULT 240",
        "version": "integer NOT NULL DEFAULT 1",
    },
    "memory_catalog": {
        "child_filter": "jsonb NOT NULL DEFAULT '{}'::jsonb",
        "child_table": "varchar(120)",
        "importance_score": "double precision NOT NULL DEFAULT 0.6",
        "last_updated_at": "timestamptz",
        "query_hints": "jsonb NOT NULL DEFAULT '[]'::jsonb",
        "record_count": "integer",
        "summary": "text NOT NULL DEFAULT ''",
        "time_range_end": "date",
        "time_range_start": "date",
        "title": "varchar(160)",
    },
    "memory_exports": {
        "completed_at": "timestamptz",
        "encrypted": "boolean NOT NULL DEFAULT false",
        "included_sections": "jsonb NOT NULL DEFAULT '[]'::jsonb",
        "schema_version": "varchar(40) NOT NULL DEFAULT 'legacy'",
        "status": "varchar(32) NOT NULL DEFAULT 'legacy_unknown'",
    },
    "nutrition_daily_summaries": {
        "adherence_score": "double precision",
        "target_calories": "double precision",
        "target_protein_g": "double precision",
        "total_sodium_mg": "double precision",
    },
    "nutrition_logs": {
        "confidence_score": "double precision",
        "image_id": "uuid",
        "log_date": "date",
        "meal_type": "varchar(80)",
        "sodium_mg": "double precision",
    },
    "recovery_logs": {"resting_hr": "integer", "sleep_quality_score": "double precision"},
    "risk_notes": {"first_seen_at": "timestamptz", "valid_until": "timestamptz"},
    "symptom_logs": {
        "action_taken": "text",
        "body_part": "varchar(80)",
        "severity_score": "double precision",
        "status": "varchar(32) NOT NULL DEFAULT 'active'",
        "symptom_type": "varchar(80)",
        "trigger_context": "text",
    },
    "user_preferences": {
        "category": "varchar(80) NOT NULL DEFAULT 'legacy_settings'",
        "confidence_score": "double precision NOT NULL DEFAULT 0.75",
        "content": "text",
        "id": "uuid NOT NULL DEFAULT gen_random_uuid()",
        "source_id": "uuid",
        "source_type": "varchar(80) NOT NULL DEFAULT 'legacy'",
        "strength_score": "double precision NOT NULL DEFAULT 0.6",
        "valid_from": "timestamptz",
        "valid_until": "timestamptz",
    },
    "chat_messages": {"message_metadata": "jsonb NOT NULL DEFAULT '{}'::jsonb"},
    "workout_sessions": {
        "ended_at": "timestamptz",
        "mood_score": "double precision",
        "plan_id": "uuid",
    },
    "eval_results": {"agent_run_id": "uuid"},
    "exercise_logs": {"exercise_id": "uuid"},
    "tool_calls": {
        "input_json": "jsonb NOT NULL DEFAULT '{}'::jsonb",
        "latency_ms": "integer NOT NULL DEFAULT 0",
    },
}
TIMESTAMP_TABLES = (
    "coaching_cases",
    "eval_cases",
    "eval_runs",
    "explanation_knowledge",
    "fitness_decision_rules",
    "food_items",
    "plan_templates",
    "prompt_versions",
    "agent_decisions",
    "body_metrics",
    "daily_checkins",
    "fitness_goals",
    "meal_logs",
    "memory_blocks",
    "memory_catalog",
    "memory_exports",
    "nutrition_daily_summaries",
    "nutrition_logs",
    "recovery_logs",
    "risk_notes",
    "symptom_logs",
    "workout_logs",
    "agent_runs",
    "chat_messages",
    "workout_sessions",
    "eval_results",
    "exercise_logs",
    "tool_calls",
)


def upgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    original = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in set(COLUMNS) | set(TIMESTAMP_TABLES)
    }
    for table, columns in COLUMNS.items():
        for name, definition in columns.items():
            op.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{name}" {definition}')
    for table in TIMESTAMP_TABLES:
        if "updated_at" not in original[table]:
            op.execute(f'ALTER TABLE "{table}" ADD COLUMN updated_at timestamptz')
            op.execute(f'UPDATE "{table}" SET updated_at = COALESCE(created_at, now())')
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN updated_at SET DEFAULT now(), ALTER COLUMN updated_at SET NOT NULL'
            )

    # Respect the same optional vector setting as the initial migration.
    from fast_api.app.core.config import get_settings

    settings = get_settings()
    embedding_type = (
        f"vector({int(settings.vector_dimension)})" if settings.use_pgvector else "jsonb"
    )
    op.execute(f"ALTER TABLE food_items ADD COLUMN IF NOT EXISTS embedding {embedding_type}")

    # Each backfill runs only when introducing its destination column, never
    # overwrites a current record on re-entry or a previously create_all database.
    mappings = (
        ("explanation_knowledge", "knowledge_id", "'legacy_' || id::text"),
        ("prompt_versions", "name", "COALESCE(prompt_id, 'legacy_' || id::text)"),
        ("fitness_goals", "target", "COALESCE(target_value::text, '')"),
        ("body_metrics", "body_fat_percent", "body_fat_pct"),
        ("meal_logs", "calories", "total_calories"),
        ("meal_logs", "meals", "COALESCE(food_items::jsonb, '[]'::jsonb)"),
        ("memory_blocks", "content", "COALESCE(summary, '')"),
        ("memory_blocks", "title", "COALESCE(block_key, block_type, 'Legacy memory')"),
        ("memory_catalog", "title", "COALESCE(category, 'Legacy catalog')"),
        ("memory_catalog", "record_count", "COALESCE(entity_count, 0)"),
        ("memory_catalog", "last_updated_at", "COALESCE(last_updated, created_at, now())"),
        ("nutrition_logs", "confidence_score", "COALESCE(confidence, 0.75)"),
        ("nutrition_logs", "log_date", "COALESCE(logged_at::date, created_at::date, CURRENT_DATE)"),
        ("risk_notes", "first_seen_at", "COALESCE(created_at, now())"),
        ("symptom_logs", "body_part", "body_location"),
        ("symptom_logs", "severity_score", "severity"),
        ("symptom_logs", "symptom_type", "COALESCE(symptom_name, 'legacy_unspecified')"),
        (
            "user_preferences",
            "content",
            "jsonb_build_object('language', language, 'coach_style', coach_style, 'notification_enabled', notification_enabled)::text",
        ),
    )
    required = {
        "knowledge_id",
        "name",
        "target",
        "content",
        "title",
        "record_count",
        "last_updated_at",
        "confidence_score",
        "log_date",
        "first_seen_at",
        "symptom_type",
    }
    for table, column, expression in mappings:
        if column not in original[table]:
            op.execute(f'UPDATE "{table}" SET "{column}" = {expression}')
            if column in required:
                op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET NOT NULL')

    if "rule_type" not in original["fitness_decision_rules"]:
        op.execute("UPDATE fitness_decision_rules SET enabled = false")
    if "template_type" not in original["plan_templates"]:
        op.execute("UPDATE plan_templates SET enabled = false, status = 'legacy_unreviewed'")
        op.execute(
            "UPDATE plan_templates SET template_json = COALESCE(plan_schema::jsonb, '{}'::jsonb)"
        )
    # The old food table holds private meal entries; do not copy them to the
    # public catalog description/nutrition or turn them into searchable items.
    for table, column in (("food_items", "nutrition_log_id"), ("prompt_versions", "prompt_id")):
        if column in original[table]:
            op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP NOT NULL')
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_explanation_knowledge_id ON explanation_knowledge(knowledge_id)"
    )

    # A former one-row-per-user settings table now holds many preference facts.
    # Preserve every legacy field and row; change only the obsolete key constraint.
    primary = inspector.get_pk_constraint("user_preferences")
    if primary.get("constrained_columns") == ["user_id"]:
        constraint = primary["name"].replace('"', '""')
        op.execute(f'ALTER TABLE user_preferences DROP CONSTRAINT "{constraint}"')
        op.execute("ALTER TABLE user_preferences ADD PRIMARY KEY (id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_preferences_user_id ON user_preferences(user_id)"
    )


def downgrade() -> None:
    # Removing fields would lose domain state. Rolling back code must not erase it.
    pass
