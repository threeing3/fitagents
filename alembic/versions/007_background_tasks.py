"""Add background tasks for async workload processing.

Revision ID: 007_background_tasks
Revises: 006_decision_outcomes
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "007_background_tasks"
down_revision: Union[str, None] = "006_decision_outcomes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_background_tasks_user_id", "background_tasks", ["user_id"])
    op.create_index("ix_background_tasks_task_type", "background_tasks", ["task_type"])
    op.create_index("ix_background_tasks_status", "background_tasks", ["status"])
    op.create_index("ix_background_tasks_status_created", "background_tasks", ["status", "created_at"])
    op.create_index("ix_background_tasks_user_status", "background_tasks", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_background_tasks_user_status", table_name="background_tasks")
    op.drop_index("ix_background_tasks_status_created", table_name="background_tasks")
    op.drop_index("ix_background_tasks_status", table_name="background_tasks")
    op.drop_index("ix_background_tasks_task_type", table_name="background_tasks")
    op.drop_index("ix_background_tasks_user_id", table_name="background_tasks")
    op.drop_table("background_tasks")
