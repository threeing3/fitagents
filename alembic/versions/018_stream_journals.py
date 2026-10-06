"""Add independent persistent diagnostic journals without changing business rows."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "018_stream_journals"
down_revision = "017_domain_jsonb_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_journals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.String(80), nullable=False),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("recorded_count", sa.Integer(), nullable=False),
        sa.Column("header", JSONB(), nullable=False),
    )
    op.create_index("ix_stream_journals_owner_id", "stream_journals", ["owner_id"])
    op.create_table(
        "stream_journal_events",
        sa.Column(
            "journal_id", UUID(as_uuid=True), sa.ForeignKey("stream_journals.id"), primary_key=True
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("payload", JSONB(), nullable=False),
    )


def downgrade() -> None:
    # Preserve diagnostic evidence on code rollback; deletion requires explicit consent.
    pass
