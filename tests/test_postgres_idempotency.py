"""PostgreSQL concurrency checks for durable workout idempotency.

These tests are opt-in because they require an isolated PostgreSQL database.
Set FITAGENT_POSTGRES_TEST_URL to enable them. Each test run creates and later
removes only its own randomly named schema.
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from fast_api.app.core.config import Settings
from fast_api.app.core.errors import IdempotencyConflictError
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.schemas.agent import WorkoutLogRequest
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider

POSTGRES_TEST_URL = os.getenv("FITAGENT_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="FITAGENT_POSTGRES_TEST_URL is required for PostgreSQL concurrency tests",
)


@pytest.fixture(scope="module")
def postgres_business():
    assert POSTGRES_TEST_URL is not None
    schema = f"fitagent_idempotency_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(POSTGRES_TEST_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(CreateSchema(schema))

    engine = create_engine(
        POSTGRES_TEST_URL,
        connect_args={"options": f"-c search_path={schema},public"},
        pool_size=6,
        max_overflow=0,
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    user_id = uuid.uuid4()
    with session_factory() as db:
        db.add(
            models.User(
                id=user_id,
                email=f"postgres-concurrency-{user_id}@example.test",
                password_hash="not-a-login",
            )
        )
        db.commit()

    provider = ModelProvider(
        Settings(
            _env_file=None,
            database_url=POSTGRES_TEST_URL,
            llm_provider="offline",
            embedding_provider="offline",
            use_pgvector=False,
            jwt_secret_key="synthetic-postgres-concurrency-only",
        )
    )
    yield SimpleNamespace(
        engine=engine,
        session_factory=session_factory,
        provider=provider,
        user_id=user_id,
        schema=schema,
    )

    engine.dispose()
    with admin_engine.connect() as connection:
        connection.execute(DropSchema(schema, cascade=True, if_exists=True))
    admin_engine.dispose()


def _workout_request(user_id, key, *, rpe=5):
    return WorkoutLogRequest(
        user_id=user_id,
        idempotency_key=key,
        performed_at=datetime(2026, 9, 20, 10),
        workout_name=f"postgres concurrent dumbbell session {key}",
        duration_minutes=35,
        rpe=rpe,
        completion_rate=0.9,
        exercises=[{"name": "dumbbell row", "sets": [{"reps": 10, "weight": 12}]}],
    )


def _submit_workout(postgres_business, barrier, key, *, rpe=5):
    with postgres_business.session_factory() as db:
        service = CoachAgentService(db, postgres_business.provider)
        barrier.wait(timeout=10)
        log = service.record_workout_log(_workout_request(postgres_business.user_id, key, rpe=rpe))
        return str(log.id), bool(getattr(log, "_idempotent_replay", False)), log.rpe


def test_concurrent_identical_workout_requests_commit_once(postgres_business, record_property):
    repetitions = 10
    observed = []
    for index in range(repetitions):
        key = f"postgres-same-payload-{index}"
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(_submit_workout, postgres_business, barrier, key),
                executor.submit(_submit_workout, postgres_business, barrier, key),
            ]
            pair = [future.result(timeout=20) for future in futures]
        assert pair[0][0] == pair[1][0]
        assert sorted(item[1] for item in pair) == [False, True]
        observed.append({"key": key, "log_id": pair[0][0], "replays": [p[1] for p in pair]})

    with postgres_business.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(models.IdempotencyRecord)) == repetitions
        assert db.scalar(select(func.count()).select_from(models.WorkoutLog)) == repetitions
        assert db.scalar(select(func.count()).select_from(models.WorkoutSession)) == repetitions
        assert db.scalar(select(func.count()).select_from(models.ExerciseLog)) == repetitions
        assert db.scalar(select(func.count()).select_from(models.LongTermMemory)) == repetitions
    record_property("postgres_concurrent_identical", observed)


def test_concurrent_conflicting_workout_requests_choose_one_payload(
    postgres_business, record_property
):
    repetitions = 5
    observed = []

    def submit(barrier, key, rpe):
        try:
            log_id, replayed, stored_rpe = _submit_workout(postgres_business, barrier, key, rpe=rpe)
            return {
                "status": "committed",
                "log_id": log_id,
                "replayed": replayed,
                "rpe": stored_rpe,
            }
        except IdempotencyConflictError as exc:
            return {"status": "conflict", "detail": str(exc), "rpe": rpe}

    for index in range(repetitions):
        key = f"postgres-conflicting-payload-{index}"
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(submit, barrier, key, 4),
                executor.submit(submit, barrier, key, 8),
            ]
            pair = [future.result(timeout=20) for future in futures]
        assert sorted(item["status"] for item in pair) == ["committed", "conflict"]
        winner = next(item for item in pair if item["status"] == "committed")
        assert winner["replayed"] is False
        observed.append({"key": key, "outcomes": pair})

    with postgres_business.session_factory() as db:
        conflict_records = db.scalars(
            select(models.IdempotencyRecord).where(
                models.IdempotencyRecord.idempotency_key.like("postgres-conflicting-payload-%")
            )
        ).all()
        assert len(conflict_records) == repetitions
        for record in conflict_records:
            log = db.get(models.WorkoutLog, uuid.UUID(record.response_json["workout_log_id"]))
            assert log is not None
            assert log.rpe == record.request_json["rpe"]
    record_property("postgres_concurrent_conflicts", observed)
