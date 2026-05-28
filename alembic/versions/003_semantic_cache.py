"""Add semantic_cache table for LLM response caching.

Revision ID: 003_semantic_cache
Revises: 002_feedback
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from fast_api.app.core.config import get_settings

settings = get_settings()
# pgvector Vector type or JSONB fallback
vector_type = (
    postgresql.VECTOR(settings.vector_dimension)
    if settings.use_pgvector
    else postgresql.JSONB
)

revision: str = "003_semantic_cache"
down_revision: Union[str, None] = "002_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "semantic_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("prompt_hash", sa.String(64), index=True, nullable=False),
        sa.Column("system_prompt_hash", sa.String(64), index=True, nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(120), default="unknown"),
        sa.Column("hit_count", sa.Integer(), default=1),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ttl_seconds", sa.Integer(), default=86400),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Create IVFFlat index for fast ANN search on pgvector
    if settings.use_pgvector:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_semantic_cache_embedding "
            "ON semantic_cache USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )


def downgrade() -> None:
    op.drop_table("semantic_cache")
