"""Add memory links for corrections.

Revision ID: 005_memory_links
Revises: 004_hindsight_memory_fields
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005_memory_links"
down_revision: Union[str, None] = "004_hindsight_memory_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("long_term_memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("long_term_memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("link_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memory_links_user_id", "memory_links", ["user_id"])
    op.create_index("ix_memory_links_source_memory_id", "memory_links", ["source_memory_id"])
    op.create_index("ix_memory_links_target_memory_id", "memory_links", ["target_memory_id"])
    op.create_index("ix_memory_links_link_type", "memory_links", ["link_type"])


def downgrade() -> None:
    op.drop_index("ix_memory_links_link_type", table_name="memory_links")
    op.drop_index("ix_memory_links_target_memory_id", table_name="memory_links")
    op.drop_index("ix_memory_links_source_memory_id", table_name="memory_links")
    op.drop_index("ix_memory_links_user_id", table_name="memory_links")
    op.drop_table("memory_links")
