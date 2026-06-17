"""Add decision outcomes for outcome-aware memory.

Revision ID: 006_decision_outcomes
Revises: 005_memory_links
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "006_decision_outcomes"
down_revision: Union[str, None] = "005_memory_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome_type", sa.String(80), nullable=False),
        sa.Column("outcome_status", sa.String(40), nullable=False),
        sa.Column("outcome_summary", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("observed_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reflected_memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("long_term_memories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_decision_outcomes_decision_id", "decision_outcomes", ["decision_id"])
    op.create_index("ix_decision_outcomes_user_id", "decision_outcomes", ["user_id"])
    op.create_index("ix_decision_outcomes_decision_id", "decision_outcomes", ["decision_id"])
    op.create_index("ix_decision_outcomes_outcome_type", "decision_outcomes", ["outcome_type"])
    op.create_index("ix_decision_outcomes_outcome_status", "decision_outcomes", ["outcome_status"])
    op.create_index("ix_decision_outcomes_observed_start_at", "decision_outcomes", ["observed_start_at"])
    op.create_index("ix_decision_outcomes_observed_end_at", "decision_outcomes", ["observed_end_at"])


def downgrade() -> None:
    op.drop_index("ix_decision_outcomes_observed_end_at", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_observed_start_at", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_outcome_status", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_outcome_type", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_decision_id", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_user_id", table_name="decision_outcomes")
    op.drop_constraint("uq_decision_outcomes_decision_id", "decision_outcomes", type_="unique")
    op.drop_table("decision_outcomes")
