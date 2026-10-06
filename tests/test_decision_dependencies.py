"""Correction applicability, independent owners and immutable historical results."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.decision_dependencies import DecisionDependencyService
from fast_api.app.services.decision_evaluation import DecisionEvaluationService
from fast_api.app.services.decision_logger import DecisionLogger
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.outcome_reflection_service import OutcomeReflectionService


@pytest.fixture
def dependency_state():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="dependencies@example.test", password_hash="synthetic")
        other = models.User(email="other-dependencies@example.test", password_hash="synthetic")
        db.add_all([user, other])
        db.flush()
        profile = models.UserProfile(user_id=user.id, goal="maintenance", injuries=[])
        db.add(profile)
        db.flush()
        yield db, user, other, profile
    engine.dispose()


def decision(db, user, context=None):
    return DecisionLogger(db).log_decision(
        user.id,
        {
            "decision_type": "training_adjustment",
            "decision_result": "reduce volume",
            "reason": "synthetic recovery check",
            "context_used": context or {},
        },
    )


def evaluation(db, item):
    return db.scalar(
        select(models.DecisionEvaluationPlan).where(
            models.DecisionEvaluationPlan.decision_id == item.id
        )
    )


def test_goal_change_stops_evaluation_followup_and_direct_reflection(dependency_state):
    db, user, _other, profile = dependency_state
    item = decision(db, user)
    plan = evaluation(db, item)
    followup = models.DecisionFollowup(
        user_id=user.id,
        evaluation_plan_id=plan.id,
        question_type="strategy_execution",
        question_payload={},
        scheduled_at=datetime.utcnow(),
        status="pending",
    )
    db.add(followup)
    db.commit()
    profile.goal = "muscle_gain"
    service = DecisionDependencyService(db)
    assert service.invalidate_changed(user.id) == [str(item.id)]
    assert plan.status == "invalidated"
    assert followup.status == "cancelled"
    assert DecisionEvaluationService(db).next_followup_for_delivery(user.id) is None
    with pytest.raises(ValueError, match="no longer applicable"):
        DecisionEvaluationService(db).answer_followup(
            followup.id, user.id, {"implementation_status": "implemented"}
        )
    assert followup.answer_json == {}
    assert (
        OutcomeReflectionService(db).reflect_decision(item.id, "implemented")["reason"]
        == "dependencies_changed"
    )
    assert DecisionEvaluationService(db).refresh_plan(plan, "manual")["status"] == "invalidated"
    profile.goal = "maintenance"
    assert service.invalidate_changed(user.id) == []
    assert service.invalidated(item)  # Matching an old value cannot resurrect history.


def test_completed_history_preserved_but_derived_memory_not_active(dependency_state):
    db, user, _other, profile = dependency_state
    item = decision(db, user)
    plan = evaluation(db, item)
    plan.status = "completed"
    plan.outcome_status = "improved"
    memory = models.LongTermMemory(
        user_id=user.id,
        memory_type="experience",
        category="training",
        content="old strategy evidence",
        status="active",
    )
    db.add(memory)
    db.flush()
    outcome = models.DecisionOutcome(
        user_id=user.id,
        decision_id=item.id,
        outcome_type="training_outcome",
        outcome_status="improved",
        outcome_summary="historical improvement",
        metrics={"score": 7},
        evidence=[{"source": "original"}],
        reflected_memory_id=memory.id,
    )
    db.add(outcome)
    db.commit()
    profile.goal = "fat_loss"
    DecisionDependencyService(db).invalidate_changed(user.id)
    assert plan.status == "completed" and plan.outcome_status == "improved"
    assert outcome.outcome_status == "improved" and outcome.metrics["score"] == 7
    assert outcome.evidence == [{"source": "original"}]
    assert memory.status == "superseded"
    assert (
        db.scalar(
            select(models.MemoryCatalog).where(models.MemoryCatalog.user_id == user.id)
        ).record_count
        == 0
    )


def test_new_evidence_does_not_invalidate_but_baseline_correction_does(dependency_state):
    db, user, _other, _profile = dependency_state
    baseline = models.RecoveryLog(
        user_id=user.id, log_date=datetime.utcnow().date(), fatigue_score=8
    )
    db.add(baseline)
    db.flush()
    item = decision(db, user, {"baseline_recovery_log_id": str(baseline.id)})
    db.add(
        models.RecoveryLog(
            user_id=user.id,
            log_date=(datetime.utcnow() + timedelta(days=1)).date(),
            fatigue_score=4,
        )
    )
    assert DecisionDependencyService(db).invalidate_changed(user.id) == []
    baseline.fatigue_score = 3
    assert DecisionDependencyService(db).invalidate_changed(user.id) == [str(item.id)]
    assert (
        "baseline_recovery.fatigue_score"
        in item.context_used["dependency_validity"]["changed_fields"]
    )


def test_source_correction_is_owner_scoped_and_explicit(dependency_state):
    db, user, other, _profile = dependency_state
    source_id = str(uuid.uuid4())
    item = decision(db, user, {"evidence": [{"table": "long_term_memories", "id": source_id}]})
    unrelated = decision(db, user)
    foreign = decision(db, other, {"source_id": source_id})
    service = DecisionDependencyService(db)
    assert service.invalidate_changed(user.id, [source_id]) == [str(item.id)]
    assert not service.invalidated(unrelated)
    assert not service.invalidated(foreign)


def test_direct_memory_correction_revokes_decision_and_pending_followup(dependency_state):
    db, user, _other, _profile = dependency_state
    manager = MemoryManager(db)
    old = manager.retain_memory(
        user.id, "用户喜欢晨练。", "world", "user_preference", category="preference"
    )
    item = decision(db, user, {"evidence": [{"table": "long_term_memories", "id": str(old.id)}]})
    plan = evaluation(db, item)
    followup = models.DecisionFollowup(
        user_id=user.id,
        evaluation_plan_id=plan.id,
        question_type="strategy_execution",
        question_payload={},
        scheduled_at=datetime.utcnow(),
        status="pending",
    )
    db.add(followup)
    db.commit()
    corrected = manager.add_memory(
        user.id,
        {
            "content": "不对，我的训练时间偏好改了，现在是晚练。",
            "category": "preference",
            "corrected_memory_ids": [old.id],
        },
    )

    assert corrected.id != old.id
    assert old.status == "superseded"
    assert DecisionDependencyService.invalidated(item)
    assert "source.corrected" in item.context_used["dependency_validity"]["changed_fields"]
    assert plan.status == "invalidated"
    assert followup.status == "cancelled"
    assert DecisionEvaluationService(db).next_followup_for_delivery(user.id) is None


def test_memory_correction_requires_owned_explicit_target_and_preserves_unrelated(
    dependency_state,
):
    db, user, other, _profile = dependency_state
    manager = MemoryManager(db)
    old = manager.retain_memory(
        user.id, "用户喜欢晨练。", "world", "user_preference", category="preference"
    )
    unrelated = manager.retain_memory(
        user.id, "用户偏好低强度训练。", "world", "user_preference", category="preference"
    )
    foreign = manager.retain_memory(
        other.id, "其他用户喜欢晨练。", "world", "user_preference", category="preference"
    )
    db.commit()
    correction = {"content": "不对，我的训练偏好改了，现在是晚练。", "category": "preference"}

    with pytest.raises(ValueError, match="explicit corrected_memory_ids"):
        manager.add_memory(user.id, correction)
    with pytest.raises(ValueError, match="owned by this user"):
        manager.add_memory(user.id, {**correction, "corrected_memory_ids": [foreign.id]})
    assert old.status == unrelated.status == foreign.status == "active"

    manager.add_memory(user.id, {**correction, "corrected_memory_ids": [old.id]})
    assert old.status == "superseded"
    assert unrelated.status == foreign.status == "active"


def test_dependency_invalidation_rolls_back_with_outer_transaction(dependency_state):
    db, user, _other, profile = dependency_state
    item = decision(db, user)
    db.commit()
    profile.goal = "fat_loss"
    DecisionDependencyService(db).invalidate_changed(user.id)
    db.rollback()
    assert not DecisionDependencyService.invalidated(item)
    assert evaluation(db, item).status == "scheduled"


def test_new_risk_evidence_stops_old_evaluation(dependency_state):
    db, user, _other, _profile = dependency_state
    item = decision(db, user)
    db.add(
        models.RiskNote(
            user_id=user.id,
            risk_type="injury",
            description="synthetic new pain",
            severity_score=0.8,
            status="active",
        )
    )
    assert DecisionDependencyService(db).invalidate_changed(user.id) == [str(item.id)]
    assert evaluation(db, item).status == "invalidated"
