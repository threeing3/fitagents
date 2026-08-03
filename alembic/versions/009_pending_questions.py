"""Add pending questions for short-term follow-up state.

Revision ID: 009_pending_questions
Revises: 008_decision_evaluation_plans
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "009_pending_questions"
down_revision: Union[str, None] = "008_decision_evaluation_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("question_type", sa.String(80), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("options_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("answer_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("resolved_message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pending_questions_user_id", "pending_questions", ["user_id"])
    op.create_index("ix_pending_questions_session_id", "pending_questions", ["session_id"])
    op.create_index("ix_pending_questions_assistant_message_id", "pending_questions", ["assistant_message_id"])
    op.create_index("ix_pending_questions_question_type", "pending_questions", ["question_type"])
    op.create_index("ix_pending_questions_status", "pending_questions", ["status"])
    op.create_index("ix_pending_questions_resolved_message_id", "pending_questions", ["resolved_message_id"])
    op.create_index("ix_pending_questions_expires_at", "pending_questions", ["expires_at"])
    op.create_index("ix_pending_questions_user_session_status", "pending_questions", ["user_id", "session_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_pending_questions_user_session_status", table_name="pending_questions")
    op.drop_index("ix_pending_questions_expires_at", table_name="pending_questions")
    op.drop_index("ix_pending_questions_resolved_message_id", table_name="pending_questions")
    op.drop_index("ix_pending_questions_status", table_name="pending_questions")
    op.drop_index("ix_pending_questions_question_type", table_name="pending_questions")
    op.drop_index("ix_pending_questions_assistant_message_id", table_name="pending_questions")
    op.drop_index("ix_pending_questions_session_id", table_name="pending_questions")
    op.drop_index("ix_pending_questions_user_id", table_name="pending_questions")
    op.drop_table("pending_questions")
