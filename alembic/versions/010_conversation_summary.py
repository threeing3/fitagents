"""Add session-level conversation summary.

Revision ID: 010_conversation_summary
Revises: 009_pending_questions
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "010_conversation_summary"
down_revision: Union[str, None] = "009_pending_questions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column("conversation_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_sessions", "summary_updated_at")
    op.drop_column("conversation_sessions", "conversation_summary")
