"""Fixed synthetic challenges for FitAgent's decision-evaluation state machine."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.decision_evaluation import DecisionEvaluationService

DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1]
    / "datasets"
    / "fixtures"
    / "fitagent_evaluation_challenge_v1.json"
)
DEFAULT_REPORT = (
    Path(__file__).resolve().parent / "reports" / "fitagent_evaluation_challenge_v1.summary.json"
)
ALLOWED_SCENARIOS = {
    "no_feedback_expiry",
    "dangerous_symptom",
    "delayed_evidence",
    "service_reinstantiation",
}


def load_challenge_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "fitagent-evaluation-challenge/v1":
        raise ValueError("Unsupported evaluation challenge schema")
    if payload.get("source") != "synthetic_fixed_evaluation_challenge_v1":
        raise ValueError("Evaluation challenge source must remain explicitly synthetic")
    if payload.get("training_eligible") is not False:
        raise ValueError("Evaluation challenges must not be marked training eligible")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation challenge dataset must contain cases")
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("Evaluation challenge case IDs must be non-empty and unique")
    scenarios = {str(case.get("scenario") or "") for case in cases}
    if scenarios != ALLOWED_SCENARIOS:
        raise ValueError("Evaluation challenge must cover every frozen scenario")
    return payload


def _new_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _count(db: Session, model: type[Any], *filters: Any) -> int:
    statement = select(func.count()).select_from(model)
    if filters:
        statement = statement.where(*filters)
    return int(db.scalar(statement) or 0)


def _experience_memories(db: Session, user_id: uuid.UUID) -> list[models.LongTermMemory]:
    return list(
        db.scalars(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.memory_network == "experience",
            )
        )
    )


def _seed_adjustment(
    db: Session,
    case: dict[str, Any],
) -> tuple[models.User, models.AgentDecision, models.DecisionEvaluationPlan]:
    user = models.User(
        id=uuid.uuid4(),
        email=f"{case['case_id']}@synthetic.example.test",
        password_hash="not-a-login",
        display_name="Synthetic evaluation challenge",
    )
    db.add(user)
    db.flush()
    decision_time = datetime.utcnow() - timedelta(days=int(case["decision_age_days"]))
    baseline = models.RecoveryLog(
        user_id=user.id,
        log_date=decision_time.date(),
        sleep_hours=5.5,
        fatigue_score=9,
        soreness_score=6,
        notes="Synthetic trigger record; not post-decision evidence.",
    )
    db.add(baseline)
    db.flush()
    decision = models.AgentDecision(
        user_id=user.id,
        decision_type="plan_adjustment",
        input_summary="Synthetic high-fatigue trigger",
        context_used={
            "latest_checkin": {"fatigue": 9, "soreness": 6},
            "baseline_recovery_log_id": str(baseline.id),
        },
        decision_result="reduce training load",
        reason="Protect recovery while collecting follow-up evidence",
        confidence_score=0.82,
        created_at=decision_time,
    )
    db.add(decision)
    db.flush()
    plan = DecisionEvaluationService(db).create_for_decision(decision)
    db.flush()
    return user, decision, plan


def _add_post_decision_evidence(
    db: Session,
    user_id: uuid.UUID,
    decision_time: datetime,
    case: dict[str, Any],
) -> None:
    evidence_time = decision_time + timedelta(days=int(case["evidence_day"]))
    db.add_all(
        [
            models.WorkoutLog(
                user_id=user_id,
                performed_at=evidence_time,
                workout_name=f"{case['case_id']} reduced-load workout",
                rpe=6,
                completion_rate=float(case["completion_rate"]),
            ),
            models.RecoveryLog(
                user_id=user_id,
                log_date=evidence_time.date(),
                sleep_hours=7.5,
                fatigue_score=float(case["fatigue"]),
                soreness_score=2,
                notes="Synthetic post-decision evidence.",
            ),
        ]
    )
    db.flush()


def _outcome(db: Session, decision_id: uuid.UUID) -> models.DecisionOutcome | None:
    return db.scalar(
        select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision_id)
    )


def _answer_strategy_followup(
    db: Session,
    service: DecisionEvaluationService,
    plan_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_outcome: str,
) -> None:
    followup = db.scalar(
        select(models.DecisionFollowup).where(
            models.DecisionFollowup.evaluation_plan_id == plan_id,
            models.DecisionFollowup.question_type == "strategy_execution",
            models.DecisionFollowup.status == "pending",
        )
    )
    if followup is None:
        raise RuntimeError("Objective evidence did not create a strategy follow-up")
    service.answer_followup(
        followup.id,
        user_id,
        {
            "implementation_status": "implemented",
            "subjective_outcome": expected_outcome,
            "comment": "Synthetic delayed confirmation from the fixed challenge.",
        },
    )


def _run_no_feedback(db: Session, case: dict[str, Any]) -> dict[str, Any]:
    user, decision, plan = _seed_adjustment(db, case)
    result = DecisionEvaluationService(db).refresh_plan(
        plan,
        trigger_type="scheduled_scan",
        now=plan.window_end + timedelta(seconds=1),
    )
    outcome = _outcome(db, decision.id)
    checks = {
        "expired_as_insufficient": result["status"] == "insufficient_evidence",
        "outcome_labeled_insufficient": plan.outcome_status == "insufficient_evidence",
        "terminal_timestamp_recorded": plan.completed_at is not None,
        "no_outcome_fabricated": outcome is None,
        "no_experience_fabricated": not _experience_memories(db, user.id),
    }
    return _result(case, checks, plan, outcome, db)


def _run_dangerous_symptom(db: Session, case: dict[str, Any]) -> dict[str, Any]:
    user, decision, plan = _seed_adjustment(db, case)
    symptom_time = decision.created_at + timedelta(days=1)
    db.add(
        models.SymptomLog(
            user_id=user.id,
            symptom_date=symptom_time.date(),
            body_part="knee" if "knee" in case["symptom_type"] else "chest",
            symptom_type=str(case["symptom_type"]),
            severity_score=float(case["severity"]),
            trigger_context="Synthetic post-adjustment safety signal",
            status="active",
        )
    )
    db.flush()
    service = DecisionEvaluationService(db)
    first = service.refresh_plan(plan, trigger_type="symptom_logged", now=symptom_time)
    followup = db.scalar(
        select(models.DecisionFollowup).where(
            models.DecisionFollowup.evaluation_plan_id == plan.id,
            models.DecisionFollowup.question_type == "safety_check",
            models.DecisionFollowup.status == "pending",
        )
    )
    if followup is None:
        raise RuntimeError("Safety escalation did not create an urgent follow-up")
    service.answer_followup(
        followup.id,
        user.id,
        {
            "implementation_status": "implemented",
            "safety_status": "severe",
            "comment": "Synthetic user confirms worsening and stops training.",
        },
    )
    outcome = _outcome(db, decision.id)
    safety_followups = list(
        db.scalars(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan.id,
                models.DecisionFollowup.question_type == "safety_check",
            )
        )
    )
    checks = {
        "danger_detected": first["reason"] == "safety_escalation",
        "terminal_escalated_state": plan.status == "escalated",
        "safety_outcome_labeled": plan.outcome_status == "safety_escalated",
        "safety_followup_bounded": (
            len(safety_followups) == 1
            and safety_followups[0].status == "answered"
            and plan.followup_count == 1
        ),
        "no_automatic_outcome": outcome is None,
        "no_experience_memory": not _experience_memories(db, user.id),
    }
    return _result(case, checks, plan, outcome, db)


def _run_delayed_evidence(db: Session, case: dict[str, Any]) -> dict[str, Any]:
    user, decision, plan = _seed_adjustment(db, case)
    _add_post_decision_evidence(db, user.id, decision.created_at, case)
    service = DecisionEvaluationService(db)
    result = service.refresh_plan(
        plan,
        trigger_type="delayed_evidence_scan",
        now=decision.created_at + timedelta(days=int(case["evidence_day"])),
    )
    _answer_strategy_followup(
        db,
        service,
        plan.id,
        user.id,
        str(case["expected_outcome"]),
    )
    outcome = _outcome(db, decision.id)
    memories = _experience_memories(db, user.id)
    checks = {
        "within_window_evidence_counted": (
            result["evidence"].get("workout_count") == 1
            and result["evidence"].get("recovery_count") == 1
        ),
        "evaluation_completed": plan.status == "completed",
        "expected_outcome": (
            outcome is not None and outcome.outcome_status == case["expected_outcome"]
        ),
        "single_outcome": _count(
            db, models.DecisionOutcome, models.DecisionOutcome.user_id == user.id
        )
        == 1,
        "single_experience_memory": len(memories) == 1,
    }
    return _result(case, checks, plan, outcome, db)


def _run_service_reinstantiation(engine: Engine, case: dict[str, Any]) -> dict[str, Any]:
    with Session(engine) as first_db:
        user, decision, plan = _seed_adjustment(first_db, case)
        user_id, decision_id, plan_id = user.id, decision.id, plan.id
        decision_time = decision.created_at
        first_db.commit()

    with Session(engine) as second_db:
        persisted_plan = second_db.get(models.DecisionEvaluationPlan, plan_id)
        if persisted_plan is None:
            raise RuntimeError("Evaluation plan was not durable across service instances")
        _add_post_decision_evidence(second_db, user_id, decision_time, case)
        DecisionEvaluationService(second_db).refresh_plan(
            persisted_plan,
            trigger_type="service_reinstantiated",
            now=decision_time + timedelta(days=int(case["evidence_day"])),
        )
        second_db.commit()

    with Session(engine) as third_db:
        service = DecisionEvaluationService(third_db)
        _answer_strategy_followup(
            third_db,
            service,
            plan_id,
            user_id,
            str(case["expected_outcome"]),
        )
        third_db.commit()

    with Session(engine) as fourth_db:
        persisted_plan = fourth_db.get(models.DecisionEvaluationPlan, plan_id)
        if persisted_plan is None:
            raise RuntimeError("Evaluation plan disappeared after restart verification")
        outcome = _outcome(fourth_db, decision_id)
        memories = _experience_memories(fourth_db, user_id)
        checks = {
            "plan_survived_new_session": persisted_plan.status == "completed",
            "expected_outcome": (
                outcome is not None and outcome.outcome_status == case["expected_outcome"]
            ),
            "single_outcome_after_restart": _count(
                fourth_db,
                models.DecisionOutcome,
                models.DecisionOutcome.decision_id == decision_id,
            )
            == 1,
            "single_experience_after_restart": len(memories) == 1,
            "evidence_preserved": (
                (persisted_plan.evidence_snapshot or {}).get("workout_count") == 1
                and (persisted_plan.evidence_snapshot or {}).get("recovery_count") == 1
            ),
        }
        return _result(case, checks, persisted_plan, outcome, fourth_db)


def _result(
    case: dict[str, Any],
    checks: dict[str, bool],
    plan: models.DecisionEvaluationPlan,
    outcome: models.DecisionOutcome | None,
    db: Session,
) -> dict[str, Any]:
    followups = list(
        db.scalars(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan.id
            )
        )
    )
    return {
        "case_id": case["case_id"],
        "scenario": case["scenario"],
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "plan_status": plan.status,
            "plan_outcome_status": plan.outcome_status,
            "outcome_status": outcome.outcome_status if outcome else None,
            "followup_count": len(followups),
            "pending_followups": sum(item.status == "pending" for item in followups),
            "experience_memory_count": len(_experience_memories(db, plan.user_id)),
            "evidence": plan.evidence_snapshot or {},
        },
    }


def evaluate_challenge_case(case: dict[str, Any]) -> dict[str, Any]:
    engine = _new_engine()
    try:
        if case["scenario"] == "service_reinstantiation":
            return _run_service_reinstantiation(engine, case)
        with Session(engine) as db:
            if case["scenario"] == "no_feedback_expiry":
                return _run_no_feedback(db, case)
            if case["scenario"] == "dangerous_symptom":
                return _run_dangerous_symptom(db, case)
            if case["scenario"] == "delayed_evidence":
                return _run_delayed_evidence(db, case)
            raise ValueError(f"Unsupported challenge scenario: {case['scenario']}")
    finally:
        engine.dispose()


def evaluate_challenges(dataset_path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    dataset = load_challenge_dataset(dataset_path)
    results = [evaluate_challenge_case(case) for case in dataset["cases"]]
    scenario_names = sorted({item["scenario"] for item in results})
    scenario_rates = {
        scenario: (
            sum(item["passed"] for item in results if item["scenario"] == scenario)
            / sum(1 for item in results if item["scenario"] == scenario)
        )
        for scenario in scenario_names
    }
    passed = sum(item["passed"] for item in results)
    return {
        "schema_version": "fitagent-evaluation-challenge-report/v1",
        "dataset": {
            "name": dataset_path.name,
            "source": dataset["source"],
            "cases": len(results),
            "training_eligible": dataset["training_eligible"],
            "independent_unit": "isolated_synthetic_evaluation_journey",
        },
        "summary": {
            "cases": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "task_success_rate": passed / len(results),
            "scenario_success_rates": scenario_rates,
        },
        "cases": results,
        "limitations": [
            "Synthetic fixed state-machine challenges; no real-user efficacy claim.",
            "Service reinstantiation uses new database sessions on one in-process engine.",
            "No process crash, reverse proxy, or distributed worker recovery is tested.",
            "Safety escalation validates conservative state transitions, not medical triage quality.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = evaluate_challenges(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
