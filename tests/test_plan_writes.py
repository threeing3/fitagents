"""Independent-session expected-content writes; not PostgreSQL load tests."""

import asyncio
import copy
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.plan_writes import (
    StalePlanWriteError,
    lock_plan_owner,
    replace_plan_content,
)


@pytest.fixture
def plan_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated_plan_writes.db'}")
    Base.metadata.create_all(engine)
    owner_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    baseline = {"training_days": [{"date": "2026-10-10", "sets": 4}]}
    with Session(engine) as db:
        db.add(models.User(id=owner_id, email="writes@example.test", password_hash="synthetic"))
        db.flush()
        db.add(
            models.TrainingPlan(id=plan_id, user_id=owner_id, status="active", plan_json=baseline)
        )
        db.commit()
    yield engine, owner_id, plan_id, baseline
    engine.dispose()


def test_stale_independent_session_cannot_overwrite_committed_plan(plan_store):
    engine, owner_id, plan_id, baseline = plan_store
    winner = {"training_days": [{"date": "2026-10-10", "sets": 3}]}
    with Session(engine) as stale, Session(engine) as first:
        cached = stale.get(models.TrainingPlan, plan_id)
        expected = copy.deepcopy(cached.plan_json)
        stale.commit()  # Release the read transaction, retain the stale input.
        replace_plan_content(first, owner_id, plan_id, baseline, winner)
        first.commit()
        with pytest.raises(StalePlanWriteError):
            replace_plan_content(stale, owner_id, plan_id, expected, {"stale": True})
        stale.rollback()
    with Session(engine) as reader:
        assert reader.get(models.TrainingPlan, plan_id).plan_json == winner


@pytest.mark.parametrize("failure", ["owner", "archived", "missing", "baseline"])
def test_conditional_write_rejects_wrong_target(plan_store, failure):
    engine, owner_id, plan_id, baseline = plan_store
    with Session(engine) as db:
        if failure == "owner":
            other = models.User(email="other-writes@example.test", password_hash="synthetic")
            db.add(other)
            db.commit()
            owner_id = other.id
        elif failure == "archived":
            db.get(models.TrainingPlan, plan_id).status = "archived"
            db.commit()
        elif failure == "missing":
            plan_id = uuid.uuid4()
        elif failure == "baseline":
            baseline = {"unverified": True}
        with pytest.raises(StalePlanWriteError):
            replace_plan_content(db, owner_id, plan_id, baseline, {"must_not_write": True})
        db.rollback()
        assert all(
            plan.plan_json != {"must_not_write": True} for plan in db.query(models.TrainingPlan)
        )


def test_conditional_write_shares_outer_transaction(plan_store):
    engine, owner_id, plan_id, baseline = plan_store
    with Session(engine) as db:
        changed = replace_plan_content(db, owner_id, plan_id, baseline, {"candidate": True})
        assert changed.plan_json == {"candidate": True}
        db.rollback()
    with Session(engine) as reader:
        assert reader.get(models.TrainingPlan, plan_id).plan_json == baseline


def test_owner_gate_compiles_postgresql_row_lock(plan_store):
    engine, owner_id, _plan_id, _baseline = plan_store
    statements = []
    with Session(engine) as db:
        original = db.scalar

        def capture(statement, *args, **kwargs):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return original(statement, *args, **kwargs)

        db.scalar = capture
        lock_plan_owner(db, owner_id)
    assert "FOR NO KEY UPDATE" in statements[0]


def test_runtime_records_stale_write_as_blocked_without_retry(plan_store):
    engine, owner_id, plan_id, _baseline = plan_store
    calls = []
    with Session(engine) as db:
        registry = ToolRegistry()

        def write(_payload):
            calls.append("attempt")
            replace_plan_content(db, owner_id, plan_id, {"stale": True}, {"bad": True})

        registry.register(
            ToolSpec(
                name="plan.write",
                description="Conditional plan write",
                permission_level="write",
                side_effects=True,
                retry_count=3,
            ),
            write,
        )
        result = asyncio.run(registry.execute("plan.write"))
        assert result.status == "blocked"
        assert result.attempts == 1  # Handler attempted, business write rejected.
        assert calls == ["attempt"]
        assert registry.successful_call("plan.write") is None


def test_owner_gate_preserves_fk_key_share_compatibility():
    class NoAutoflush:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Database:
        no_autoflush = NoAutoflush()
        statement = None

        def scalar(self, statement):
            self.statement = statement
            return object()

    db = Database()
    lock_plan_owner(db, uuid.uuid4())
    sql = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "FOR NO KEY UPDATE" in sql
