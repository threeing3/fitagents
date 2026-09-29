import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.decision_evaluation import DecisionEvaluationService, _aligned_now
from fast_api.app.services.decision_logger import DecisionLogger


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
