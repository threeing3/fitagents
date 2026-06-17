from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["USE_PGVECTOR"] = "false"
os.environ["LLM_PROVIDER"] = "offline"
os.environ["EMBEDDING_PROVIDER"] = "offline"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from fast_api.app.db import models  # noqa: E402
from fast_api.app.db.database import Base  # noqa: E402
from fast_api.app.services.context_builder import ContextBuilder  # noqa: E402
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService  # noqa: E402
from fast_api.app.services.memory_system import MemoryManager  # noqa: E402
from fast_api.app.services.reflection_service import ReflectionService  # noqa: E402
from fast_api.app.services.strategy_memory_policy import build_strategy_memory_response_note  # noqa: E402


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


REQUIRED_CASE_FIELDS = {
    "user_message",
    "seeded_memories",
    "seeded_logs",
    "expected_recalled_terms",
    "expected_intent",
    "expected_safety_rule",
    "should_not_include",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Hindsight memory recall evals.")
    parser.add_argument(
        "--cases",
        default=str(REPO_ROOT / "tests" / "evals" / "hindsight_memory_eval_cases.json"),
        help="Path to eval case JSON.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSONL output path. Defaults to logs/experiments/memory-eval-<timestamp>.jsonl.",
    )
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Eval cases must be a non-empty JSON list.")
    for index, case in enumerate(cases):
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            raise ValueError(f"Case {index} is missing required fields: {sorted(missing)}")
    return cases


def make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return session_factory()


def seed_case(db: Session, case: dict[str, Any], index: int) -> uuid.UUID:
    user_id = uuid.uuid4()
    db.add(
        models.User(
            id=user_id,
            email=f"memory-eval-{index}@example.com",
            username=f"memory_eval_{index}",
            password_hash="eval-only",
            display_name=f"Memory Eval {index}",
            timezone="Asia/Shanghai",
        )
    )
    profile_payload = (case.get("seeded_logs") or {}).get("profile") or {}
    db.add(
        models.UserProfile(
            user_id=user_id,
            age=30,
            sex="unknown",
            height_cm=170,
            weight_kg=70,
            activity_level="moderate",
            goal=profile_payload.get("goal", "general_fitness"),
            experience_level=profile_payload.get("experience_level", "beginner"),
            workout_frequency=profile_payload.get("workout_frequency", 3),
            workout_duration=profile_payload.get("workout_duration", 60),
            dietary_preferences=profile_payload.get("dietary_preferences", []),
            allergies=profile_payload.get("allergies", []),
            equipment_available=profile_payload.get("equipment_available", ["gym"]),
            injuries=profile_payload.get("injuries", []),
            target_calories=profile_payload.get("target_calories"),
            target_protein_g=profile_payload.get("target_protein_g"),
            target_carbs_g=profile_payload.get("target_carbs_g"),
            target_fat_g=profile_payload.get("target_fat_g"),
        )
    )
    db.flush()
    seed_memories(db, user_id, case)
    seed_logs(db, user_id, case.get("seeded_logs") or {})
    db.flush()
    if case.get("run_outcome_reflection"):
        case["_outcome_reflection"] = ReflectionService(db).reflect_decision_outcomes(user_id)
    db.commit()
    return user_id


def seed_memories(db: Session, user_id: uuid.UUID, case: dict[str, Any]) -> None:
    manager = MemoryManager(db)
    for item in case.get("seeded_memories") or []:
        manager.retain_memory(
            user_id=user_id,
            content=item["content"],
            memory_network=item.get("memory_network", "world"),
            fact_kind=item.get("fact_kind", "unknown"),
            category=item.get("category"),
            summary=item.get("summary"),
            entities=item.get("entities") or [],
            evidence=item.get("evidence") or [
                {"table": "eval_cases", "id": case.get("case_id", "unknown")}
            ],
            importance_score=float(item.get("importance_score", 0.7)),
            confidence_score=float(item.get("confidence_score", 0.8)),
            source_type="memory_eval",
        )


def seed_logs(db: Session, user_id: uuid.UUID, logs: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    for item in logs.get("agent_decisions") or []:
        created_at = now - timedelta(days=int(item.get("created_at_days_ago", 3)))
        db.add(
            models.AgentDecision(
                id=uuid.UUID(item["id"]) if item.get("id") else uuid.uuid4(),
                user_id=user_id,
                decision_type=item.get("decision_type", "training_adjustment"),
                input_summary=item.get("input_summary", "Eval decision"),
                context_used=item.get("context_used", {}),
                decision_result=item.get("decision_result", "Eval decision result"),
                reason=item.get("reason", "Eval decision reason"),
                confidence_score=float(item.get("confidence_score", 0.8)),
                accepted_by_user=item.get("accepted_by_user"),
                created_at=created_at,
            )
        )
    for item in logs.get("risk_notes") or []:
        db.add(
            models.RiskNote(
                user_id=user_id,
                body_part=item.get("body_part"),
                risk_type=item["risk_type"],
                description=item["description"],
                severity_score=float(item.get("severity_score", 0.5)),
                confidence_score=float(item.get("confidence_score", 0.75)),
                first_seen_at=now,
                last_seen_at=now,
                status=item.get("status", "active"),
            )
        )
    for item in logs.get("workout_logs") or []:
        performed_at = now - timedelta(days=int(item.get("days_ago", 0)))
        db.add(
            models.WorkoutLog(
                user_id=user_id,
                performed_at=performed_at,
                workout_name=item.get("workout_name", "Workout"),
                exercises=item.get("exercises", []),
                duration_minutes=item.get("duration_minutes"),
                rpe=item.get("rpe"),
                completion_rate=item.get("completion_rate"),
                notes=item.get("notes"),
            )
        )
    for item in logs.get("exercise_logs") or []:
        session_id = uuid.uuid4()
        db.add(
            models.WorkoutSession(
                id=session_id,
                user_id=user_id,
                session_date=date.today(),
                session_name=item.get("session_name", "Eval session"),
                started_at=now,
                ended_at=now,
                completion_score=item.get("completion_score", 1.0),
                fatigue_score=item.get("fatigue_score"),
                mood_score=item.get("mood_score"),
                notes=item.get("session_notes"),
            )
        )
        db.add(
            models.ExerciseLog(
                session_id=session_id,
                user_id=user_id,
                exercise_name=item["exercise_name"],
                set_index=int(item.get("set_index", 1)),
                reps=item.get("reps"),
                weight=item.get("weight"),
                rpe=item.get("rpe"),
                completed=bool(item.get("completed", True)),
                pain_score=item.get("pain_score"),
                pain_location=item.get("pain_location"),
                notes=item.get("notes"),
            )
        )
    for item in logs.get("nutrition_summaries") or []:
        summary_date = date.today() - timedelta(days=int(item.get("days_ago", 0)))
        db.add(
            models.NutritionDailySummary(
                user_id=user_id,
                summary_date=summary_date,
                total_calories=item.get("total_calories"),
                total_protein_g=item.get("total_protein_g"),
                total_carbs_g=item.get("total_carbs_g"),
                total_fat_g=item.get("total_fat_g"),
                total_sodium_mg=item.get("total_sodium_mg"),
                target_calories=item.get("target_calories"),
                target_protein_g=item.get("target_protein_g"),
                adherence_score=item.get("adherence_score"),
                summary_text=item.get("summary_text"),
            )
        )
    for item in logs.get("recovery_logs") or []:
        log_date = date.today() - timedelta(days=int(item.get("days_ago", 0)))
        db.add(
            models.RecoveryLog(
                user_id=user_id,
                log_date=log_date,
                sleep_hours=item.get("sleep_hours"),
                sleep_quality_score=item.get("sleep_quality_score"),
                fatigue_score=item.get("fatigue_score"),
                soreness_score=item.get("soreness_score"),
                stress_score=item.get("stress_score"),
                resting_hr=item.get("resting_hr"),
                notes=item.get("notes"),
            )
        )
    for item in logs.get("symptom_logs") or []:
        symptom_date = date.today() - timedelta(days=int(item.get("days_ago", 0)))
        db.add(
            models.SymptomLog(
                user_id=user_id,
                symptom_date=symptom_date,
                body_part=item.get("body_part"),
                symptom_type=item["symptom_type"],
                severity_score=item.get("severity_score"),
                trigger_context=item.get("trigger_context"),
                action_taken=item.get("action_taken"),
                status=item.get("status", "active"),
            )
        )


def build_packet(db: Session, user_id: uuid.UUID, user_message: str) -> dict[str, Any]:
    builder = ContextBuilder(db)
    builder.knowledge = FitnessKnowledgeService(None)
    return builder.build_context_packet(user_id, user_message)


def serialize_memory_context(packet: dict[str, Any]) -> str:
    payload = {
        "relevant_memories": packet.get("relevant_memories") or [],
        "world_memories": packet.get("world_memories") or [],
        "experience_memories": packet.get("experience_memories") or [],
        "observation_memories": packet.get("observation_memories") or [],
        "opinion_memories": packet.get("opinion_memories") or [],
        "strategy_memory_guidance": packet.get("strategy_memory_guidance") or {},
    }
    return json.dumps(payload, ensure_ascii=False, default=str).lower()


def build_strategy_response_text(packet: dict[str, Any]) -> str:
    return build_strategy_memory_response_note(packet).lower()


def evaluate_case(case: dict[str, Any], index: int) -> dict[str, Any]:
    db = make_session()
    try:
        user_id = seed_case(db, case, index)
        packet = build_packet(db, user_id, case["user_message"])
    finally:
        db.close()

    memory_text = serialize_memory_context(packet)
    expected_terms = [str(term).lower() for term in case["expected_recalled_terms"]]
    missing_terms = [term for term in expected_terms if term not in memory_text]
    wrong_terms = [str(term).lower() for term in case["should_not_include"] if str(term).lower() in memory_text]
    decision_rules = packet.get("knowledge_context", {}).get("decision_rules") or []
    rule_ids = [rule.get("rule_id") for rule in decision_rules]
    expected_rule = case.get("expected_safety_rule")
    safety_rule_hit = None if expected_rule is None else expected_rule in rule_ids
    outcome_reflection = case.get("_outcome_reflection") or {}
    expected_fact_kind = case.get("expected_memory_fact_kind")
    expected_outcome_status = case.get("expected_outcome_status")
    expected_response_terms = [str(term).lower() for term in case.get("expected_response_terms") or []]
    response_text = build_strategy_response_text(packet)
    missing_response_terms = [term for term in expected_response_terms if term not in response_text]
    reflected_fact_kinds = [memory.get("fact_kind") for memory in outcome_reflection.get("memories", [])]
    reflected_statuses = [outcome.get("outcome_status") for outcome in outcome_reflection.get("outcomes", [])]
    return {
        "record_type": "case_result",
        "case_id": case.get("case_id", f"case-{index}"),
        "user_message": case["user_message"],
        "expected_intent": case["expected_intent"],
        "actual_intent": packet.get("intent"),
        "intent_hit": packet.get("intent") == case["expected_intent"],
        "expected_recalled_terms": case["expected_recalled_terms"],
        "recall_scope": "long_term_memory_groups",
        "missing_recalled_terms": missing_terms,
        "recall_hit": not missing_terms,
        "expected_safety_rule": expected_rule,
        "matched_safety_rules": rule_ids,
        "safety_rule_hit": safety_rule_hit,
        "expected_memory_fact_kind": expected_fact_kind,
        "memory_fact_kind_hit": None if expected_fact_kind is None else expected_fact_kind in reflected_fact_kinds,
        "expected_outcome_status": expected_outcome_status,
        "outcome_status_hit": None if expected_outcome_status is None else expected_outcome_status in reflected_statuses,
        "expected_response_terms": case.get("expected_response_terms") or [],
        "missing_response_terms": missing_response_terms,
        "response_strategy_hit": None if not expected_response_terms else not missing_response_terms,
        "outcome_reflection": outcome_reflection,
        "should_not_include": case["should_not_include"],
        "wrong_terms": wrong_terms,
        "wrong_memory_hit": bool(wrong_terms),
        "context_summary": packet.get("context_summary", ""),
        "retrieval_debug": packet.get("retrieval_debug", {}),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    safety_records = [record for record in records if record["safety_rule_hit"] is not None]
    outcome_records = [record for record in records if record["outcome_status_hit"] is not None]
    strategy_records = [record for record in records if record["memory_fact_kind_hit"] is not None]
    response_records = [record for record in records if record["response_strategy_hit"] is not None]
    failed_strategy_records = [
        record
        for record in records
        if record.get("expected_memory_fact_kind") == "failed_strategy"
    ]
    return {
        "record_type": "summary",
        "total_cases": total,
        "recall_hit_rate": round(sum(1 for record in records if record["recall_hit"]) / total, 4),
        "intent_hit_rate": round(sum(1 for record in records if record["intent_hit"]) / total, 4),
        "safety_rule_hit_rate": (
            round(sum(1 for record in safety_records if record["safety_rule_hit"]) / len(safety_records), 4)
            if safety_records
            else None
        ),
        "outcome_recall_hit_rate": (
            round(sum(1 for record in outcome_records if record["outcome_status_hit"]) / len(outcome_records), 4)
            if outcome_records
            else None
        ),
        "strategy_reuse_hit_rate": (
            round(sum(1 for record in strategy_records if record["memory_fact_kind_hit"]) / len(strategy_records), 4)
            if strategy_records
            else None
        ),
        "failed_strategy_avoidance_rate": (
            round(
                sum(
                    1
                    for record in failed_strategy_records
                    if record["memory_fact_kind_hit"]
                    and record["outcome_status_hit"]
                    and not record["wrong_memory_hit"]
                )
                / len(failed_strategy_records),
                4,
            )
            if failed_strategy_records
            else None
        ),
        "response_strategy_hit_rate": (
            round(sum(1 for record in response_records if record["response_strategy_hit"]) / len(response_records), 4)
            if response_records
            else None
        ),
        "wrong_memory_rate": round(sum(1 for record in records if record["wrong_memory_hit"]) / total, 4),
    }


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return REPO_ROOT / "logs" / "experiments" / f"memory-eval-{timestamp}.jsonl"


def main() -> int:
    args = parse_args()
    cases = load_cases(Path(args.cases))
    records = [evaluate_case(case, index) for index, case in enumerate(cases)]
    summary = summarize(records)
    output_path = Path(args.output) if args.output else default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        handle.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({"output": str(output_path), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
