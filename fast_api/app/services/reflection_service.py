import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.memory_system import MemoryManager


class ReflectionService:
    """Create conservative Hindsight observation/opinion memories from recent records."""

    def __init__(self, db: Session, memory_manager: MemoryManager | None = None):
        self.db = db
        self.memory_manager = memory_manager or MemoryManager(db)

    def reflect_user_memory(self, user_id: uuid.UUID) -> dict[str, Any]:
        since_date = date.today() - timedelta(days=7)
        since_dt = datetime.utcnow() - timedelta(days=7)
        recovery_logs = self._recent_recovery_logs(user_id, since_date)
        symptom_logs = self._recent_symptom_logs(user_id, since_date)
        nutrition_summaries = self._recent_nutrition_summaries(user_id, since_date)
        workout_logs = self._recent_workout_logs(user_id, since_dt)
        risk_notes = self.memory_manager.get_active_risk_notes(user_id)
        decisions = self._recent_agent_decisions(user_id, since_dt)
        memories = self.memory_manager.search_memories(user_id, "recent coaching pattern", top_k=5)

        created: list[models.LongTermMemory] = []
        observation = self._build_observation(
            recovery_logs,
            symptom_logs,
            nutrition_summaries,
            workout_logs,
            risk_notes,
        )
        if observation:
            created.append(
                self.memory_manager.retain_memory(
                    user_id=user_id,
                    content=observation["content"],
                    memory_network="observation",
                    fact_kind="coach_observation",
                    category=observation["category"],
                    evidence=observation["evidence"],
                    importance_score=0.68,
                    confidence_score=0.75,
                    source_type="reflection",
                )
            )

        opinion = self._build_opinion(recovery_logs, symptom_logs, risk_notes, decisions, memories)
        if opinion:
            created.append(
                self.memory_manager.retain_memory(
                    user_id=user_id,
                    content=opinion["content"],
                    memory_network="opinion",
                    fact_kind="coach_opinion",
                    category=opinion["category"],
                    evidence=opinion["evidence"],
                    importance_score=0.7,
                    confidence_score=0.8,
                    source_type="reflection",
                )
            )
        return {
            "created_count": len(created),
            "memories": [self._memory_summary(memory) for memory in created],
        }

    def reflect_weekly(self, user_id: uuid.UUID, week_start: date, week_end: date) -> dict[str, Any]:
        start_dt = datetime.combine(week_start, datetime.min.time())
        end_dt = datetime.combine(week_end, datetime.max.time())
        workout_logs = self._workout_logs_between(user_id, start_dt, end_dt)
        exercise_logs = self._exercise_logs_between(user_id, start_dt, end_dt)
        nutrition_summaries = self._nutrition_summaries_between(user_id, week_start, week_end)
        recovery_logs = self._recovery_logs_between(user_id, week_start, week_end)
        symptom_logs = self._symptom_logs_between(user_id, week_start, week_end)
        decisions = self._agent_decisions_between(user_id, start_dt, end_dt)

        created: list[models.LongTermMemory] = []
        weekly_specs = [
            self._build_weekly_training_observation(workout_logs, exercise_logs, week_start, week_end),
            self._build_weekly_nutrition_observation(nutrition_summaries, week_start, week_end),
            self._build_weekly_recovery_observation(recovery_logs, symptom_logs, week_start, week_end),
        ]
        for spec in weekly_specs:
            if not spec:
                continue
            created.append(
                self.memory_manager.retain_memory(
                    user_id=user_id,
                    content=spec["content"],
                    memory_network="observation",
                    fact_kind=spec["fact_kind"],
                    category=spec["category"],
                    evidence=spec["evidence"],
                    occurred_start=start_dt,
                    occurred_end=end_dt,
                    importance_score=0.66,
                    confidence_score=0.78,
                    source_type="weekly_reflection",
                )
            )

        opinion = self._build_weekly_opinion(
            workout_logs,
            nutrition_summaries,
            recovery_logs,
            symptom_logs,
            decisions,
            week_start,
            week_end,
        )
        if opinion:
            created.append(
                self.memory_manager.retain_memory(
                    user_id=user_id,
                    content=opinion["content"],
                    memory_network="opinion",
                    fact_kind="coach_opinion",
                    category="weekly",
                    evidence=opinion["evidence"],
                    occurred_start=start_dt,
                    occurred_end=end_dt,
                    importance_score=0.7,
                    confidence_score=opinion["confidence"],
                    source_type="weekly_reflection",
                )
            )
        return {"created_count": len(created), "memories": [self._memory_summary(memory) for memory in created]}

    def reflect_decision_outcomes(
        self,
        user_id: uuid.UUID,
        since_days: int = 14,
        outcome_window_days: int = 7,
    ) -> dict[str, Any]:
        from fast_api.app.services.outcome_reflection_service import OutcomeReflectionService

        return OutcomeReflectionService(self.db, self.memory_manager).reflect_recent_decision_outcomes(
            user_id=user_id,
            since_days=since_days,
            outcome_window_days=outcome_window_days,
        )

    def _recent_recovery_logs(self, user_id: uuid.UUID, since: date) -> list[models.RecoveryLog]:
        return list(
            self.db.scalars(
                select(models.RecoveryLog)
                .where(models.RecoveryLog.user_id == user_id, models.RecoveryLog.log_date >= since)
                .order_by(desc(models.RecoveryLog.log_date))
            )
        )

    def _recent_symptom_logs(self, user_id: uuid.UUID, since: date) -> list[models.SymptomLog]:
        return list(
            self.db.scalars(
                select(models.SymptomLog)
                .where(models.SymptomLog.user_id == user_id, models.SymptomLog.symptom_date >= since)
                .order_by(desc(models.SymptomLog.symptom_date))
            )
        )

    def _recent_nutrition_summaries(self, user_id: uuid.UUID, since: date) -> list[models.NutritionDailySummary]:
        return list(
            self.db.scalars(
                select(models.NutritionDailySummary)
                .where(models.NutritionDailySummary.user_id == user_id, models.NutritionDailySummary.summary_date >= since)
                .order_by(desc(models.NutritionDailySummary.summary_date))
            )
        )

    def _recent_workout_logs(self, user_id: uuid.UUID, since: datetime) -> list[models.WorkoutLog]:
        return list(
            self.db.scalars(
                select(models.WorkoutLog)
                .where(models.WorkoutLog.user_id == user_id, models.WorkoutLog.performed_at >= since)
                .order_by(desc(models.WorkoutLog.performed_at))
            )
        )

    def _recent_agent_decisions(self, user_id: uuid.UUID, since: datetime) -> list[models.AgentDecision]:
        return list(
            self.db.scalars(
                select(models.AgentDecision)
                .where(models.AgentDecision.user_id == user_id, models.AgentDecision.created_at >= since)
                .order_by(desc(models.AgentDecision.created_at))
                .limit(10)
            )
        )

    def _workout_logs_between(self, user_id: uuid.UUID, start: datetime, end: datetime) -> list[models.WorkoutLog]:
        return list(
            self.db.scalars(
                select(models.WorkoutLog)
                .where(models.WorkoutLog.user_id == user_id, models.WorkoutLog.performed_at >= start, models.WorkoutLog.performed_at <= end)
                .order_by(desc(models.WorkoutLog.performed_at))
            )
        )

    def _exercise_logs_between(self, user_id: uuid.UUID, start: datetime, end: datetime) -> list[models.ExerciseLog]:
        return list(
            self.db.scalars(
                select(models.ExerciseLog)
                .where(models.ExerciseLog.user_id == user_id, models.ExerciseLog.created_at >= start, models.ExerciseLog.created_at <= end)
                .order_by(desc(models.ExerciseLog.created_at))
            )
        )

    def _nutrition_summaries_between(self, user_id: uuid.UUID, start: date, end: date) -> list[models.NutritionDailySummary]:
        return list(
            self.db.scalars(
                select(models.NutritionDailySummary)
                .where(models.NutritionDailySummary.user_id == user_id, models.NutritionDailySummary.summary_date >= start, models.NutritionDailySummary.summary_date <= end)
                .order_by(desc(models.NutritionDailySummary.summary_date))
            )
        )

    def _recovery_logs_between(self, user_id: uuid.UUID, start: date, end: date) -> list[models.RecoveryLog]:
        return list(
            self.db.scalars(
                select(models.RecoveryLog)
                .where(models.RecoveryLog.user_id == user_id, models.RecoveryLog.log_date >= start, models.RecoveryLog.log_date <= end)
                .order_by(desc(models.RecoveryLog.log_date))
            )
        )

    def _symptom_logs_between(self, user_id: uuid.UUID, start: date, end: date) -> list[models.SymptomLog]:
        return list(
            self.db.scalars(
                select(models.SymptomLog)
                .where(models.SymptomLog.user_id == user_id, models.SymptomLog.symptom_date >= start, models.SymptomLog.symptom_date <= end)
                .order_by(desc(models.SymptomLog.symptom_date))
            )
        )

    def _agent_decisions_between(self, user_id: uuid.UUID, start: datetime, end: datetime) -> list[models.AgentDecision]:
        return list(
            self.db.scalars(
                select(models.AgentDecision)
                .where(models.AgentDecision.user_id == user_id, models.AgentDecision.created_at >= start, models.AgentDecision.created_at <= end)
                .order_by(desc(models.AgentDecision.created_at))
                .limit(20)
            )
        )

    def _build_observation(
        self,
        recovery_logs: list[models.RecoveryLog],
        symptom_logs: list[models.SymptomLog],
        nutrition_summaries: list[models.NutritionDailySummary],
        workout_logs: list[models.WorkoutLog],
        risk_notes: list[models.RiskNote],
    ) -> dict[str, Any] | None:
        evidence = self._evidence_for(recovery_logs, "recovery_logs")
        evidence += self._evidence_for(symptom_logs, "symptom_logs")
        evidence += self._evidence_for(nutrition_summaries, "nutrition_daily_summaries")
        evidence += self._evidence_for(workout_logs, "workout_logs")
        evidence += self._evidence_for(risk_notes, "risk_notes")
        if not evidence:
            return None
        avg_sleep = self._average([log.sleep_hours for log in recovery_logs if log.sleep_hours is not None])
        avg_fatigue = self._average([log.fatigue_score for log in recovery_logs if log.fatigue_score is not None])
        symptom_count = len(symptom_logs)
        workout_count = len(workout_logs)
        parts = [f"Recent 7-day pattern: workouts={workout_count}, symptoms={symptom_count}."]
        category = "training"
        if avg_sleep is not None or avg_fatigue is not None:
            category = "recovery"
            parts.append(f"Average sleep={avg_sleep if avg_sleep is not None else 'unknown'}h.")
            parts.append(f"Average fatigue={avg_fatigue if avg_fatigue is not None else 'unknown'}.")
        if risk_notes:
            category = "risk"
            parts.append("Active risk notes are present, so coaching should stay conservative.")
        return {"category": category, "content": " ".join(parts), "evidence": evidence[:20]}

    def _build_opinion(
        self,
        recovery_logs: list[models.RecoveryLog],
        symptom_logs: list[models.SymptomLog],
        risk_notes: list[models.RiskNote],
        decisions: list[models.AgentDecision],
        memories: list[models.LongTermMemory],
    ) -> dict[str, Any] | None:
        evidence = self._evidence_for(recovery_logs, "recovery_logs")
        evidence += self._evidence_for(symptom_logs, "symptom_logs")
        evidence += self._evidence_for(risk_notes, "risk_notes")
        evidence += self._evidence_for(decisions, "agent_decisions")
        evidence += self._evidence_for(memories, "long_term_memories")
        if not evidence:
            return None
        avg_fatigue = self._average([log.fatigue_score for log in recovery_logs if log.fatigue_score is not None])
        if risk_notes or symptom_logs or (avg_fatigue is not None and avg_fatigue >= 7):
            content = (
                "Based on recent recovery, symptoms, and risk evidence, the agent should prefer conservative "
                "progression over frequent max-load attempts."
            )
            category = "training"
        else:
            content = (
                "Based on recent records, the agent can use normal progressive coaching while continuing to "
                "monitor recovery and adherence."
            )
            category = "training"
        return {"category": category, "content": content, "evidence": evidence[:20]}

    def _build_weekly_training_observation(
        self,
        workout_logs: list[models.WorkoutLog],
        exercise_logs: list[models.ExerciseLog],
        week_start: date,
        week_end: date,
    ) -> dict[str, Any] | None:
        evidence = self._evidence_for(workout_logs, "workout_logs") + self._evidence_for(exercise_logs, "exercise_logs")
        if not evidence:
            return None
        avg_rpe = self._average([log.rpe for log in workout_logs if log.rpe is not None])
        content = f"Weekly training observation {week_start} to {week_end}: workouts={len(workout_logs)}, exercise_sets={len(exercise_logs)}, avg_rpe={avg_rpe if avg_rpe is not None else 'unknown'}."
        return {"fact_kind": "weekly_training_observation", "category": "training", "content": content, "evidence": evidence[:30]}

    def _build_weekly_nutrition_observation(
        self,
        summaries: list[models.NutritionDailySummary],
        week_start: date,
        week_end: date,
    ) -> dict[str, Any] | None:
        evidence = self._evidence_for(summaries, "nutrition_daily_summaries")
        if not evidence:
            return None
        avg_adherence = self._average([summary.adherence_score for summary in summaries if summary.adherence_score is not None])
        avg_protein = self._average([summary.total_protein_g for summary in summaries if summary.total_protein_g is not None])
        content = f"Weekly nutrition observation {week_start} to {week_end}: nutrition_days={len(summaries)}, avg_adherence={avg_adherence if avg_adherence is not None else 'unknown'}, avg_protein_g={avg_protein if avg_protein is not None else 'unknown'}."
        return {"fact_kind": "weekly_nutrition_observation", "category": "nutrition", "content": content, "evidence": evidence[:30]}

    def _build_weekly_recovery_observation(
        self,
        recovery_logs: list[models.RecoveryLog],
        symptom_logs: list[models.SymptomLog],
        week_start: date,
        week_end: date,
    ) -> dict[str, Any] | None:
        evidence = self._evidence_for(recovery_logs, "recovery_logs") + self._evidence_for(symptom_logs, "symptom_logs")
        if not evidence:
            return None
        avg_sleep = self._average([log.sleep_hours for log in recovery_logs if log.sleep_hours is not None])
        avg_fatigue = self._average([log.fatigue_score for log in recovery_logs if log.fatigue_score is not None])
        content = f"Weekly recovery observation {week_start} to {week_end}: recovery_days={len(recovery_logs)}, symptoms={len(symptom_logs)}, avg_sleep={avg_sleep if avg_sleep is not None else 'unknown'}h, avg_fatigue={avg_fatigue if avg_fatigue is not None else 'unknown'}."
        return {"fact_kind": "weekly_recovery_observation", "category": "recovery", "content": content, "evidence": evidence[:30]}

    def _build_weekly_opinion(
        self,
        workout_logs: list[models.WorkoutLog],
        nutrition_summaries: list[models.NutritionDailySummary],
        recovery_logs: list[models.RecoveryLog],
        symptom_logs: list[models.SymptomLog],
        decisions: list[models.AgentDecision],
        week_start: date,
        week_end: date,
    ) -> dict[str, Any] | None:
        evidence = (
            self._evidence_for(workout_logs, "workout_logs")
            + self._evidence_for(nutrition_summaries, "nutrition_daily_summaries")
            + self._evidence_for(recovery_logs, "recovery_logs")
            + self._evidence_for(symptom_logs, "symptom_logs")
            + self._evidence_for(decisions, "agent_decisions")
        )
        enough_evidence = len(workout_logs) >= 3 and len(nutrition_summaries) >= 4 and len(recovery_logs) >= 4
        if not enough_evidence or not evidence:
            return None
        avg_fatigue = self._average([log.fatigue_score for log in recovery_logs if log.fatigue_score is not None])
        if symptom_logs or (avg_fatigue is not None and avg_fatigue >= 7):
            content = f"Weekly coach opinion {week_start} to {week_end}: favor conservative progression next week because recovery or symptom evidence is elevated."
        else:
            content = f"Weekly coach opinion {week_start} to {week_end}: normal progression appears reasonable if current state remains consistent with the evidence."
        return {"content": content, "evidence": evidence[:40], "confidence": 0.82}

    def _evidence_for(self, rows: list[Any], table: str) -> list[dict[str, str]]:
        return [
            {
                "table": table,
                "id": str(row.id),
                "summary": self._evidence_summary(row),
                "time": self._evidence_time(row),
            }
            for row in rows
            if getattr(row, "id", None)
        ]

    def _evidence_summary(self, row: Any) -> str:
        for attr in ("summary_text", "notes", "description", "reason", "decision_result", "workout_name", "symptom_type", "exercise_name"):
            value = getattr(row, attr, None)
            if value:
                return str(value)[:180]
        return row.__class__.__name__

    def _evidence_time(self, row: Any) -> str:
        for attr in ("performed_at", "summary_date", "log_date", "symptom_date", "created_at", "updated_at"):
            value = getattr(row, attr, None)
            if value:
                return value.isoformat() if hasattr(value, "isoformat") else str(value)
        return datetime.utcnow().isoformat()

    def _average(self, values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(float(value) for value in values) / len(values), 2)

    def _memory_summary(self, memory: models.LongTermMemory) -> dict[str, Any]:
        return {
            "id": str(memory.id),
            "memory_network": memory.memory_network,
            "fact_kind": memory.fact_kind,
            "category": memory.category,
            "summary": memory.summary,
            "evidence": memory.evidence or [],
        }
