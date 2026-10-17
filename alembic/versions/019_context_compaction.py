"""Independent durable context checkpoints; preserve all original conversations."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "019_context_compaction"
down_revision = "018_stream_journals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    context = op.get_context()
    if (
        not context.as_sql
        and op.get_bind().dialect.name == "postgresql"
        and not context.config.attributes.get("context_compaction_backup_verified")
    ):
        raise RuntimeError(
            "Migration 019 requires a verified backup. Run "
            "python -m scripts.apply_context_compaction before starting the application."
        )
    op.create_table(
        "context_checkpoint_states",
        sa.Column("scope_id", sa.String(100), primary_key=True),
        sa.Column("owner_id", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("checkpoint", JSONB(), nullable=False),
        sa.Column("active_compaction_id", sa.String(80)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "context_compaction_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_id", sa.String(100), nullable=False),
        sa.Column("owner_id", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("compaction_id", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table, columns in (
        ("context_checkpoint_states", ["owner_id", "session_id"]),
        ("context_compaction_events", ["scope_id", "owner_id", "session_id", "compaction_id"]),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    # Diagnostic checkpoints/evidence survive code rollback. No implicit deletion.
    pass
