"""Persist action-bound approvals. Additive; downgrade preserves audit records."""

from alembic import op

revision = "015_pending_approvals"
down_revision = "014_idempotency_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_approvals (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id),
            session_id uuid,
            job_id uuid REFERENCES background_tasks(id),
            tool_name varchar(120) NOT NULL,
            tool_description text NOT NULL,
            permission_level varchar(32) NOT NULL,
            input_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            input_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            context_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            status varchar(32) NOT NULL DEFAULT 'pending',
            expires_at timestamptz NOT NULL,
            decided_at timestamptz,
            decided_by varchar(32),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_pending_approval_job UNIQUE (job_id)
        );
        CREATE INDEX IF NOT EXISTS ix_pending_approvals_user_id ON pending_approvals(user_id);
        CREATE INDEX IF NOT EXISTS ix_pending_approvals_status ON pending_approvals(status);
    """)


def downgrade() -> None:
    # Removing this table would destroy approval/execution evidence.
    pass
