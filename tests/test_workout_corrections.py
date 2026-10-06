import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.core.errors import IdempotencyConflictError, ResourceNotFoundError
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.schemas.agent import WorkoutCorrectionRequest, WorkoutLogRequest
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.workout_corrections import correct_workout, list_workouts


@pytest.fixture
def recorded():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = uuid.uuid4()
        db.add(models.User(id=owner, email="correct@example.test", password_hash="none"))
        db.commit()
        provider = ModelProvider(
            Settings(_env_file=None, LLM_PROVIDER="offline", EMBEDDING_PROVIDER="offline")
        )
        service = CoachAgentService(db, provider)
        log = service.record_workout_log(
            WorkoutLogRequest(user_id=owner, workout_name="慢跑", duration_minutes=30, rpe=6)
        )
        yield db, service, owner, log.id
    engine.dispose()


def request(**changes):
    return WorkoutCorrectionRequest(
        **{
            "idempotency_key": "correct-one",
            "expected_revision": 0,
            "expected": {"duration_minutes": 30, "rpe": 6},
            "changes": {"duration_minutes": 20, "rpe": 4},
            "reason": "核对手表后更正实际时长与整体强度",
            **changes,
        }
    )


def source_for(db, log_id):
    return db.scalar(
        select(models.LongTermMemory).where(
            models.LongTermMemory.memory_metadata["canonical_workout_source"]
            .as_boolean()
            .is_(True),
            models.LongTermMemory.memory_metadata["workout_log_id"].as_string() == str(log_id),
        )
    )


def test_correction_updates_canonical_facts_preserves_original_and_replays(recorded):
    db, service, owner, log_id = recorded
    source = source_for(db, log_id)
    old_memory_id = uuid.UUID(source.memory_metadata["current"]["memory_id"])
    session_id = uuid.UUID(source.memory_metadata["current"]["session_id"])
    result = correct_workout(service, owner, log_id, request())
    assert result["before"] == {"duration_minutes": 30, "rpe": 6}
    assert result["after"] == {"duration_minutes": 20, "rpe": 4}
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 20
    assert db.get(models.WorkoutSession, session_id).fatigue_score == 4
    assert db.get(models.LongTermMemory, old_memory_id).status == "superseded"
    assert source.memory_metadata["original_facts"]["duration_minutes"] == 30
    assert source.memory_metadata["current"]["revision"] == 1
    replay = correct_workout(service, owner, log_id, request())
    assert replay["idempotent_replay"] and replay["audit_id"] == result["audit_id"]
    assert db.scalar(select(func.count(models.WorkoutLog.id))) == 1
    assert (
        db.scalar(
            select(func.count(models.IdempotencyRecord.id)).where(
                models.IdempotencyRecord.operation == "workout_correction"
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"expected_revision": 2},
        {"expected": {"duration_minutes": 29, "rpe": 6}},
        {"changes": {"duration_minutes": 30, "rpe": 6}},
    ],
)
def test_stale_or_noop_correction_does_not_change_facts(recorded, patch):
    db, service, owner, log_id = recorded
    with pytest.raises(IdempotencyConflictError):
        correct_workout(service, owner, log_id, request(**patch))
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 30
    assert source_for(db, log_id).memory_metadata["current"]["revision"] == 0
    assert not db.scalar(
        select(models.IdempotencyRecord).where(
            models.IdempotencyRecord.operation == "workout_correction"
        )
    )


def test_cross_owner_cannot_correct_or_replay(recorded):
    db, service, owner, log_id = recorded
    other = uuid.uuid4()
    db.add(models.User(id=other, email="other@example.test", password_hash="none"))
    db.commit()
    correct_workout(service, owner, log_id, request())
    with pytest.raises(ResourceNotFoundError):
        correct_workout(service, other, log_id, request())
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 20


def test_unassociated_historical_record_is_not_guessed(recorded):
    db, service, owner, log_id = recorded
    historical = models.WorkoutLog(user_id=owner, workout_name="慢跑", duration_minutes=30, rpe=6)
    db.add(historical)
    db.commit()
    with pytest.raises(IdempotencyConflictError, match="association"):
        correct_workout(service, owner, historical.id, request())
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 30


def test_failure_after_mutation_rolls_back_all_views(recorded, monkeypatch):
    db, service, owner, log_id = recorded
    old_memory_id = uuid.UUID(source_for(db, log_id).memory_metadata["current"]["memory_id"])

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure after canonical update")

    monkeypatch.setattr(service, "_write_memory", fail)
    with pytest.raises(RuntimeError, match="injected"):
        correct_workout(service, owner, log_id, request())
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 30
    assert db.get(models.LongTermMemory, old_memory_id).status == "active"
    assert source_for(db, log_id).memory_metadata["current"]["revision"] == 0


def test_corrected_source_revokes_decision_and_recursive_memories(recorded):
    from fast_api.app.services.decision_logger import DecisionLogger

    db, service, owner, log_id = recorded
    decision = DecisionLogger(db).log_decision(
        owner,
        {
            "decision_type": "training_adjustment",
            "decision_result": "reduce volume",
            "reason": "synthetic record evidence",
            "context_used": {"workout_log_id": str(log_id)},
        },
    )
    first = models.LongTermMemory(
        user_id=owner,
        memory_type="observation",
        memory_network="observation",
        category="training",
        content="derived from original workout",
        evidence=[{"table": "workout_logs", "id": str(log_id)}],
        status="active",
    )
    db.add(first)
    db.flush()
    second = models.LongTermMemory(
        user_id=owner,
        memory_type="experience",
        memory_network="experience",
        category="training",
        content="derived from first observation",
        evidence=[{"table": "long_term_memories", "id": str(first.id)}],
        status="active",
    )
    db.add(second)
    db.commit()
    result = correct_workout(service, owner, log_id, request())
    assert result["invalidated_decision_ids"] == [str(decision.id)]
    assert decision.context_used["dependency_validity"]["status"] == "invalidated"
    assert decision.decision_result == "reduce volume"  # Historical result is not relabelled.
    assert first.status == second.status == "superseded"
    plan = db.scalar(
        select(models.DecisionEvaluationPlan).where(
            models.DecisionEvaluationPlan.decision_id == decision.id
        )
    )
    assert plan.status == "invalidated"


def test_reusing_key_with_different_patch_is_rejected(recorded):
    db, service, owner, log_id = recorded
    correct_workout(service, owner, log_id, request())
    with pytest.raises(IdempotencyConflictError):
        correct_workout(service, owner, log_id, request(reason="different request"))
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 20


def test_revision_blocks_old_baseline_even_after_values_return(recorded):
    db, service, owner, log_id = recorded
    correct_workout(service, owner, log_id, request())
    correct_workout(
        service,
        owner,
        log_id,
        request(
            idempotency_key="return-to-original",
            expected_revision=1,
            expected={"duration_minutes": 20, "rpe": 4},
            changes={"duration_minutes": 30, "rpe": 6},
        ),
    )
    with pytest.raises(IdempotencyConflictError, match="revision changed"):
        correct_workout(service, owner, log_id, request(idempotency_key="stale-client"))
    assert source_for(db, log_id).memory_metadata["current"]["revision"] == 2


def test_owned_list_displays_current_revision_and_unassociated_history(recorded):
    db, service, owner, log_id = recorded
    other = uuid.uuid4()
    db.add(models.User(id=other, email="list-other@example.test", password_hash="none"))
    foreign = models.WorkoutLog(user_id=other, workout_name="foreign")
    historical = models.WorkoutLog(user_id=owner, workout_name="historical")
    db.add_all([foreign, historical])
    db.commit()
    correct_workout(service, owner, log_id, request())
    items = {row["id"]: row for row in list_workouts(db, owner)}
    assert set(items) == {str(log_id), str(historical.id)}
    assert items[str(log_id)]["revision"] == 1
    assert items[str(log_id)]["duration_minutes"] == 20
    assert items[str(log_id)]["correction_available"]
    assert not items[str(historical.id)]["correction_available"]
    assert len(list_workouts(db, owner, limit=1)) == 1


@pytest.mark.parametrize("action", ["confirm", "cancel", "stale", "other_session", "expired"])
def test_chat_proposal_requires_scoped_confirmation(recorded, action):
    from datetime import datetime, timedelta

    from fast_api.app.services.workout_correction_chat import handle_workout_correction_command

    db, service, owner, log_id = recorded
    session = service.create_session(owner, "Synthetic", "Correction")
    _, updates = handle_workout_correction_command(
        service,
        owner,
        session.id,
        f"更正训练记录 {log_id} 时长为20分钟；原因：核对手表",
    )
    confirmation_id = updates["confirmation_id"]
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 30
    db.commit()
    command = f"确认更正训练 {confirmation_id}"
    sid = session.id
    if action == "cancel":
        command = f"取消更正训练 {confirmation_id}"
    elif action == "stale":
        correct_workout(service, owner, log_id, request())
    elif action == "other_session":
        sid = service.create_session(owner, "Synthetic", "Other").id
    elif action == "expired":
        pending = db.get(models.PendingQuestion, uuid.UUID(confirmation_id))
        pending.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    _, result = handle_workout_correction_command(service, owner, sid, command)
    db.commit()
    if action == "confirm":
        assert result["workout_correction_status"] == "corrected"
        assert db.get(models.WorkoutLog, log_id).duration_minutes == 20
        _, replay = handle_workout_correction_command(service, owner, sid, command)
        assert replay["correction"]["idempotent_replay"]
        assert replay["correction"]["audit_id"] == result["correction"]["audit_id"]
    else:
        assert result["workout_correction_status"] == (
            "cancelled" if action == "cancel" else "rejected"
        )
        assert db.get(models.WorkoutLog, log_id).duration_minutes == (
            20 if action == "stale" else 30
        )


def test_ambiguous_chat_correction_never_creates_or_overwrites_record(recorded):
    from fast_api.app.services.workout_correction_chat import handle_workout_correction_command

    db, service, owner, log_id = recorded
    session = service.create_session(owner, "Synthetic", "Correction")
    _, result = handle_workout_correction_command(
        service, owner, session.id, "我之前的训练记录写错了，我刚完成20分钟慢跑，帮我记录"
    )
    assert result["workout_correction_status"] == "clarify"
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 30
    assert db.scalar(select(func.count(models.WorkoutLog.id))) == 1


def test_chat_correction_rejects_duplicate_invalid_or_foreign_target(recorded):
    from fast_api.app.services.workout_correction_chat import handle_workout_correction_command

    db, service, owner, log_id = recorded
    session = service.create_session(owner, "Synthetic", "Correction")
    for text in [
        f"更正训练记录 {log_id} 时长为0分钟；原因：核对",
        f"更正训练记录 {log_id} 时长为20分钟，时长为25分钟；原因：核对",
        f"更正训练记录 {uuid.uuid4()} 时长为20分钟；原因：核对",
    ]:
        _, result = handle_workout_correction_command(service, owner, session.id, text)
        assert result["workout_correction_status"] == "rejected"
    assert db.get(models.WorkoutLog, log_id).duration_minutes == 30


@pytest.mark.parametrize(
    "patch",
    [
        {"changes": {}},
        {"expected": {"rpe": 6}},
        {"reason": " "},
        {"changes": {"duration_minutes": 0, "rpe": 4}},
        {"changes": {"duration_minutes": 20, "rpe": 11}},
        {"changes": {"duration_minutes": 20, "rpe": 4, "user_id": "fake"}},
    ],
)
def test_request_rejects_ambiguous_or_unsupported_changes(patch):
    with pytest.raises(ValidationError):
        request(**patch)
