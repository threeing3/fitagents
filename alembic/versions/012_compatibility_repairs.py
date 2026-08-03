"""Move additive local-volume repairs into the migration history.

Revision ID: 012_compatibility_repairs
Revises: 011_chat_message_token_counts
Create Date: 2026-08-03

This migration is intentionally additive and idempotent. It replaces the old
application-startup compatibility SQL so schema changes are visible, reviewable,
and reproducible in an experiment or deployment.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "012_compatibility_repairs"
down_revision: Union[str, None] = "011_chat_message_token_counts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These repairs preserve existing rows and are safe to re-run against a
    # partially upgraded development volume.
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS username varchar(80),
            ADD COLUMN IF NOT EXISTS avatar_url text;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username
            ON users(username) WHERE username IS NOT NULL;

        ALTER TABLE plan_templates
            ADD COLUMN IF NOT EXISTS constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS rationale text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS version varchar(40) NOT NULL DEFAULT 'v1',
            ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true,
            ADD COLUMN IF NOT EXISTS status varchar(32) NOT NULL DEFAULT 'active';
        CREATE INDEX IF NOT EXISTS ix_plan_templates_enabled ON plan_templates(enabled);
        CREATE INDEX IF NOT EXISTS ix_plan_templates_status ON plan_templates(status);

        ALTER TABLE coaching_cases
            ADD COLUMN IF NOT EXISTS case_type varchar(80) NOT NULL DEFAULT 'general',
            ADD COLUMN IF NOT EXISTS title varchar(200) NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS profile_summary text,
            ADD COLUMN IF NOT EXISTS scenario text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS situation text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS approach text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS coach_response_pattern text NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS key_principles jsonb NOT NULL DEFAULT '[]'::jsonb;
        CREATE INDEX IF NOT EXISTS ix_coaching_cases_case_type ON coaching_cases(case_type);

        UPDATE coaching_cases
        SET title = COALESCE(NULLIF(title, ''), case_id)
        WHERE title = '';

        CREATE TABLE IF NOT EXISTS agent_task_states (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            task_type varchar(80) NOT NULL,
            title varchar(200) NOT NULL,
            objective text NOT NULL,
            status varchar(32) NOT NULL DEFAULT 'active',
            phase varchar(80) NOT NULL DEFAULT 'observe',
            current_step text,
            success_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
            next_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
            progress_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            source_run_id uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
            last_observed_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz DEFAULT now(),
            updated_at timestamptz DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_agent_task_states_user_id ON agent_task_states(user_id);
        CREATE INDEX IF NOT EXISTS ix_agent_task_states_task_type ON agent_task_states(task_type);
        CREATE INDEX IF NOT EXISTS ix_agent_task_states_status ON agent_task_states(status);
        CREATE INDEX IF NOT EXISTS ix_agent_task_states_source_run_id ON agent_task_states(source_run_id);

        CREATE TABLE IF NOT EXISTS agent_task_events (
            id uuid PRIMARY KEY,
            task_id uuid NOT NULL REFERENCES agent_task_states(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            agent_run_id uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
            event_type varchar(80) NOT NULL,
            summary text NOT NULL,
            payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz DEFAULT now(),
            updated_at timestamptz DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_agent_task_events_task_id ON agent_task_events(task_id);
        CREATE INDEX IF NOT EXISTS ix_agent_task_events_user_id ON agent_task_events(user_id);
        CREATE INDEX IF NOT EXISTS ix_agent_task_events_agent_run_id ON agent_task_events(agent_run_id);
        CREATE INDEX IF NOT EXISTS ix_agent_task_events_event_type ON agent_task_events(event_type);

        CREATE TABLE IF NOT EXISTS agent_run_replays (
            id uuid PRIMARY KEY,
            agent_run_id uuid NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id uuid REFERENCES conversation_sessions(id) ON DELETE SET NULL,
            request_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            state_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            tool_plan_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            response_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            config_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            replay_status varchar(32) NOT NULL DEFAULT 'recorded',
            created_at timestamptz DEFAULT now(),
            updated_at timestamptz DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_agent_run_replays_agent_run_id ON agent_run_replays(agent_run_id);
        CREATE INDEX IF NOT EXISTS ix_agent_run_replays_user_id ON agent_run_replays(user_id);
        CREATE INDEX IF NOT EXISTS ix_agent_run_replays_session_id ON agent_run_replays(session_id);
        CREATE INDEX IF NOT EXISTS ix_agent_run_replays_replay_status ON agent_run_replays(replay_status);
        """
    )


def downgrade() -> None:
    # Deliberately a no-op: this compatibility migration must never delete
    # user data or remove columns from a shared development volume.
    pass
