import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.decision_evaluation import DecisionEvaluationService, _aligned_now
from fast_api.app.services.decision_logger import DecisionLogger


def test_declined_followup_is_durable_scoped_and_not_a_failed_strategy():
    db = make_db()
    user, other = add_user(db), add_user(db)
    item = DecisionLogger(db).log_decision(
        user.id,
        {
            "decision_type": "nutrition_strategy",
            "decision_result": "synthetic",
            "reason": "test",
        },
    )
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(item)
    service.refresh_plan(plan, "test")
    followup = db.scalar(
        select(models.DecisionFollowup).where(models.DecisionFollowup.user_id == user.id)
    )
    with pytest.raises(ValueError, match="not found"):
        service.decline_followup(followup.id, other.id)
    assert followup.status == "pending"
    result = service.decline_followup(followup.id, user.id)
    db.commit()
    followup_id, user_id, plan_id, decision_id = followup.id, user.id, plan.id, item.id
    db.expunge_all()
    plan = db.get(models.DecisionEvaluationPlan, plan_id)
    assert result["status"] == "declined"
    assert plan.expected_action["followup_declined"] is True
    assert plan.implementation_status == "unknown"
    service.refresh_plan(plan, "scheduled_scan")
    assert service.next_followup_for_delivery(user_id) is None
    assert service.decline_followup(followup_id, user_id)["status"] == "declined"
    with pytest.raises(ValueError, match="no longer applicable"):
        service.answer_followup(followup_id, user_id, {"implementation_status": "implemented"})
    assert (
        db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision_id)
        )
        is None
    )


def test_evaluation_clock_matches_postgres_timezone_awareness():
    aware_reference = datetime(2026, 9, 22, tzinfo=timezone.utc)
    naive_reference = datetime(2026, 9, 22)
    naive_now = datetime(2026, 9, 23)
    aware_now = datetime(2026, 9, 23, tzinfo=timezone.utc)

    assert _aligned_now(aware_reference, naive_now).tzinfo is not None
    assert _aligned_now(naive_reference, aware_now).tzinfo is None


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def add_user(db):
    user = models.User(
        email=f"{uuid.uuid4()}@example.com",
        password_hash="test",
        display_name="Evaluation User",
    )
    db.add(user)
    db.flush()
    return user


def test_decision_logger_creates_evaluation_plan():
    db = make_db()
    user = add_user(db)

    decision = DecisionLogger(db).log_decision(
        user.id,
        {
            "decision_type": "plan_adjustment",
            "input_summary": "High fatigue",
            "context_used": {"fatigue": 8},
            "decision_result": "reduce load",
            "reason": "Protect recovery",
            "confidence_score": 0.8,
        },
    )

    plan = db.scalar(
        select(models.DecisionEvaluationPlan).where(
            models.DecisionEvaluationPlan.decision_id == decision.id
        )
    )
    assert plan is not None
    assert plan.evaluation_type == "training_adjustment"
    assert plan.status == "scheduled"
    assert plan.expected_action["requires_user_confirmation"] is True
    assert plan.minimum_evidence == {"workout_count": 1, "recovery_count": 1}


def test_refresh_plan_reloads_status_after_owner_lock():
    db = make_db()
    user = add_user(db)
    decision = DecisionLogger(db).log_decision(
        user.id,
        {
            "decision_type": "plan_adjustment",
            "decision_result": "reduce load",
            "reason": "synthetic",
        },
    )
    plan = db.scalar(
        select(models.DecisionEvaluationPlan).where(
            models.DecisionEvaluationPlan.decision_id == decision.id
        )
    )
    db.commit()
    db.execute(
        update(models.DecisionEvaluationPlan)
        .where(models.DecisionEvaluationPlan.id == plan.id)
        .values(status="completed")
        .execution_options(synchronize_session=False)
    )
    assert plan.status == "scheduled"  # Simulate a stale ORM identity-map value.

    result = DecisionEvaluationService(db).refresh_plan(plan, "scheduled_scan")

    assert result["reason"] == "evaluation_not_active"
    assert plan.status == "completed"


def test_followup_answer_reloads_cancelled_row_before_writing():
    db = make_db()
    user = add_user(db)
    decision = DecisionLogger(db).log_decision(
        user.id,
        {
            "decision_type": "plan_adjustment",
            "decision_result": "reduce load",
            "reason": "synthetic",
        },
    )
    plan = db.scalar(
        select(models.DecisionEvaluationPlan).where(
            models.DecisionEvaluationPlan.decision_id == decision.id
        )
    )
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
    db.execute(
        update(models.DecisionFollowup)
        .where(models.DecisionFollowup.id == followup.id)
        .values(status="cancelled")
        .execution_options(synchronize_session=False)
    )
    assert followup.status == "pending"

    with pytest.raises(ValueError, match="no longer applicable"):
        DecisionEvaluationService(db).answer_followup(
            followup.id, user.id, {"implementation_status": "implemented"}
        )
    assert followup.status == "cancelled"
    assert followup.answer_json == {}


def test_followup_delivery_reloads_attempt_count_and_stops_at_limit():
    db = make_db()
    user = add_user(db)
    decision = DecisionLogger(db).log_decision(
        user.id,
        {"decision_type": "plan_adjustment", "decision_result": "reduce load", "reason": "test"},
    )
    plan = db.scalar(
        select(models.DecisionEvaluationPlan).where(
            models.DecisionEvaluationPlan.decision_id == decision.id
        )
    )
    followup = models.DecisionFollowup(
        user_id=user.id,
        evaluation_plan_id=plan.id,
        question_type="strategy_execution",
        question_payload={},
        scheduled_at=datetime.utcnow() - timedelta(minutes=1),
        status="pending",
        attempt_count=0,
    )
    db.add(followup)
    db.commit()
    db.execute(
        update(models.DecisionFollowup)
        .where(models.DecisionFollowup.id == followup.id)
        .values(attempt_count=1)
        .execution_options(synchronize_session=False)
    )
    assert followup.attempt_count == 0

    service = DecisionEvaluationService(db)
    assert service.next_followup_for_delivery(user.id) is not None
    assert followup.attempt_count == 2
    assert service.next_followup_for_delivery(user.id) is None


def test_event_evidence_creates_followup_then_answer_reflects_outcome():
    db = make_db()
    user = add_user(db)
    decision_time = datetime.utcnow() - timedelta(days=2)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="training_adjustment",
        input_summary="Fatigue elevated",
        context_used={"fatigue": 8},
        decision_result="reduce load and keep pain-free movement",
        reason="Recovery was poor",
        confidence_score=0.82,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    db.add(
        models.WorkoutLog(
            user_id=user.id,
            performed_at=decision_time + timedelta(days=1),
            workout_name="Reduced load lower body",
            rpe=6,
            completion_rate=0.9,
        )
    )
    db.add(
        models.RecoveryLog(
            user_id=user.id,
            log_date=(decision_time + timedelta(days=1)).date(),
            sleep_hours=7.5,
            fatigue_score=4,
        )
    )
    db.add(
        models.SymptomLog(
            user_id=user.id,
            symptom_date=(decision_time + timedelta(days=1)).date(),
            symptom_type="knee pain",
            severity_score=2,
            status="monitoring",
        )
    )
    db.flush()

    result = service.refresh_plan(plan, trigger_type="workout_logged")
    assert result["status"] == "waiting_user"
    followup = db.scalar(
        select(models.DecisionFollowup).where(models.DecisionFollowup.evaluation_plan_id == plan.id)
    )
    assert followup is not None
    assert followup.question_type == "strategy_execution"

    service.answer_followup(
        followup.id,
        user.id,
        {
            "implementation_status": "implemented",
            "subjective_outcome": "improved",
            "comment": "Pain was lower after reducing load.",
        },
    )

    outcome = db.scalar(
        select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
    )
    assert outcome is not None
    assert outcome.outcome_status == "improved"
    assert outcome.metrics["implementation_status"] == "implemented"
    assert outcome.metrics["subjective_outcome"] == "improved"
    memory = db.get(models.LongTermMemory, outcome.reflected_memory_id)
    assert memory is not None
    assert memory.memory_network == "experience"
    assert memory.fact_kind == "strategy_experience"
    assert any(item["table"] == "decision_followups" for item in memory.evidence)
    assert memory.memory_metadata["decision_id"] == str(decision.id)
    assert memory.memory_metadata["outcome_id"] == str(outcome.id)
    assert memory.memory_metadata["baseline_state"]["fatigue"] == 8
    assert memory.memory_metadata["applicability"]["requires_similar_baseline"] is True
    assert memory.memory_metadata["last_confirmed_at"]
    assert memory.memory_metadata["review_due_at"]
    assert plan.status == "completed"


@pytest.mark.parametrize("legacy", [False, True])
def test_workout_records_do_not_prove_plan_adoption_or_strategy_gain(legacy):
    db = make_db()
    user = add_user(db)
    decision_time = datetime.utcnow() - timedelta(days=3)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="plan_generation",
        context_used={},
        input_summary="合成计划",
        decision_result="建议训练",
        reason="用户请求",
        confidence_score=0.8,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    if legacy:
        plan.expected_action = {"requires_user_confirmation": False}
        plan.implementation_status = "implemented"
    for offset in (1, 2):
        db.add(
            models.WorkoutLog(
                user_id=user.id,
                performed_at=decision_time + timedelta(days=offset),
                workout_name="自主训练，没有说明采用建议",
                completion_rate=1.0,
                rpe=5,
            )
        )
        db.add(
            models.RecoveryLog(
                user_id=user.id,
                log_date=(decision_time + timedelta(days=offset)).date(),
                fatigue_score=2,
                sleep_hours=8,
            )
        )
    db.flush()
    result = service.refresh_plan(plan, trigger_type="workout_logged")
    assert result["status"] == "waiting_user"
    assert plan.evidence_snapshot["workout_count"] == 2
    assert plan.implementation_status == "unknown"
    assert (
        db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
        )
        is None
    )
    assert (
        db.scalar(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user.id,
                models.LongTermMemory.fact_kind.in_(["strategy_experience", "failed_strategy"]),
            )
        )
        is None
    )
    assert (
        db.scalar(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan.id
            )
        )
        is not None
    )


def test_not_started_followup_does_not_create_failed_strategy():
    db = make_db()
    user = add_user(db)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="nutrition_strategy",
        input_summary="Protein below target",
        context_used={},
        decision_result="use high-protein takeout defaults",
        reason="Improve adherence",
        confidence_score=0.8,
        created_at=datetime.utcnow() - timedelta(days=4),
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    service.refresh_plan(plan, trigger_type="scheduled_scan", now=datetime.utcnow())
    followup = db.scalar(
        select(models.DecisionFollowup).where(models.DecisionFollowup.evaluation_plan_id == plan.id)
    )
    assert followup is not None
    service.answer_followup(
        followup.id,
        user.id,
        {"implementation_status": "not_started", "comment": "Did not try it yet."},
    )

    assert plan.status == "insufficient_evidence"
    assert plan.outcome_status == "not_applicable"
    assert (
        db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
        )
        is None
    )
    assert (
        db.scalar(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user.id,
                models.LongTermMemory.fact_kind == "failed_strategy",
            )
        )
        is None
    )


def test_subjective_and_objective_conflict_becomes_mixed():
    db = make_db()
    user = add_user(db)
    decision_time = datetime.utcnow() - timedelta(days=2)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="training_adjustment",
        input_summary="Fatigue elevated",
        context_used={},
        decision_result="reduce load",
        reason="Protect recovery",
        confidence_score=0.8,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    db.add(
        models.WorkoutLog(
            user_id=user.id,
            performed_at=decision_time + timedelta(days=1),
            workout_name="Reduced load",
            rpe=6,
            completion_rate=0.9,
        )
    )
    db.add(
        models.RecoveryLog(
            user_id=user.id,
            log_date=(decision_time + timedelta(days=1)).date(),
            fatigue_score=4,
        )
    )
    db.flush()
    service.refresh_plan(plan, trigger_type="workout_logged")
    followup = db.scalar(
        select(models.DecisionFollowup).where(models.DecisionFollowup.evaluation_plan_id == plan.id)
    )

    service.answer_followup(
        followup.id,
        user.id,
        {
            "implementation_status": "implemented",
            "subjective_outcome": "worse",
            "comment": "The numbers were fine, but the movement felt worse.",
        },
    )

    outcome = db.scalar(
        select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
    )
    assert outcome is not None
    assert outcome.outcome_status == "mixed"
    assert outcome.metrics["subjective_outcome"] == "worse"


def test_due_scan_marks_expired_plan_insufficient_without_false_failure():
    db = make_db()
    user = add_user(db)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="plan_generation",
        input_summary="Generate plan",
        context_used={},
        decision_result="created plan",
        reason="User requested a plan",
        confidence_score=0.75,
        created_at=datetime.utcnow() - timedelta(days=20),
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)

    result = service.scan_due(user.id, now=datetime.utcnow())

    assert result["processed"] == 1
    assert plan.status == "insufficient_evidence"
    assert plan.outcome_status == "insufficient_evidence"
    assert (
        db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
        )
        is None
    )


def test_safety_evidence_escalates_and_creates_urgent_followup():
    db = make_db()
    user = add_user(db)
    decision_time = datetime.utcnow() - timedelta(days=1)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="progression",
        input_summary="Try progression",
        context_used={},
        decision_result="increase load",
        reason="Prior session looked stable",
        confidence_score=0.7,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    db.add(
        models.SymptomLog(
            user_id=user.id,
            symptom_date=date.today(),
            symptom_type="sharp pain",
            severity_score=8,
            status="active",
        )
    )
    db.flush()

    result = service.refresh_plan(plan, trigger_type="symptom_logged")

    assert result["status"] == "escalated"
    followup = db.scalar(
        select(models.DecisionFollowup).where(
            models.DecisionFollowup.evaluation_plan_id == plan.id,
            models.DecisionFollowup.question_type == "safety_check",
        )
    )
    assert followup is not None


def test_adjustment_outcome_excludes_triggering_recovery_baseline():
    db = make_db()
    user = add_user(db)
    baseline_time = datetime.utcnow() - timedelta(hours=1)
    baseline = models.RecoveryLog(
        user_id=user.id,
        log_date=baseline_time.date(),
        sleep_hours=4,
        fatigue_score=9,
        created_at=baseline_time,
        updated_at=baseline_time,
    )
    db.add(baseline)
    db.flush()
    decision_time = baseline_time + timedelta(minutes=1)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="plan_adjustment",
        input_summary="High fatigue",
        context_used={
            "latest_checkin": {"fatigue": 9},
            "baseline_recovery_log_id": str(baseline.id),
        },
        decision_result="reduce load",
        reason="Protect recovery",
        confidence_score=0.8,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    db.add(
        models.WorkoutLog(
            user_id=user.id,
            performed_at=decision_time + timedelta(days=1),
            workout_name="Reduced load",
            rpe=5,
            completion_rate=0.9,
        )
    )
    db.flush()

    first = service.refresh_plan(plan, trigger_type="workout_logged")

    assert first["evidence"]["recovery_count"] == 0
    assert first["evidence"]["safety_escalation"] is False
    assert first["status"] == "waiting_user"

    db.add(
        models.RecoveryLog(
            user_id=user.id,
            log_date=(decision_time + timedelta(days=1)).date(),
            sleep_hours=7.5,
            fatigue_score=4,
        )
    )
    db.flush()
    service.refresh_plan(plan, trigger_type="daily_checkin_submitted")
    followup = db.scalar(
        select(models.DecisionFollowup).where(
            models.DecisionFollowup.evaluation_plan_id == plan.id,
            models.DecisionFollowup.status == "pending",
        )
    )
    service.answer_followup(
        followup.id,
        user.id,
        {
            "implementation_status": "implemented",
            "subjective_outcome": "improved",
        },
    )

    outcome = db.scalar(
        select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
    )
    assert outcome is not None
    assert outcome.outcome_status == "improved"
    assert outcome.metrics["avg_fatigue_score"] == 4.0


def test_followup_delivery_is_bounded_to_two_chat_prompts():
    db = make_db()
    user = add_user(db)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="nutrition_strategy",
        input_summary="Protein below target",
        context_used={},
        decision_result="use high-protein defaults",
        reason="Improve adherence",
        confidence_score=0.8,
        created_at=datetime.utcnow() - timedelta(days=4),
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    service.refresh_plan(plan, trigger_type="scheduled_scan")

    first = service.next_followup_for_delivery(user.id)
    second = service.next_followup_for_delivery(user.id)
    third = service.next_followup_for_delivery(user.id)

    assert first["attempt_count"] == 1
    assert second["attempt_count"] == 2
    assert third is None


def test_expired_adjustment_without_feedback_becomes_insufficient_before_followup():
    db = make_db()
    user = add_user(db)
    decision_time = datetime.utcnow() - timedelta(days=8)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="plan_adjustment",
        input_summary="High fatigue",
        context_used={"fatigue": 9},
        decision_result="reduce load",
        reason="Protect recovery",
        confidence_score=0.8,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)

    result = service.refresh_plan(
        plan,
        trigger_type="scheduled_scan",
        now=plan.window_end + timedelta(seconds=1),
    )

    assert result["reason"] == "evaluation_window_expired"
    assert plan.status == "insufficient_evidence"
    assert plan.outcome_status == "insufficient_evidence"
    assert plan.completed_at is not None
    assert (
        db.scalar(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan.id
            )
        )
        is None
    )
    assert (
        db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
        )
        is None
    )


def test_answered_safety_followup_is_terminal_and_not_recreated():
    db = make_db()
    user = add_user(db)
    decision_time = datetime.utcnow() - timedelta(days=2)
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="plan_adjustment",
        input_summary="High fatigue",
        context_used={"fatigue": 9},
        decision_result="reduce load",
        reason="Protect recovery",
        confidence_score=0.8,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    service = DecisionEvaluationService(db)
    plan = service.create_for_decision(decision)
    db.add(
        models.SymptomLog(
            user_id=user.id,
            symptom_date=(decision_time + timedelta(days=1)).date(),
            symptom_type="chest tightness",
            severity_score=9,
            status="active",
        )
    )
    db.flush()

    result = service.refresh_plan(plan, trigger_type="symptom_logged")
    followup = db.scalar(
        select(models.DecisionFollowup).where(
            models.DecisionFollowup.evaluation_plan_id == plan.id,
            models.DecisionFollowup.question_type == "safety_check",
        )
    )
    assert result["reason"] == "safety_escalation"
    assert followup is not None

    service.answer_followup(
        followup.id,
        user.id,
        {
            "implementation_status": "implemented",
            "safety_status": "severe",
        },
    )

    followups = list(
        db.scalars(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan.id
            )
        )
    )
    assert plan.status == "escalated"
    assert plan.outcome_status == "safety_escalated"
    assert plan.completed_at is not None
    assert len(followups) == 1
    assert followups[0].status == "answered"
    assert (
        db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
        )
        is None
    )
