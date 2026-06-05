import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fast_api.app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 20, delay_seconds: float = 1.5) -> None:
    """Create pgvector extension, run Alembic migrations, and create tables.

    Uses Alembic for schema migrations (production-ready, version-controlled).
    Falls back to Base.metadata.create_all for fresh environments where no
    migration history exists.
    """

    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with engine.begin() as connection:
                if settings.use_pgvector:
                    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            from fast_api.app.db import models  # noqa: F401

            # Try Alembic first (production path)
            try:
                from alembic.config import Config
                from alembic import command
                import os

                alembic_ini = os.path.join(
                    os.path.dirname(__file__), "..", "..", "..", "alembic.ini"
                )
                if os.path.exists(alembic_ini):
                    alembic_cfg = Config(alembic_ini)
                    command.upgrade(alembic_cfg, "head")
                else:
                    raise FileNotFoundError("alembic.ini not found")
            except Exception:
                # Fallback for fresh deployments without migration history
                Base.metadata.create_all(bind=engine)

            _apply_compatibility_migrations()
            return
        except Exception as exc:  # pragma: no cover - exercised in Docker startup
            last_error = exc
            time.sleep(delay_seconds)

    raise RuntimeError(f"Database initialization failed: {last_error}")


def _apply_compatibility_migrations() -> None:
    """Apply small idempotent repairs for existing local Docker volumes.

    The project is evolving quickly during local agent development. These
    statements only add missing columns and keep existing data intact.
    """
    if not settings.database_url.startswith("postgresql"):
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS username varchar(80),
                    ADD COLUMN IF NOT EXISTS avatar_url text;
                CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username
                    ON users(username)
                    WHERE username IS NOT NULL;

                ALTER TABLE plan_templates
                    ADD COLUMN IF NOT EXISTS constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
                    ADD COLUMN IF NOT EXISTS rationale text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb,
                    ADD COLUMN IF NOT EXISTS version varchar(40) NOT NULL DEFAULT 'v1',
                    ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true,
                    ADD COLUMN IF NOT EXISTS status varchar(32) NOT NULL DEFAULT 'active';
                CREATE INDEX IF NOT EXISTS ix_plan_templates_enabled
                    ON plan_templates(enabled);
                CREATE INDEX IF NOT EXISTS ix_plan_templates_status
                    ON plan_templates(status);

                ALTER TABLE coaching_cases
                    ADD COLUMN IF NOT EXISTS case_type varchar(80) NOT NULL DEFAULT 'general',
                    ADD COLUMN IF NOT EXISTS title varchar(200) NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS profile_summary text,
                    ADD COLUMN IF NOT EXISTS scenario text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS situation text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS approach text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS coach_response_pattern text NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS key_principles jsonb NOT NULL DEFAULT '[]'::jsonb;
                CREATE INDEX IF NOT EXISTS ix_coaching_cases_case_type
                    ON coaching_cases(case_type);

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
                CREATE INDEX IF NOT EXISTS ix_agent_task_states_user_id
                    ON agent_task_states(user_id);
                CREATE INDEX IF NOT EXISTS ix_agent_task_states_task_type
                    ON agent_task_states(task_type);
                CREATE INDEX IF NOT EXISTS ix_agent_task_states_status
                    ON agent_task_states(status);
                CREATE INDEX IF NOT EXISTS ix_agent_task_states_source_run_id
                    ON agent_task_states(source_run_id);

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
                CREATE INDEX IF NOT EXISTS ix_agent_task_events_task_id
                    ON agent_task_events(task_id);
                CREATE INDEX IF NOT EXISTS ix_agent_task_events_user_id
                    ON agent_task_events(user_id);
                CREATE INDEX IF NOT EXISTS ix_agent_task_events_agent_run_id
                    ON agent_task_events(agent_run_id);
                CREATE INDEX IF NOT EXISTS ix_agent_task_events_event_type
                    ON agent_task_events(event_type);

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
                CREATE INDEX IF NOT EXISTS ix_agent_run_replays_agent_run_id
                    ON agent_run_replays(agent_run_id);
                CREATE INDEX IF NOT EXISTS ix_agent_run_replays_user_id
                    ON agent_run_replays(user_id);
                CREATE INDEX IF NOT EXISTS ix_agent_run_replays_session_id
                    ON agent_run_replays(session_id);
                CREATE INDEX IF NOT EXISTS ix_agent_run_replays_replay_status
                    ON agent_run_replays(replay_status);
                """
            )
        )
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'coaching_cases' AND column_name = 'situation'
                    ) THEN
                        EXECUTE 'UPDATE coaching_cases SET situation = COALESCE(NULLIF(situation, ''''), scenario, '''') WHERE situation = ''''';
                        EXECUTE 'UPDATE coaching_cases SET scenario = COALESCE(NULLIF(scenario, ''''), situation, '''') WHERE scenario = ''''';
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'coaching_cases' AND column_name = 'coach_response_pattern'
                    ) THEN
                        EXECUTE 'UPDATE coaching_cases SET coach_response_pattern = COALESCE(NULLIF(coach_response_pattern, ''''), approach, '''') WHERE coach_response_pattern = ''''';
                        EXECUTE 'UPDATE coaching_cases SET approach = COALESCE(NULLIF(approach, ''''), coach_response_pattern, '''') WHERE approach = ''''';
                    END IF;
                END $$;
                """
            )
        )
