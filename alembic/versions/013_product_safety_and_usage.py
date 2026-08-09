"""Add durable model usage and demo reset state.

Revision ID: 013_product_safety_and_usage
Revises: 012_compatibility_repairs
Create Date: 2026-08-09

The migration is additive and deliberately keeps downgrade as a no-op so a
rollback cannot delete usage evidence or demo-account history.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "013_product_safety_and_usage"
down_revision: Union[str, None] = "012_compatibility_repairs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id uuid PRIMARY KEY,
            user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            event_date date NOT NULL,
            event_type varchar(48) NOT NULL DEFAULT 'model_call',
            provider varchar(48) NOT NULL DEFAULT 'unknown',
            model_name varchar(120) NOT NULL DEFAULT 'unknown',
            endpoint varchar(160),
            status varchar(32) NOT NULL DEFAULT 'reserved',
            metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_usage_events_day_user
            ON usage_events(event_date, user_id);
        CREATE INDEX IF NOT EXISTS ix_usage_events_day_status
            ON usage_events(event_date, status);

        CREATE TABLE IF NOT EXISTS demo_reset_states (
            id uuid PRIMARY KEY,
            demo_key varchar(80) NOT NULL UNIQUE,
            active_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            reset_date date NOT NULL,
            reset_count integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    # Intentionally preserve all usage and demo history during rollback.
    pass
