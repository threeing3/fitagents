"""Align legacy JSON with domain JSONB equality without removing domain state.

Reject duplicate object keys rather than silently dropping them. Deployment
requires a verified backup and review; startup is not deployment authorization.
"""

import json

from sqlalchemy import inspect, text

from alembic import op

revision = "017_domain_jsonb_alignment"
down_revision = "016_domain_schema_alignment"
branch_labels = None
depends_on = None

JSON_COLUMNS = {
    "coaching_cases": ("tags",),
    "eval_cases": (
        "input_json",
        "expected_json",
        "eval_dimensions",
        "expected_scores",
        "must_include",
    ),
    "eval_runs": ("dimension_averages",),
    "agent_decisions": ("context_used",),
    "long_term_memories": ("memory_metadata",),
    "training_plans": ("plan_json",),
    "user_profiles": ("dietary_preferences", "allergies", "equipment_available", "injuries"),
    "workout_logs": ("exercises",),
    "agent_runs": ("nodes",),
    "eval_results": ("details", "dimension_scores_json", "rule_checks_json"),
    "tool_calls": ("output_json",),
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate legacy JSON object key; manual reconciliation required")
        result[key] = value
    return result


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    for table, names in JSON_COLUMNS.items():
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        for name in names:
            if str(columns[name]["type"]).upper() != "JSON":
                continue
            rows = connection.execute(
                text(f'SELECT "{name}"::text FROM "{table}" WHERE "{name}" IS NOT NULL')
            )
            try:
                for row in rows:
                    json.loads(row[0], object_pairs_hook=unique_object)
            except ValueError as exc:
                raise ValueError(
                    f"Cannot safely convert {table}.{name}; review legacy JSON"
                ) from exc
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{name}" TYPE jsonb USING "{name}"::jsonb'
            )
    # Background runs need no chat session; aggregate evaluations need no case.
    # The initial schema required both despite current domain models allowing null.
    op.execute("ALTER TABLE agent_runs ALTER COLUMN session_id DROP NOT NULL")
    op.execute("ALTER TABLE eval_results ALTER COLUMN eval_case_id DROP NOT NULL")


def downgrade() -> None:
    # Retain compatible storage and domain state on code rollback.
    pass
