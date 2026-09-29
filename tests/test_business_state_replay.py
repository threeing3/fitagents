"""Real business handlers and models, isolated database, no live model calls."""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.core.errors import IdempotencyConflictError
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.schemas.agent import (
    DailyCheckinRequest,
    PlanAdjustRequest,
    PlanGenerateRequest,
    WorkoutLogRequest,
)
from fast_api.app.services.agent_runtime import (
    AgentExecutor,
    AgentTaskTimeline,
    ToolRegistry,
    ToolSpec,
)
from fast_api.app.services.agent_task_state import AgentTaskStateService
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.context_builder import ContextBuilder
from fast_api.app.services.decision_evaluation import DecisionEvaluationService
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.model_provider import ModelProvider


@pytest.fixture
def business():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    provider = ModelProvider(
        Settings(
            _env_file=None,
            llm_provider="offline",
            embedding_provider="offline",
            use_pgvector=False,
            jwt_secret_key="synthetic-business-tests-only",
        )
    )
    with Session(engine) as db:
        user = models.User(
            id=uuid.uuid4(), email="synthetic@example.test", password_hash="not-a-login"
        )
        other = models.User(
            id=uuid.uuid4(), email="other@example.test", password_hash="not-a-login"
        )
        db.add_all([user, other])
        db.flush()
        profile = models.UserProfile(
            user_id=user.id,
            age=25,
            sex="male",
            height_cm=175,
            weight_kg=70,
            goal="maintenance",
            experience_level="beginner",
            workout_frequency=3,
            equipment_available=["dumbbells"],
            injuries=[],
        )
        db.add(profile)
        db.commit()
        service = CoachAgentService(db, provider)
        session = service.create_session(user.id, "Synthetic", "Business replay")
        yield SimpleNamespace(
            engine=engine,
            db=db,
            user_id=user.id,
            other_id=other.id,
            profile=profile,
            service=service,
            session_id=session.id,
        )
    engine.dispose()


def snapshot(business, record_property):
    """Read committed state with a fresh ORM session rather than cached objects."""
    tables = [
        models.TrainingPlan,
        models.DailyCheckin,
        models.IdempotencyRecord,
        models.RecoveryLog,
        models.WorkoutLog,
        models.WorkoutSession,
        models.ExerciseLog,
        models.LongTermMemory,
        models.RiskNote,
        models.UserProfile,
        models.AgentDecision,
        models.DecisionEvaluationPlan,
        models.AgentTaskState,
        models.AgentTaskEvent,
    ]
    with Session(business.engine) as reader:
        result = {
            model.__tablename__: [
                {column.name: getattr(row, column.name) for column in model.__table__.columns}
                for row in reader.scalars(select(model)).all()
            ]
            for model in tables
        }
    record_property("committed_state", json.dumps(result, ensure_ascii=False, default=str))
    return result


def execute(registry, name, payload, record_property):
    timeline = AgentTaskTimeline("real business state replay")
    step = timeline.add_step("Execute business tool", name)
    execution = asyncio.run(AgentExecutor().execute(registry, timeline, step, payload))
    record_property("tool_trace", json.dumps(execution.result.to_trace(), default=str))
    record_property("task_trace", json.dumps(timeline.to_dict(), default=str))
    return execution


def chat_registry(business, message="生成训练计划"):
    return business.service._build_chat_tool_registry(
        business.user_id, business.session_id, business.profile, message
    )


def test_repeated_real_plan_tool_reuses_active_plan(business, record_property):
    registry = chat_registry(business)
    first = execute(registry, "plan.generate", {"reason": "explicit request"}, record_property)
    second = execute(registry, "plan.generate", {"reason": "explicit request"}, record_property)
    state = snapshot(business, record_property)
    assert first.result.status == second.result.status == "success"
    assert len(state["training_plans"]) == 1
    assert first.result.output_json["plan_id"] == second.result.output_json["plan_id"]


def test_force_generation_archives_old_plan(business, record_property):
    first = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    first_id = first.id
    second = business.service.generate_plan(
        PlanGenerateRequest(user_id=business.user_id, force=True)
    )
    state = snapshot(business, record_property)
    assert len(state["training_plans"]) == 2
    assert {row["id"]: row["status"] for row in state["training_plans"]} == {
        first_id: "archived",
        second.id: "active",
    }


def test_dated_plan_request_replaces_default_then_reuses_matching_version(
    business, record_property
):
    initial = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    target = date.today() + timedelta(days=1)
    request = PlanGenerateRequest(
        user_id=business.user_id, target_date=target, exercise_type="easy_jog"
    )
    changed = business.service.generate_plan(request)
    repeated = business.service.generate_plan(request)
    state = snapshot(business, record_property)
    assert changed.id == repeated.id != initial.id
    assert {row["id"]: row["status"] for row in state["training_plans"]} == {
        initial.id: "archived",
        changed.id: "active",
    }
    assert changed.plan_json["request_constraints"] == {
        "target_date": target.isoformat(),
        "exercise_type": "easy_jog",
    }
    assert business.db.get(models.UserProfile, business.user_id).goal == "maintenance"


@pytest.mark.parametrize(
    ("wrong_field", "expected_issue"),
    [
        ("date", "requested_date_mismatch"),
        ("exercise", "requested_exercise_mismatch"),
    ],
)
def test_invalid_dated_candidate_never_archives_or_writes(
    business, record_property, monkeypatch, wrong_field, expected_issue
):
    original = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    before = snapshot(business, record_property)
    build_plan = business.service._build_plan_json

    def wrong_candidate(*args, **kwargs):
        candidate = build_plan(*args, **kwargs)
        if wrong_field == "date":
            candidate["training_days"][0]["date"] = "2030-01-01"
        else:
            candidate["training_days"][0]["exercises"][0]["name"] = "力量训练"
        return candidate

    monkeypatch.setattr(business.service, "_build_plan_json", wrong_candidate)
    with pytest.raises(ValueError, match=expected_issue):
        business.service.generate_plan(
            PlanGenerateRequest(
                user_id=business.user_id,
                target_date=date.today() + timedelta(days=1),
                exercise_type="easy_jog",
            )
        )
    after = snapshot(business, record_property)
    assert [(row["id"], row["status"]) for row in after["training_plans"]] == [
        (original.id, "active")
    ]
    assert len(after["long_term_memories"]) == len(before["long_term_memories"])
    assert len(after["agent_decisions"]) == len(before["agent_decisions"])
    assert after["user_profiles"] == before["user_profiles"]


def test_dated_plan_rejects_past_date_without_state_change(business, record_property):
    before = snapshot(business, record_property)
    with pytest.raises(ValueError, match="must not be in the past"):
        business.service.generate_plan(
            PlanGenerateRequest(
                user_id=business.user_id,
                target_date=date.today() - timedelta(days=1),
                exercise_type="easy_jog",
            )
        )
    after = snapshot(business, record_property)
    assert after["training_plans"] == before["training_plans"] == []
    assert after["long_term_memories"] == before["long_term_memories"]


def test_repeated_dated_request_does_not_reuse_corrupt_plan(business):
    request = PlanGenerateRequest(
        user_id=business.user_id,
        target_date=date.today() + timedelta(days=1),
        exercise_type="easy_jog",
    )
    plan = business.service.generate_plan(request)
    corrupted = dict(plan.plan_json)
    days = [dict(day) for day in corrupted["training_days"]]
    days[0]["date"] = "2030-01-01"
    corrupted["training_days"] = days
    plan.plan_json = corrupted
    business.db.commit()
    with pytest.raises(ValueError, match="Existing plan does not satisfy"):
        business.service.generate_plan(request)
    with Session(business.engine) as reader:
        plans = reader.scalars(
            select(models.TrainingPlan).where(models.TrainingPlan.user_id == business.user_id)
        ).all()
        assert len(plans) == 1
        assert plans[0].id == plan.id


def test_repairable_plan_issue_is_fixed_before_commit(business, record_property, monkeypatch):
    build_plan = business.service._build_plan_json

    def omit_review_cadence(*args, **kwargs):
        candidate = build_plan(*args, **kwargs)
        candidate.pop("review_cadence")
        return candidate

    monkeypatch.setattr(business.service, "_build_plan_json", omit_review_cadence)
    plan = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    state = snapshot(business, record_property)
    assert plan.plan_json["review_cadence"] == "weekly"
    assert len(state["training_plans"]) == 1
    assert state["agent_decisions"][0]["context_used"]["precommit_verification"]["passed"]


def test_plan_write_failure_rolls_back_archival_and_new_side_effects(
    business, record_property, monkeypatch
):
    original = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    before = snapshot(business, record_property)

    def fail_memory(*args, **kwargs):
        raise RuntimeError("injected before plan commit")

    monkeypatch.setattr(business.service, "_write_memory", fail_memory)
    with pytest.raises(RuntimeError, match="injected before plan commit"):
        business.service.generate_plan(
            PlanGenerateRequest(
                user_id=business.user_id,
                target_date=date.today() + timedelta(days=1),
                exercise_type="easy_jog",
            )
        )
    after = snapshot(business, record_property)
    assert [(row["id"], row["status"]) for row in after["training_plans"]] == [
        (original.id, "active")
    ]
    assert len(after["long_term_memories"]) == len(before["long_term_memories"])
    assert len(after["agent_decisions"]) == len(before["agent_decisions"])


def test_plan_response_loss_can_be_reconciled_in_fresh_service(
    business, record_property, monkeypatch
):
    original = business.service._generate_plan_tool
    committed_ids = []

    def lose_response(user_id):
        output = original(user_id)
        committed_ids.append(output["plan_id"])
        raise TimeoutError("injected after actual plan commit")

    monkeypatch.setattr(business.service, "_generate_plan_tool", lose_response)
    first = execute(chat_registry(business), "plan.generate", {}, record_property)
    assert first.result.status == "outcome_unknown"
    business.db.rollback()
    with Session(business.engine) as db:
        service = CoachAgentService(db, business.service.model_provider)
        profile = db.get(models.UserProfile, business.user_id)
        registry = service._build_chat_tool_registry(
            business.user_id, business.session_id, profile, "生成训练计划"
        )
        second = execute(registry, "plan.generate", {}, record_property)
        assert second.result.status == "success"
        assert second.result.output_json["plan_id"] == committed_ids[0]
    state = snapshot(business, record_property)
    assert len(committed_ids) == len(state["training_plans"]) == 1


def test_fatigue_checkin_adjusts_real_plan_and_preserves_equipment(business, record_property):
    business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    outcome = business.service.record_daily_checkin(
        DailyCheckinRequest(user_id=business.user_id, sleep_hours=4, fatigue=9)
    )
    state = snapshot(business, record_property)
    assert outcome["auto_adjusted"]
    active = [row for row in state["training_plans"] if row["status"] == "active"]
    archived = [row for row in state["training_plans"] if row["status"] == "archived"]
    assert len(active) == len(archived) == 1
    for day in active[0]["plan_json"]["training_days"]:
        assert day["equipment"] == ["dumbbells"]
        assert all(exercise["sets"] == 2 for exercise in day["exercises"])
    assert len(state["daily_checkins"]) == len(state["recovery_logs"]) == 1


def test_checkin_failure_rolls_back_nested_plan_adjustment(business, record_property, monkeypatch):
    first = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    first_id = first.id
    before = snapshot(business, record_property)

    def fail_after_adjustment(*_args, **_kwargs):
        raise RuntimeError("injected after nested plan adjustment before outer commit")

    monkeypatch.setattr(AgentTaskStateService, "update_from_checkin", fail_after_adjustment)
    with pytest.raises(RuntimeError, match="injected after nested"):
        business.service.record_daily_checkin(
            DailyCheckinRequest(user_id=business.user_id, sleep_hours=4, fatigue=9)
        )
    business.db.rollback()
    after = snapshot(business, record_property)
    assert [(row["id"], row["status"]) for row in after["training_plans"]] == [(first_id, "active")]
    assert after["daily_checkins"] == after["recovery_logs"] == []
    assert len(after["long_term_memories"]) == len(before["long_term_memories"])
    for table in [
        "agent_decisions",
        "decision_evaluation_plans",
        "agent_task_states",
        "agent_task_events",
    ]:
        assert len(after[table]) == len(before[table])


def test_standalone_plan_adjustment_still_commits(business, record_property):
    first = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    first_id = first.id
    adjusted = business.service.adjust_plan(
        PlanAdjustRequest(user_id=business.user_id, reason="synthetic user request")
    )
    adjusted_id = adjusted.id
    business.db.rollback()
    state = snapshot(business, record_property)
    assert {row["id"]: row["status"] for row in state["training_plans"]} == {
        first_id: "archived",
        adjusted_id: "active",
    }


@pytest.mark.parametrize("new_sleep", [8.0, None])
def test_partial_checkin_correction_preserves_unspecified_fields(
    business, record_property, new_sleep
):
    first = business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=date(2026, 9, 20),
            sleep_hours=7,
            fatigue=3,
            soreness=2,
            stress=4,
            notes="preserve this note",
        )
    )
    second = business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id, checkin_date=date(2026, 9, 20), sleep_hours=new_sleep
        )
    )
    state = snapshot(business, record_property)
    assert first["checkin_id"] == second["checkin_id"]
    assert len(state["daily_checkins"]) == len(state["recovery_logs"]) == 1
    checkin = state["daily_checkins"][0]
    recovery = state["recovery_logs"][0]
    assert checkin["sleep_hours"] == recovery["sleep_hours"] == new_sleep
    assert checkin["fatigue"] == recovery["fatigue_score"] == 3
    assert checkin["soreness"] == recovery["soreness_score"] == 2
    assert checkin["stress"] == recovery["stress_score"] == 4
    assert checkin["notes"] == recovery["notes"] == "preserve this note"


def _daily_state_memories(business, user_id):
    return list(
        business.db.scalars(
            select(models.LongTermMemory)
            .where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.memory_type == "recent_state",
                models.LongTermMemory.source == "daily_checkin",
            )
            .order_by(models.LongTermMemory.created_at)
        )
    )


def test_same_day_checkin_versions_memory_and_excludes_old_state_from_context(
    business, record_property
):
    checkin_date = date(2026, 9, 20)
    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=checkin_date,
            sleep_hours=7,
            fatigue=3,
            soreness=2,
            notes="preserve this note",
        )
    )
    old = _daily_state_memories(business, business.user_id)[0]
    other = models.LongTermMemory(
        user_id=business.other_id,
        memory_type="recent_state",
        category="recovery",
        content="sleep=5h; fatigue=8/10",
        source="daily_checkin",
        status="active",
        memory_metadata={"scope_type": "daily_checkin", "checkin_date": "2026-09-20"},
    )
    business.db.add(other)
    business.db.commit()

    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=checkin_date,
            sleep_hours=8,
        )
    )
    business.db.expire_all()
    own = _daily_state_memories(business, business.user_id)
    assert len(own) == 2
    current = next(memory for memory in own if memory.status == "active")
    previous = next(memory for memory in own if memory.id == old.id)
    assert previous.status == "superseded"
    assert previous.valid_until is not None
    assert current.memory_metadata["scope_type"] == "daily_checkin"
    assert current.memory_metadata["checkin_date"] == "2026-09-20"
    assert "sleep=8.0h" in current.content
    assert "fatigue=3/10" in current.content
    assert "notes=preserve this note" in current.content
    assert business.db.get(models.LongTermMemory, other.id).status == "active"

    link = business.db.scalar(
        select(models.MemoryLink).where(
            models.MemoryLink.source_memory_id == current.id,
            models.MemoryLink.target_memory_id == previous.id,
        )
    )
    assert link is not None
    assert link.link_type == "updates"

    manager = MemoryManager(business.db, business.service.model_provider)
    active_ids = {
        item.id
        for item in manager.search_memories(
            business.user_id,
            "sleep fatigue preserve",
            top_k=10,
            memory_type="recent_state",
        )
    }
    assert current.id in active_ids
    assert previous.id not in active_ids
    audit_ids = {
        item.id
        for item in manager.search_memories(
            business.user_id,
            "sleep fatigue preserve",
            top_k=10,
            memory_type="recent_state",
            include_expired=True,
        )
    }
    assert {current.id, previous.id} <= audit_ids

    context = ContextBuilder(business.db, business.service.model_provider).build_context_packet(
        business.user_id,
        "我今天睡了多久，疲劳如何？",
        intent="recovery_check",
    )
    context_ids = {uuid.UUID(item["id"]) for item in context["relevant_memories"]}
    assert current.id in context_ids
    assert previous.id not in context_ids
    snapshot(business, record_property)


def test_clearing_same_day_checkin_removes_stale_memory_from_active_recall(
    business, record_property
):
    checkin_date = date(2026, 9, 20)
    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=checkin_date,
            sleep_hours=7,
        )
    )
    old = _daily_state_memories(business, business.user_id)[0]
    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=checkin_date,
            sleep_hours=None,
        )
    )
    business.db.expire_all()
    memories = _daily_state_memories(business, business.user_id)
    assert len(memories) == 1
    assert memories[0].id == old.id
    assert memories[0].status == "superseded"
    manager = MemoryManager(business.db, business.service.model_provider)
    assert not manager.search_memories(
        business.user_id,
        "sleep",
        top_k=10,
        memory_type="recent_state",
    )
    snapshot(business, record_property)


def test_checkin_memory_versioning_keeps_other_dates_independent(business, record_property):
    for checkin_date, sleep_hours in [
        (date(2026, 9, 19), 6.0),
        (date(2026, 9, 20), 7.0),
    ]:
        business.service.record_daily_checkin(
            DailyCheckinRequest(
                user_id=business.user_id,
                checkin_date=checkin_date,
                sleep_hours=sleep_hours,
            )
        )

    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=date(2026, 9, 20),
            sleep_hours=8,
        )
    )
    business.db.expire_all()
    memories = _daily_state_memories(business, business.user_id)
    active_by_date = {
        memory.memory_metadata["checkin_date"]: memory
        for memory in memories
        if memory.status == "active"
    }
    assert set(active_by_date) == {"2026-09-19", "2026-09-20"}
    assert "sleep=6.0h" in active_by_date["2026-09-19"].content
    assert "sleep=8.0h" in active_by_date["2026-09-20"].content
    assert len([memory for memory in memories if memory.status == "superseded"]) == 1
    snapshot(business, record_property)


def test_checkin_memory_versioning_rolls_back_as_one_transaction(
    business, record_property, monkeypatch
):
    checkin_date = date(2026, 9, 20)
    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=checkin_date,
            sleep_hours=7,
            fatigue=3,
        )
    )
    old = _daily_state_memories(business, business.user_id)[0]

    def fail_after_memory_versioning(*_args, **_kwargs):
        raise RuntimeError("injected after memory versioning before outer commit")

    monkeypatch.setattr(AgentTaskStateService, "update_from_checkin", fail_after_memory_versioning)
    with pytest.raises(RuntimeError, match="after memory versioning"):
        business.service.record_daily_checkin(
            DailyCheckinRequest(
                user_id=business.user_id,
                checkin_date=checkin_date,
                sleep_hours=8,
            )
        )
    business.db.rollback()
    business.db.expire_all()
    memories = _daily_state_memories(business, business.user_id)
    assert len(memories) == 1
    assert memories[0].id == old.id
    assert memories[0].status == "active"
    assert memories[0].valid_until is None
    snapshot(business, record_property)


def test_repeated_checkin_request_key_replays_without_duplicate_side_effects(
    business, record_property
):
    request = DailyCheckinRequest(
        user_id=business.user_id,
        checkin_date=date(2026, 9, 20),
        sleep_hours=7,
        fatigue=3,
        idempotency_key="daily-checkin-001",
    )
    first = business.service.record_daily_checkin(request)
    second = business.service.record_daily_checkin(request)
    state = snapshot(business, record_property)
    assert first["idempotent_replay"] is False
    assert second == {**first, "idempotent_replay": True}
    assert len(state["daily_checkins"]) == 1
    assert len(_daily_state_memories(business, business.user_id)) == 1
    assert len(state["idempotency_records"]) == 1


def test_checkin_request_key_rejects_different_payload(business, record_property):
    first = DailyCheckinRequest(
        user_id=business.user_id,
        checkin_date=date(2026, 9, 20),
        sleep_hours=7,
        idempotency_key="daily-checkin-conflict",
    )
    business.service.record_daily_checkin(first)
    with pytest.raises(IdempotencyConflictError, match="different request"):
        business.service.record_daily_checkin(
            DailyCheckinRequest(
                user_id=business.user_id,
                checkin_date=date(2026, 9, 20),
                sleep_hours=8,
                idempotency_key="daily-checkin-conflict",
            )
        )
    state = snapshot(business, record_property)
    assert state["daily_checkins"][0]["sleep_hours"] == 7
    assert len(_daily_state_memories(business, business.user_id)) == 1
    assert len(state["idempotency_records"]) == 1


def test_checkin_request_key_is_scoped_per_user(business, record_property):
    for user_id, sleep_hours in [
        (business.user_id, 7.0),
        (business.other_id, 5.0),
    ]:
        result = business.service.record_daily_checkin(
            DailyCheckinRequest(
                user_id=user_id,
                checkin_date=date(2026, 9, 20),
                sleep_hours=sleep_hours,
                idempotency_key="shared-client-key",
            )
        )
        assert result["idempotent_replay"] is False
    state = snapshot(business, record_property)
    assert len(state["daily_checkins"]) == 2
    assert len(state["idempotency_records"]) == 2
    assert len(_daily_state_memories(business, business.user_id)) == 1
    assert len(_daily_state_memories(business, business.other_id)) == 1


def test_failed_checkin_does_not_consume_request_key(business, record_property, monkeypatch):
    original = AgentTaskStateService.update_from_checkin

    def fail_before_commit(*_args, **_kwargs):
        raise RuntimeError("injected before checkin commit")

    monkeypatch.setattr(AgentTaskStateService, "update_from_checkin", fail_before_commit)
    request = DailyCheckinRequest(
        user_id=business.user_id,
        checkin_date=date(2026, 9, 20),
        sleep_hours=7,
        idempotency_key="daily-checkin-retry-after-failure",
    )
    with pytest.raises(RuntimeError, match="before checkin commit"):
        business.service.record_daily_checkin(request)
    business.db.rollback()
    assert not business.db.scalars(select(models.IdempotencyRecord)).all()

    monkeypatch.setattr(AgentTaskStateService, "update_from_checkin", original)
    result = business.service.record_daily_checkin(request)
    state = snapshot(business, record_property)
    assert result["idempotent_replay"] is False
    assert len(state["daily_checkins"]) == 1
    assert len(state["idempotency_records"]) == 1


def test_committed_checkin_response_can_be_replayed_in_fresh_service(business, record_property):
    request = DailyCheckinRequest(
        user_id=business.user_id,
        checkin_date=date(2026, 9, 20),
        sleep_hours=7,
        idempotency_key="daily-checkin-response-lost",
    )
    first = business.service.record_daily_checkin(request)
    with Session(business.engine) as db:
        fresh_service = CoachAgentService(db, business.service.model_provider)
        replayed = fresh_service.record_daily_checkin(request)
    state = snapshot(business, record_property)
    assert replayed == {**first, "idempotent_replay": True}
    assert len(state["daily_checkins"]) == 1
    assert len(_daily_state_memories(business, business.user_id)) == 1


def test_joint_checkin_correction_adjustment_and_retry_has_one_final_state(
    business, record_property
):
    original_plan = business.service.generate_plan(PlanGenerateRequest(user_id=business.user_id))
    business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=date(2026, 9, 20),
            sleep_hours=7,
            fatigue=3,
            idempotency_key="joint-normal-state",
        )
    )
    correction = DailyCheckinRequest(
        user_id=business.user_id,
        checkin_date=date(2026, 9, 20),
        sleep_hours=4,
        fatigue=9,
        idempotency_key="joint-high-fatigue-correction",
    )
    corrected = business.service.record_daily_checkin(correction)
    before_retry = snapshot(business, record_property)
    replayed = business.service.record_daily_checkin(correction)
    after_retry = snapshot(business, record_property)

    assert corrected["auto_adjusted"] is True
    assert replayed == {**corrected, "idempotent_replay": True}
    plans = {row["id"]: row["status"] for row in after_retry["training_plans"]}
    assert plans[original_plan.id] == "archived"
    assert list(plans.values()).count("active") == 1
    memories = _daily_state_memories(business, business.user_id)
    assert len(memories) == 2
    current = next(memory for memory in memories if memory.status == "active")
    previous = next(memory for memory in memories if memory.status == "superseded")
    assert "sleep=4.0h" in current.content
    assert "fatigue=9/10" in current.content
    context = ContextBuilder(business.db, business.service.model_provider).build_context_packet(
        business.user_id,
        "我今天的睡眠和疲劳状态如何？",
        intent="recovery_check",
    )
    context_ids = {uuid.UUID(item["id"]) for item in context["relevant_memories"]}
    assert current.id in context_ids
    assert previous.id not in context_ids
    for table in [
        "training_plans",
        "daily_checkins",
        "recovery_logs",
        "long_term_memories",
        "agent_decisions",
        "agent_task_states",
        "agent_task_events",
        "idempotency_records",
    ]:
        assert len(after_retry[table]) == len(before_retry[table])


def workout_request(business, idempotency_key=None):
    return WorkoutLogRequest(
        user_id=business.user_id,
        idempotency_key=idempotency_key,
        performed_at=datetime(2026, 9, 20, 10),
        workout_name="synthetic dumbbell session",
        rpe=5,
        exercises=[{"name": "dumbbell row", "sets": [{"reps": 10, "weight": 12}]}],
    )


def test_repeated_workout_request_key_replays_without_duplicate_records(business, record_property):
    request = workout_request(business, idempotency_key="workout-001")
    first = business.service.record_workout_log(request)
    first_replay_flag = getattr(first, "_idempotent_replay", False)
    second = business.service.record_workout_log(request)
    state = snapshot(business, record_property)
    assert second.id == first.id
    assert first_replay_flag is False
    assert getattr(second, "_idempotent_replay", False) is True
    assert len(state["workout_logs"]) == 1
    assert len(state["workout_sessions"]) == 1
    assert len(state["exercise_logs"]) == 1
    assert len(state["long_term_memories"]) == 1


def test_workout_request_key_rejects_different_payload(business, record_property):
    first = workout_request(business, idempotency_key="workout-conflict")
    business.service.record_workout_log(first)
    changed = workout_request(business, idempotency_key="workout-conflict")
    changed.rpe = 8
    with pytest.raises(IdempotencyConflictError, match="different request"):
        business.service.record_workout_log(changed)
    state = snapshot(business, record_property)
    assert len(state["workout_logs"]) == 1
    assert state["workout_logs"][0]["rpe"] == 5
    assert len(state["idempotency_records"]) == 1


def test_failed_workout_does_not_consume_request_key(business, record_property, monkeypatch):
    original = DecisionEvaluationService.on_user_event

    def fail_before_commit(*_args, **_kwargs):
        raise RuntimeError("injected after workout writes before commit")

    monkeypatch.setattr(DecisionEvaluationService, "on_user_event", fail_before_commit)
    request = workout_request(business, idempotency_key="workout-retry-after-failure")
    with pytest.raises(RuntimeError, match="after workout writes"):
        business.service.record_workout_log(request)
    business.db.rollback()
    for model in [
        models.WorkoutLog,
        models.WorkoutSession,
        models.ExerciseLog,
        models.LongTermMemory,
        models.IdempotencyRecord,
    ]:
        assert not business.db.scalars(select(model)).all()

    monkeypatch.setattr(DecisionEvaluationService, "on_user_event", original)
    result = business.service.record_workout_log(request)
    state = snapshot(business, record_property)
    assert getattr(result, "_idempotent_replay", False) is False
    assert len(state["workout_logs"]) == 1
    assert len(state["idempotency_records"]) == 1


def test_committed_workout_response_can_be_replayed_in_fresh_service(business, record_property):
    request = workout_request(business, idempotency_key="workout-response-lost")
    first = business.service.record_workout_log(request)
    with Session(business.engine) as db:
        fresh_service = CoachAgentService(db, business.service.model_provider)
        replayed = fresh_service.record_workout_log(request)
        assert replayed.id == first.id
        assert getattr(replayed, "_idempotent_replay", False) is True
    state = snapshot(business, record_property)
    assert len(state["workout_logs"]) == 1
    assert len(state["workout_sessions"]) == 1
    assert len(state["exercise_logs"]) == 1
    assert len(state["long_term_memories"]) == 1


def test_distinct_workout_request_keys_preserve_two_real_sessions(business, record_property):
    first = business.service.record_workout_log(
        workout_request(business, idempotency_key="workout-session-a")
    )
    second = business.service.record_workout_log(
        workout_request(business, idempotency_key="workout-session-b")
    )
    state = snapshot(business, record_property)
    assert first.id != second.id
    assert len(state["workout_logs"]) == 2
    assert len(state["workout_sessions"]) == 2
    assert len(state["exercise_logs"]) == 2
    assert len(state["long_term_memories"]) == 2
    assert len(state["idempotency_records"]) == 2


def test_idempotency_key_is_isolated_by_business_operation(business, record_property):
    shared_key = "shared-across-operations"
    checkin = business.service.record_daily_checkin(
        DailyCheckinRequest(
            user_id=business.user_id,
            checkin_date=date(2026, 9, 20),
            sleep_hours=7,
            idempotency_key=shared_key,
        )
    )
    workout = business.service.record_workout_log(
        workout_request(business, idempotency_key=shared_key)
    )
    state = snapshot(business, record_property)
    assert checkin["idempotent_replay"] is False
    assert getattr(workout, "_idempotent_replay", False) is False
    assert {row["operation"] for row in state["idempotency_records"]} == {
        "daily_checkin",
        "workout_log",
    }


def test_real_workout_commit_then_response_lost_is_not_retried(business, record_property):
    registry = ToolRegistry()
    calls = []
    request = workout_request(business, idempotency_key="tool-response-lost")

    def handler(_payload):
        calls.append(True)
        business.service.record_workout_log(request)
        raise TimeoutError("injected after actual service commit")

    registry.register(
        ToolSpec(
            name="workout.record",
            description="Test adapter around real workout service",
            permission_level="write",
            side_effects=True,
            retry_count=2,
        ),
        handler,
    )
    result = execute(registry, "workout.record", {}, record_property)
    business.db.rollback()
    state = snapshot(business, record_property)
    assert result.result.status == "outcome_unknown"
    assert result.completed_event["status"] == "failed"
    assert len(calls) == 1
    assert len(state["workout_logs"]) == len(state["workout_sessions"]) == 1
    assert len(state["exercise_logs"]) == 1
    assert state["exercise_logs"][0]["weight"] == 12
    with Session(business.engine) as db:
        recovered = CoachAgentService(db, business.service.model_provider).record_workout_log(
            request
        )
        assert recovered.id == state["workout_logs"][0]["id"]
        assert getattr(recovered, "_idempotent_replay", False) is True


def test_workout_requests_without_identity_remain_distinct(business, record_property):
    request = workout_request(business)
    business.service.record_workout_log(request)
    business.service.record_workout_log(request)
    state = snapshot(business, record_property)
    assert len(state["workout_logs"]) == len(state["workout_sessions"]) == 2
    record_property("boundary", "requests without an idempotency key remain distinct")


def seed_risk(business, user_id):
    memory = models.LongTermMemory(
        user_id=user_id,
        memory_type="risk_signal",
        category="risk",
        content="shoulder injury",
        source="synthetic_fixture",
        status="active",
    )
    risk = models.RiskNote(
        user_id=user_id, body_part="shoulder", risk_type="injury", description="shoulder injury"
    )
    business.db.add_all([memory, risk])
    business.db.commit()
    return memory.id, risk.id


@pytest.mark.parametrize("accepted", [True, False])
def test_real_memory_correction_respects_verification_and_user_scope(
    business, record_property, accepted
):
    memory_id, risk_id = seed_risk(business, business.user_id)
    other_memory, other_risk = seed_risk(business, business.other_id)
    extraction = business.service._rule_profile_extraction("我的右肩没有伤")
    registry = chat_registry(business, "我的右肩没有伤")
    verified = execute(registry, "memory.verify", {"extraction": extraction}, record_property)
    assert verified.result.status == "success"
    verification = verified.result.output_json
    assert verification["accepted_corrections"]
    if not accepted:
        # Inject an explicit denial to check the write boundary, not verifier accuracy.
        verification = {**verification, "accepted_corrections": [], "accepted_candidates": []}
    result = execute(
        registry,
        "memory.write",
        {"extraction": extraction, "verification": verification},
        record_property,
    )
    business.db.commit()
    state = snapshot(business, record_property)
    assert result.result.status == "success"
    memories = {row["id"]: row["status"] for row in state["long_term_memories"]}
    risks = {row["id"]: row["status"] for row in state["risk_notes"]}
    assert memories[memory_id] == ("superseded" if accepted else "active")
    assert risks[risk_id] == ("corrected" if accepted else "active")
    assert memories[other_memory] == risks[other_risk] == "active"
    if not accepted:
        assert result.result.output_json["written"] == []


@pytest.mark.parametrize("verification", [{}, {"accepted_candidates": []}])
def test_incomplete_verification_does_not_restore_raw_corrections(
    business, record_property, verification
):
    memory_id, _ = seed_risk(business, business.user_id)
    message = "我的右肩没有伤"
    result = execute(
        chat_registry(business, message),
        "memory.write",
        {
            "extraction": business.service._rule_profile_extraction(message),
            "verification": verification,
        },
        record_property,
    )
    business.db.commit()
    state = snapshot(business, record_property)
    assert result.result.output_json["written"] == []
    assert (
        next(row for row in state["long_term_memories"] if row["id"] == memory_id)["status"]
        == "active"
    )


def process_correction(business, message, record_property):
    extraction = business.service._rule_profile_extraction(message)
    business.service._apply_profile_extraction(business.profile, extraction)
    registry = chat_registry(business, message)
    verified = execute(registry, "memory.verify", {"extraction": extraction}, record_property)
    written = execute(
        registry,
        "memory.write",
        {"extraction": extraction, "verification": verified.result.output_json},
        record_property,
    )
    business.db.commit()
    return extraction, verified.result.output_json, written.result.output_json


def test_injury_correction_propagates_to_search_context_catalog_and_links(
    business, record_property
):
    business.profile.injuries = ["shoulder"]
    old_memory_id, old_risk_id = seed_risk(business, business.user_id)
    other_memory_id, other_risk_id = seed_risk(business, business.other_id)
    manager = MemoryManager(business.db, business.service.model_provider)
    manager.update_memory_catalog(business.user_id, "risk")
    manager.update_memory_blocks(business.user_id)
    business.db.commit()

    before = manager.search_memories(business.user_id, "shoulder injury", top_k=10)
    assert old_memory_id in {item.id for item in before}
    extraction, verification, written = process_correction(
        business, "我的右肩没有伤", record_property
    )
    assert extraction["corrections"] == verification["accepted_corrections"]
    assert written["written"]

    state = snapshot(business, record_property)
    memories = {row["id"]: row for row in state["long_term_memories"]}
    risks = {row["id"]: row for row in state["risk_notes"]}
    assert memories[old_memory_id]["status"] == "superseded"
    assert memories[old_memory_id]["valid_until"] is not None
    assert risks[old_risk_id]["status"] == "corrected"
    assert risks[old_risk_id]["valid_until"] is not None
    assert memories[other_memory_id]["status"] == risks[other_risk_id]["status"] == "active"
    own_profile = next(row for row in state["user_profiles"] if row["user_id"] == business.user_id)
    assert own_profile["injuries"] == []

    active_ids = {
        item.id for item in manager.search_memories(business.user_id, "shoulder injury", top_k=10)
    }
    assert old_memory_id not in active_ids
    audit_ids = {
        item.id
        for item in manager.search_memories(
            business.user_id, "shoulder injury", top_k=10, include_expired=True
        )
    }
    assert old_memory_id in audit_ids
    context = ContextBuilder(business.db, business.service.model_provider).build_context_packet(
        business.user_id, "右肩训练应该注意什么", intent="injury_or_risk"
    )
    assert str(old_memory_id) not in {item["id"] for item in context["relevant_memories"]}
    assert context["active_risk_notes"] == []
    risk_catalog = next(item for item in context["memory_catalog"] if item["category"] == "risk")
    assert risk_catalog["record_count"] == 0
    assert "shoulder injury" not in risk_catalog["summary"]
    risk_block = business.db.scalar(
        select(models.MemoryBlock).where(
            models.MemoryBlock.user_id == business.user_id,
            models.MemoryBlock.block_type == "risk",
        )
    )
    assert risk_block.content == "No active risk notes."
    correction_id = uuid.UUID(written["written"][0])
    link = business.db.scalar(
        select(models.MemoryLink).where(
            models.MemoryLink.source_memory_id == correction_id,
            models.MemoryLink.target_memory_id == old_memory_id,
        )
    )
    assert link is not None
    assert link.link_type == "contradicts"


def test_goal_correction_updates_profile_and_supersedes_old_goal_memory(business, record_property):
    business.profile.goal = "fat_loss"
    manager = MemoryManager(business.db, business.service.model_provider)
    old = manager.retain_memory(
        business.user_id,
        "用户目标是减脂。",
        "world",
        "user_profile_fact",
        category="profile",
    )
    manager.update_memory_catalog(business.user_id, "profile")
    business.db.commit()

    extraction, verification, written = process_correction(
        business, "不对，我的目标改了，现在不是减脂，是增肌。", record_property
    )
    assert extraction["profile_patch"]["goal"] == "muscle_gain"
    assert {item["field"] for item in verification["accepted_corrections"]} == {"goal"}
    state = snapshot(business, record_property)
    old_row = next(item for item in state["long_term_memories"] if item["id"] == old.id)
    assert old_row["status"] == "superseded"
    assert old_row["valid_until"] is not None
    profile = next(item for item in state["user_profiles"] if item["user_id"] == business.user_id)
    assert profile["goal"] == "muscle_gain"

    context = ContextBuilder(business.db, business.service.model_provider).build_context_packet(
        business.user_id, "给我新的训练计划", intent="training_plan"
    )
    assert context["core_profile"]["goal"] == "muscle_gain"
    assert str(old.id) not in {item["id"] for item in context["relevant_memories"]}
    profile_catalog = next(
        item for item in context["memory_catalog"] if item["category"] == "profile"
    )
    assert "用户目标是减脂" not in profile_catalog["summary"]
    correction_id = uuid.UUID(written["written"][0])
    assert business.db.scalar(
        select(models.MemoryLink).where(
            models.MemoryLink.source_memory_id == correction_id,
            models.MemoryLink.target_memory_id == old.id,
        )
    )


@pytest.mark.parametrize(
    "previous,new_goal,message",
    [
        ("fat_loss", "muscle_gain", "档案里把我的目标写成了减脂，这是错的；请改为增肌。"),
        ("muscle_gain", "fat_loss", "档案里把我的目标写成了增肌，这是错的；请改为减脂。"),
    ],
)
def test_explicit_goal_replacement_invalidates_old_memory(
    business, record_property, previous, new_goal, message
):
    business.profile.goal = previous
    manager = MemoryManager(business.db, business.service.model_provider)
    old = manager.retain_memory(
        business.user_id,
        f"用户目标是{'减脂' if previous == 'fat_loss' else '增肌'}。",
        "world",
        "user_profile_fact",
        category="profile",
    )
    other = manager.retain_memory(
        business.other_id,
        "其他用户的旧目标。",
        "world",
        "user_profile_fact",
        category="profile",
    )
    business.db.commit()

    extraction, verification, written = process_correction(business, message, record_property)
    assert extraction["profile_patch"]["goal"] == new_goal
    assert {item["field"] for item in verification["accepted_corrections"]} == {"goal"}
    assert written["written"]
    business.db.refresh(old)
    business.db.refresh(other)
    assert business.profile.goal == new_goal
    assert old.status == "superseded"
    assert other.status == "active"
    context = ContextBuilder(business.db, business.service.model_provider).build_context_packet(
        business.user_id, "按我当前目标安排训练", intent="training_plan"
    )
    assert context["core_profile"]["goal"] == new_goal
    assert str(old.id) not in {item["id"] for item in context["relevant_memories"]}
    active_non_corrections = business.db.scalars(
        select(models.LongTermMemory).where(
            models.LongTermMemory.user_id == business.user_id,
            models.LongTermMemory.status == "active",
            models.LongTermMemory.memory_type != "correction",
        )
    ).all()
    old_term = "减脂" if previous == "fat_loss" else "增肌"
    assert not any(old_term in memory.content for memory in active_non_corrections)


@pytest.mark.parametrize(
    "message",
    [
        "档案目标写错了，请改为增肌，或者改为维持。",
        "档案目标写错了，请改为不增肌。",
    ],
)
def test_unresolved_goal_replacement_does_not_guess_profile_patch(business, message):
    extraction = business.service._rule_profile_extraction(message)
    assert "goal" not in extraction["profile_patch"]
    assert not [item for item in extraction["corrections"] if item["field"] == "goal"]
    assert any(item["field"] == "goal" for item in extraction["ignored_candidates"])
