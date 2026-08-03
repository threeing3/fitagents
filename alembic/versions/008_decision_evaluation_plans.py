"""Add decision evaluation plans and structured follow-ups.

Revision ID: 008_decision_evaluation_plans
Revises: 007_background_tasks
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "008_decision_evaluation_plans"
down_revision: Union[str, None] = "007_background_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_evaluation_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("evaluation_type", sa.String(80), nullable=False),
        sa.Column("baseline_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("expected_action", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("objective_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("subjective_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("minimum_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("implementation_status", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("outcome_status", sa.String(40), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("followup_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_decision_evaluation_plans_decision_id", "decision_evaluation_plans", ["decision_id"])
    op.create_index("ix_decision_evaluation_plans_user_id", "decision_evaluation_plans", ["user_id"])
    op.create_index("ix_decision_evaluation_plans_decision_id", "decision_evaluation_plans", ["decision_id"])
    op.create_index("ix_decision_evaluation_plans_status", "decision_evaluation_plans", ["status"])
    op.create_index("ix_decision_evaluation_plans_evaluation_type", "decision_evaluation_plans", ["evaluation_type"])
    op.create_index("ix_decision_evaluation_plans_implementation_status", "decision_evaluation_plans", ["implementation_status"])
    op.create_index("ix_decision_evaluation_plans_outcome_status", "decision_evaluation_plans", ["outcome_status"])
    op.create_index("ix_decision_evaluation_plans_window_start", "decision_evaluation_plans", ["window_start"])
    op.create_index("ix_decision_evaluation_plans_window_end", "decision_evaluation_plans", ["window_end"])
    op.create_index("ix_decision_evaluation_plans_next_check_at", "decision_evaluation_plans", ["next_check_at"])
    op.create_index("ix_decision_evaluation_plans_status_next_check", "decision_evaluation_plans", ["status", "next_check_at"])
    op.create_index("ix_decision_evaluation_plans_user_status", "decision_evaluation_plans", ["user_id", "status"])

    op.create_table(
        "decision_followups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evaluation_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("decision_evaluation_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_type", sa.String(80), nullable=False),
        sa.Column("question_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("trigger_type", sa.String(40), nullable=False, server_default="scheduled"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_decision_followups_evaluation_plan_id", "decision_followups", ["evaluation_plan_id"])
    op.create_index("ix_decision_followups_user_id", "decision_followups", ["user_id"])
    op.create_index("ix_decision_followups_question_type", "decision_followups", ["question_type"])
    op.create_index("ix_decision_followups_status", "decision_followups", ["status"])
    op.create_index("ix_decision_followups_user_status", "decision_followups", ["user_id", "status"])
    op.create_index("ix_decision_followups_plan_status", "decision_followups", ["evaluation_plan_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_decision_followups_plan_status", table_name="decision_followups")
    op.drop_index("ix_decision_followups_user_status", table_name="decision_followups")
    op.drop_index("ix_decision_followups_status", table_name="decision_followups")
    op.drop_index("ix_decision_followups_question_type", table_name="decision_followups")
    op.drop_index("ix_decision_followups_user_id", table_name="decision_followups")
    op.drop_index("ix_decision_followups_evaluation_plan_id", table_name="decision_followups")
    op.drop_table("decision_followups")
    op.drop_index("ix_decision_evaluation_plans_user_status", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_status_next_check", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_next_check_at", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_window_end", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_window_start", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_outcome_status", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_implementation_status", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_evaluation_type", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_status", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_decision_id", table_name="decision_evaluation_plans")
    op.drop_index("ix_decision_evaluation_plans_user_id", table_name="decision_evaluation_plans")
    op.drop_constraint("uq_decision_evaluation_plans_decision_id", "decision_evaluation_plans", type_="unique")
    op.drop_table("decision_evaluation_plans")
