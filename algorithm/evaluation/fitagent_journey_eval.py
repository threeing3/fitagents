"""Fixed synthetic task-level evaluation for the FitAgent business journey."""

from __future__ import annotations

import argparse
import copy
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.schemas.agent import (
    DailyCheckinRequest,
    PlanGenerateRequest,
    WorkoutLogRequest,
)
from fast_api.app.services.approval_manager import ApprovalManager
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.context_builder import ContextBuilder
from fast_api.app.services.decision_evaluation import DecisionEvaluationService
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.responsibilities import ResponsibilityService

DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1] / "datasets" / "fixtures" / "fitagent_journey_v1.json"
)
DEFAULT_REPORT = Path(__file__).resolve().parent / "reports" / "fitagent_journey_v2.summary.json"

CHECK_NAMES = [
    "profile_corrected",
    "stale_memory_inactive",
    "stale_memory_not_retrieved",
    "checkin_replayed_once",
    "checkin_plan_unchanged",
    "approval_required",
    "approved_scope_verified",
    "single_active_plan",
    "workout_replayed_once",
    "evaluation_evidence_collected",
    "evaluation_completed",
    "experience_memory_created",
]


def load_journey_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "fitagent-journey-dataset/v1":
        raise ValueError("Unsupported journey dataset schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Journey dataset must contain at least one case")
    case_ids = [str(item.get("case_id") or "") for item in cases]
    if not all(case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("Journey case IDs must be non-empty and unique")
    if payload.get("source") != "synthetic_fixed_journey_v1":
        raise ValueError("Journey dataset source must remain explicitly synthetic")
    if payload.get("training_eligible") is not False:
        raise ValueError("Journey evaluation cases must not be marked training eligible")
    return payload


def _offline_provider() -> ModelProvider:
    return ModelProvider(
        Settings(
            _env_file=None,
            database_url="sqlite:///:memory:",
            llm_provider="offline",
            embedding_provider="offline",
            use_pgvector=False,
            jwt_secret_key="synthetic-journey-evaluation-only",
        )
    )


def _seed_user(db: Session, case: dict[str, Any]) -> tuple[models.User, models.UserProfile]:
    user = models.User(
        id=uuid.uuid4(),
        email=f"{case['case_id']}@synthetic.example.test",
        password_hash="not-a-login",
    )
    profile = models.UserProfile(
        user_id=user.id,
        age=25,
        sex="male",
        height_cm=175,
        weight_kg=70,
        goal="fat_loss",
        experience_level="beginner",
        workout_frequency=3,
        equipment_available=["dumbbells"],
        injuries=["shoulder"] if case["correction_type"] == "injury_clear" else [],
    )
    db.add_all([user, profile])
    db.flush()
    return user, profile


def _seed_stale_state(
    db: Session,
    user_id: uuid.UUID,
    correction_type: str,
) -> models.LongTermMemory:
    if correction_type == "injury_clear":
        memory = models.LongTermMemory(
            user_id=user_id,
            memory_type="risk_signal",
            memory_network="world",
            fact_kind="user_profile_fact",
            category="risk",
            content="shoulder injury",
            summary="shoulder injury",
            source="synthetic_journey_fixture",
            status="active",
        )
        db.add(
            models.RiskNote(
                user_id=user_id,
                body_part="shoulder",
                risk_type="injury",
                description="shoulder injury",
            )
        )
    elif correction_type == "goal_replace":
        memory = models.LongTermMemory(
            user_id=user_id,
            memory_type="user_profile_fact",
            memory_network="world",
            fact_kind="user_profile_fact",
            category="profile",
            content="用户目标是减脂。",
            summary="用户目标是减脂。",
            source="synthetic_journey_fixture",
            status="active",
        )
    else:
        raise ValueError(f"Unsupported correction type: {correction_type}")
    db.add(memory)
    db.flush()
    return memory


def _count(db: Session, model: type[Any], *filters: Any) -> int:
    statement = select(func.count()).select_from(model)
    if filters:
        statement = statement.where(*filters)
    return int(db.scalar(statement) or 0)


def evaluate_journey_case(case: dict[str, Any]) -> dict[str, Any]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    provider = _offline_provider()
    try:
        with Session(engine) as db:
            user, profile = _seed_user(db, case)
            stale_memory = _seed_stale_state(db, user.id, case["correction_type"])
            manager = MemoryManager(db, provider)
            manager.update_memory_catalog(user.id, stale_memory.category)
            manager.update_memory_blocks(user.id)
            db.commit()

            service = CoachAgentService(db, provider)
            message = str(case["correction_message"])
            extraction = service._rule_profile_extraction(message)
            service._apply_profile_extraction(profile, extraction)
            verification = service._verify_memory_tool(user.id, message, extraction, profile)
            written = service.write_memories_from_message(
                user.id,
                message,
                extraction=extraction,
                verification=verification,
            )
            db.commit()

            context_intent = (
                "injury_or_risk" if case["correction_type"] == "injury_clear" else "training_plan"
            )
            context = ContextBuilder(db, provider).build_context_packet(
                user.id,
                "右肩训练应该注意什么"
                if case["correction_type"] == "injury_clear"
                else "给我新的训练计划",
                intent=context_intent,
            )
            relevant_ids = {item["id"] for item in context["relevant_memories"]}

            plan = service.generate_plan(
                PlanGenerateRequest(user_id=user.id, force=True, plan_days=7)
            )
            dated = copy.deepcopy(plan.plan_json)
            for index, day in enumerate(dated["training_days"]):
                day["date"] = (date.today() + timedelta(days=index + 1)).isoformat()
            plan.plan_json = dated
            ResponsibilityService(db).create_weekly(user.id, "每周复盘，调整先询问")
            db.commit()
            baseline = copy.deepcopy(plan.plan_json)
            checkin_request = DailyCheckinRequest(
                user_id=user.id,
                idempotency_key=f"{case['case_id']}-checkin",
                checkin_date=date.today(),
                sleep_hours=float(case["sleep_hours"]),
                fatigue=int(case["fatigue"]),
                soreness=int(case["soreness"]),
                workout_completion=70,
            )
            first_checkin = service.record_daily_checkin(checkin_request)
            replayed_checkin = service.record_daily_checkin(checkin_request)
            checkin_plan_unchanged = (
                plan.plan_json == baseline and not first_checkin["auto_adjusted"]
            )
            proposal = first_checkin["adjustment_proposal"]
            approval_required = (
                proposal["status"] == "waiting_approval" and run_one_background_task(db) is None
            )
            if not approval_required:
                raise RuntimeError("Check-in did not produce an approval-only dated proposal")
            ApprovalManager(db).approve(proposal["approval_id"])
            db.commit()
            job = run_one_background_task(db)
            expected = copy.deepcopy(baseline)
            for day in expected["training_days"]:
                if day["date"] == proposal["day_date"]:
                    for exercise in day["exercises"]:
                        exercise["sets"] -= 1
            approved_scope_verified = (
                job is not None
                and job.result_json.get("verified") is True
                and plan.plan_json == expected
            )

            adjustment_decision = db.scalar(
                select(models.AgentDecision)
                .where(
                    models.AgentDecision.user_id == user.id,
                    models.AgentDecision.decision_type == "approved_plan_adjustment",
                )
                .order_by(models.AgentDecision.created_at.desc())
            )
            if adjustment_decision is None:
                raise RuntimeError("Approved execution did not create a plan adjustment decision")
            evaluation_plan = db.scalar(
                select(models.DecisionEvaluationPlan).where(
                    models.DecisionEvaluationPlan.decision_id == adjustment_decision.id
                )
            )
            if evaluation_plan is None:
                raise RuntimeError("Plan adjustment decision has no evaluation plan")

            service.record_daily_checkin(
                DailyCheckinRequest(
                    user_id=user.id,
                    idempotency_key=f"{case['case_id']}-followup-checkin",
                    checkin_date=date.today() + timedelta(days=1),
                    sleep_hours=7.5,
                    fatigue=4,
                    soreness=2,
                    workout_completion=90,
                )
            )

            workout_request = WorkoutLogRequest(
                user_id=user.id,
                idempotency_key=f"{case['case_id']}-workout",
                performed_at=datetime.utcnow(),
                workout_name=f"{case['case_id']} adjusted workout",
                duration_minutes=35,
                rpe=int(case["workout_rpe"]),
                completion_rate=float(case["completion_rate"]),
                exercises=[{"name": "dumbbell row", "sets": [{"reps": 10, "weight": 12}]}],
            )
            first_workout = service.record_workout_log(workout_request)
            first_workout_replay = bool(getattr(first_workout, "_idempotent_replay", False))
            replayed_workout = service.record_workout_log(workout_request)

            db.refresh(evaluation_plan)
            followup = db.scalar(
                select(models.DecisionFollowup).where(
                    models.DecisionFollowup.evaluation_plan_id == evaluation_plan.id,
                    models.DecisionFollowup.status == "pending",
                )
            )
            if followup is None:
                raise RuntimeError("Workout evidence did not create an evaluation follow-up")
            DecisionEvaluationService(db).answer_followup(
                followup.id,
                user.id,
                {
                    "implementation_status": "implemented",
                    "subjective_outcome": "improved",
                    "comment": "Synthetic journey reports improved recovery after reduced load.",
                },
            )
            db.commit()
            db.refresh(evaluation_plan)
            db.refresh(stale_memory)
            db.refresh(profile)

            outcome = db.scalar(
                select(models.DecisionOutcome).where(
                    models.DecisionOutcome.decision_id == adjustment_decision.id
                )
            )
            reflected_memory = (
                db.get(models.LongTermMemory, outcome.reflected_memory_id)
                if outcome and outcome.reflected_memory_id
                else None
            )
            expected_profile = (
                profile.injuries == []
                if case["correction_type"] == "injury_clear"
                else profile.goal == "muscle_gain"
            )
            checks = {
                "checkin_plan_unchanged": checkin_plan_unchanged,
                "approval_required": approval_required,
                "approved_scope_verified": approved_scope_verified,
                "profile_corrected": expected_profile and bool(written),
                "stale_memory_inactive": stale_memory.status == "superseded",
                "stale_memory_not_retrieved": str(stale_memory.id) not in relevant_ids,
                "checkin_replayed_once": (
                    first_checkin["checkin_id"] == replayed_checkin["checkin_id"]
                    and first_checkin["idempotent_replay"] is False
                    and replayed_checkin["idempotent_replay"] is True
                    and _count(
                        db,
                        models.DailyCheckin,
                        models.DailyCheckin.user_id == user.id,
                        models.DailyCheckin.checkin_date == date.today(),
                    )
                    == 1
                ),
                "single_active_plan": (
                    _count(
                        db,
                        models.TrainingPlan,
                        models.TrainingPlan.user_id == user.id,
                        models.TrainingPlan.status == "active",
                    )
                    == 1
                ),
                "workout_replayed_once": (
                    replayed_workout.id == first_workout.id
                    and first_workout_replay is False
                    and bool(getattr(replayed_workout, "_idempotent_replay", False))
                    and _count(db, models.WorkoutLog, models.WorkoutLog.user_id == user.id) == 1
                    and _count(db, models.WorkoutSession, models.WorkoutSession.user_id == user.id)
                    == 1
                    and _count(db, models.ExerciseLog, models.ExerciseLog.user_id == user.id) == 1
                    and _count(
                        db,
                        models.LongTermMemory,
                        models.LongTermMemory.user_id == user.id,
                        models.LongTermMemory.source == "workout_log",
                    )
                    == 1
                ),
                "evaluation_evidence_collected": (
                    int((evaluation_plan.evidence_snapshot or {}).get("workout_count", 0)) >= 1
                    and int((evaluation_plan.evidence_snapshot or {}).get("recovery_count", 0)) >= 1
                ),
                "evaluation_completed": (
                    evaluation_plan.status == "completed"
                    and outcome is not None
                    and outcome.outcome_status == "improved"
                ),
                "experience_memory_created": (
                    reflected_memory is not None
                    and reflected_memory.memory_network == "experience"
                    and reflected_memory.fact_kind == "strategy_experience"
                ),
            }
            return {
                "case_id": case["case_id"],
                "correction_type": case["correction_type"],
                "passed": all(checks.values()),
                "checks": checks,
                "observed": {
                    "written_correction_memory_count": len(written),
                    "active_plan_count": _count(
                        db,
                        models.TrainingPlan,
                        models.TrainingPlan.user_id == user.id,
                        models.TrainingPlan.status == "active",
                    ),
                    "workout_count": _count(
                        db, models.WorkoutLog, models.WorkoutLog.user_id == user.id
                    ),
                    "evaluation_status": evaluation_plan.status,
                    "outcome_status": outcome.outcome_status if outcome else None,
                    "evaluation_evidence": evaluation_plan.evidence_snapshot or {},
                },
            }
    finally:
        engine.dispose()


def evaluate_journeys(dataset_path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    dataset = load_journey_dataset(dataset_path)
    results = [evaluate_journey_case(case) for case in dataset["cases"]]
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    check_rates = {
        name: sum(1 for item in results if item["checks"][name]) / total for name in CHECK_NAMES
    }
    return {
        "schema_version": "fitagent-journey-eval/v2",
        "execution_protocol": "explicit_delegation_dated_proposal_approval_v2",
        "dataset": {
            "name": dataset_path.name,
            "source": dataset["source"],
            "cases": total,
            "training_eligible": dataset["training_eligible"],
            "independent_unit": "isolated_synthetic_user_journey",
        },
        "summary": {
            "cases": total,
            "passed": passed,
            "failed": total - passed,
            "task_success_rate": passed / total,
            "check_rates": check_rates,
        },
        "cases": results,
        "limitations": [
            "Protocol v2 adds explicit approval; not directly comparable to automatic-adjustment v1 reports.",
            "Synthetic fixed journeys only; no real-user or online outcome claim.",
            "SQLite isolated execution; PostgreSQL concurrency is evaluated separately.",
            "Follow-up answers are scripted simulated outcomes, not human feedback.",
            "A passed journey demonstrates deterministic system wiring, not coaching efficacy.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = evaluate_journeys(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
