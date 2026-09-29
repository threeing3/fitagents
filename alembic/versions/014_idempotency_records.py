"""Add durable idempotency records for side-effecting operations.

Revision ID: 014_idempotency_records
Revises: 013_product_safety_and_usage
Create Date: 2026-09-20

The migration is additive. Downgrade intentionally preserves request execution
evidence instead of deleting the table.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "014_idempotency_records"
down_revision: Union[str, None] = "013_product_safety_and_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_records (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            operation varchar(80) NOT NULL,
            idempotency_key varchar(128) NOT NULL,
            request_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            response_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            status varchar(32) NOT NULL DEFAULT 'processing',
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_idempotency_user_operation_key
                UNIQUE (user_id, operation, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_idempotency_records_user_operation
            ON idempotency_records(user_id, operation);
        CREATE INDEX IF NOT EXISTS ix_idempotency_records_user_id
            ON idempotency_records(user_id);
        CREATE INDEX IF NOT EXISTS ix_idempotency_records_status
            ON idempotency_records(status);
        """
    )


def downgrade() -> None:
    # Preserve request execution evidence during rollback.
    pass
