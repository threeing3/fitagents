"""Add Hindsight-style fields to long_term_memories.

Revision ID: 004_hindsight_memory_fields
Revises: 003_semantic_cache
Create Date: 2026-06-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "004_hindsight_memory_fields"
down_revision: Union[str, None] = "003_semantic_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "long_term_memories",
        sa.Column("memory_network", sa.String(40), nullable=False, server_default="world"),
    )
    op.add_column(
        "long_term_memories",
        sa.Column("fact_kind", sa.String(80), nullable=False, server_default="unknown"),
    )
    op.add_column("long_term_memories", sa.Column("occurred_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("long_term_memories", sa.Column("occurred_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("long_term_memories", sa.Column("mentioned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "long_term_memories",
        sa.Column("entities", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )
    op.add_column(
        "long_term_memories",
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
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

    op.create_index(
        "ix_long_term_memories_user_status_network",
        "long_term_memories",
        ["user_id", "status", "memory_network"],
    )
    op.create_index(
        "ix_long_term_memories_user_status_fact_kind",
        "long_term_memories",
        ["user_id", "status", "fact_kind"],
    )
    op.create_index(
        "ix_long_term_memories_occurred_start",
        "long_term_memories",
        ["occurred_start"],
    )
    op.create_index(
        "ix_long_term_memories_entities_gin",
        "long_term_memories",
        ["entities"],
        postgresql_using="gin",
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
