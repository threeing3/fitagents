"""Add Hindsight-style fields to long_term_memories.

Revision ID: 004_hindsight_memory_fields
Revises: 003_semantic_cache
Create Date: 2026-06-10
"""
from typing import Sequence, Union

from alembic import op


revision: str = "004_hindsight_memory_fields"
down_revision: Union[str, None] = "003_semantic_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE long_term_memories
            ADD COLUMN IF NOT EXISTS memory_network varchar(40) NOT NULL DEFAULT 'world',
            ADD COLUMN IF NOT EXISTS fact_kind varchar(80) NOT NULL DEFAULT 'unknown',
            ADD COLUMN IF NOT EXISTS occurred_start timestamptz,
            ADD COLUMN IF NOT EXISTS occurred_end timestamptz,
            ADD COLUMN IF NOT EXISTS mentioned_at timestamptz,
            ADD COLUMN IF NOT EXISTS entities jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS evidence jsonb NOT NULL DEFAULT '[]'::jsonb;
        """
    )

    op.execute(
        """
        UPDATE long_term_memories
        SET fact_kind = CASE
            WHEN memory_type IN ('medical_context', 'risk_signal') OR category = 'risk' THEN 'health_fact'
            WHEN memory_type = 'nutrition_habit' OR category = 'nutrition' THEN 'nutrition_event'
            WHEN memory_type = 'training_performance' OR category = 'training' THEN 'workout_event'
            WHEN memory_type = 'recent_state' OR category = 'recovery' THEN 'recovery_event'
            WHEN memory_type = 'correction' THEN 'correction'
            WHEN category = 'daily_summary' THEN 'daily_summary'
            ELSE 'unknown'
        END
        WHERE fact_kind = 'unknown'
        """
    )
    op.execute(
        "UPDATE long_term_memories SET mentioned_at = COALESCE(mentioned_at, created_at)"
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_long_term_memories_user_status_network
            ON long_term_memories(user_id, status, memory_network);
        CREATE INDEX IF NOT EXISTS ix_long_term_memories_user_status_fact_kind
            ON long_term_memories(user_id, status, fact_kind);
        CREATE INDEX IF NOT EXISTS ix_long_term_memories_occurred_start
            ON long_term_memories(occurred_start);
        CREATE INDEX IF NOT EXISTS ix_long_term_memories_entities_gin
            ON long_term_memories USING gin(entities);
        """
    )


def downgrade() -> None:
    op.drop_index("ix_long_term_memories_entities_gin", table_name="long_term_memories")
    op.drop_index("ix_long_term_memories_occurred_start", table_name="long_term_memories")
    op.drop_index("ix_long_term_memories_user_status_fact_kind", table_name="long_term_memories")
    op.drop_index("ix_long_term_memories_user_status_network", table_name="long_term_memories")
    op.drop_column("long_term_memories", "evidence")
    op.drop_column("long_term_memories", "entities")
    op.drop_column("long_term_memories", "mentioned_at")
    op.drop_column("long_term_memories", "occurred_end")
    op.drop_column("long_term_memories", "occurred_start")
    op.drop_column("long_term_memories", "fact_kind")
    op.drop_column("long_term_memories", "memory_network")
