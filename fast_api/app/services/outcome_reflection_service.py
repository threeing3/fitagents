import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.memory_system import MemoryManager


class OutcomeReflectionService:
    """Link agent decisions to later logs and retain outcome-aware experience memories."""

    def __init__(self, db: Session, memory_manager: MemoryManager | None = None):
        self.db = db
        self.memory_manager = memory_manager or MemoryManager(db)

    def reflect_recent_decision_outcomes(
        self,
        user_id: uuid.UUID,
        since_days: int = 14,
        outcome_window_days: int = 7,
        limit: int = 20,
    ) -> dict[str, Any]:
        since = datetime.utcnow() - timedelta(days=since_days)
        decisions = list(
            self.db.scalars(
                select(models.AgentDecision)
                .where(models.AgentDecision.user_id == user_id, models.AgentDecision.created_at >= since)
                .order_by(models.AgentDecision.created_at)
                .limit(limit)
            )
        )
        created_outcomes: list[models.DecisionOutcome] = []
        created_memories: list[models.LongTermMemory] = []
        skipped: list[dict[str, str]] = []
        for decision in decisions:
            if self._existing_outcome(decision.id):
                skipped.append({"decision_id": str(decision.id), "reason": "outcome_already_exists"})
                continue
            spec = self._build_outcome_spec(decision, outcome_window_days)
            if spec is None:
                skipped.append({"decision_id": str(decision.id), "reason": "insufficient_followup_evidence"})
                continue
            outcome = models.DecisionOutcome(
                user_id=user_id,
                decision_id=decision.id,
                outcome_type=spec["outcome_type"],
                outcome_status=spec["outcome_status"],
                outcome_summary=spec["outcome_summary"],
                metrics=spec["metrics"],
                evidence=spec["evidence"],
                observed_start_at=spec["observed_start_at"],
                observed_end_at=spec["observed_end_at"],
                confidence_score=spec["confidence_score"],
            )
            self.db.add(outcome)
            self.db.flush()
            memory = self._retain_outcome_memory(decision, outcome, spec)
            outcome.reflected_memory_id = memory.id
            created_outcomes.append(outcome)
            created_memories.append(memory)
        self.db.flush()
        return {
            "created_count": len(created_outcomes),
            "skipped_count": len(skipped),
            "outcomes": [self._outcome_summary(outcome) for outcome in created_outcomes],
            "memories": [self._memory_summary(memory) for memory in created_memories],
            "skipped": skipped,
        }

    def _existing_outcome(self, decision_id: uuid.UUID) -> models.DecisionOutcome | None:
        return self.db.scalar(
            select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision_id)
        )

    def _build_outcome_spec(
        self,
        decision: models.AgentDecision,
        outcome_window_days: int,
    ) -> dict[str, Any] | None:
        start = decision.created_at or datetime.utcnow()
        end = start + timedelta(days=outcome_window_days)
        decision_type = (decision.decision_type or "").lower()
        if any(term in decision_type for term in ["nutrition", "diet", "meal"]):
            return self._build_nutrition_outcome(decision, start, end)
        return self._build_training_or_risk_outcome(decision, start, end)

    def _build_training_or_risk_outcome(
        self,
        decision: models.AgentDecision,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any] | None:
        workouts = list(
            self.db.scalars(
                select(models.WorkoutLog)
                .where(models.WorkoutLog.user_id == decision.user_id, models.WorkoutLog.performed_at >= start, models.WorkoutLog.performed_at <= end)
                .order_by(models.WorkoutLog.performed_at)
            )
        )
        recovery = list(
            self.db.scalars(
                select(models.RecoveryLog)
                .where(models.RecoveryLog.user_id == decision.user_id, models.RecoveryLog.log_date >= start.date(), models.RecoveryLog.log_date <= end.date())
                .order_by(models.RecoveryLog.log_date)
            )
        )
        symptoms = list(
            self.db.scalars(
                select(models.SymptomLog)
                .where(models.SymptomLog.user_id == decision.user_id, models.SymptomLog.symptom_date >= start.date(), models.SymptomLog.symptom_date <= end.date())
                .order_by(models.SymptomLog.symptom_date)
            )
        )
        evidence = (
            self._evidence_for([decision], "agent_decisions")
            + self._evidence_for(workouts, "workout_logs")
            + self._evidence_for(recovery, "recovery_logs")
            + self._evidence_for(symptoms, "symptom_logs")
        )
        if len(evidence) <= 1:
            return None
        avg_completion = self._average([log.completion_rate for log in workouts if log.completion_rate is not None])
        avg_rpe = self._average([log.rpe for log in workouts if log.rpe is not None])
        avg_fatigue = self._average([log.fatigue_score for log in recovery if log.fatigue_score is not None])
        max_symptom = self._max_value([log.severity_score for log in symptoms if log.severity_score is not None])
        metrics = {
            "workout_count": len(workouts),
            "symptom_count": len(symptoms),
            "avg_completion_rate": avg_completion,
            "avg_rpe": avg_rpe,
            "avg_fatigue_score": avg_fatigue,
            "max_symptom_severity": max_symptom,
        }
        status = self._training_status(avg_completion, avg_fatigue, max_symptom)
        summary = (
            f"Decision outcome for {decision.decision_type}: status={status}, "
            f"workouts={len(workouts)}, symptoms={len(symptoms)}, avg_completion={avg_completion}, "
            f"avg_fatigue={avg_fatigue}, max_symptom={max_symptom}."
        )
        return self._spec("training_outcome", status, summary, metrics, evidence, start, end)

    def _build_nutrition_outcome(
        self,
        decision: models.AgentDecision,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any] | None:
        summaries = list(
            self.db.scalars(
                select(models.NutritionDailySummary)
                .where(models.NutritionDailySummary.user_id == decision.user_id, models.NutritionDailySummary.summary_date >= start.date(), models.NutritionDailySummary.summary_date <= end.date())
                .order_by(models.NutritionDailySummary.summary_date)
            )
        )
        evidence = self._evidence_for([decision], "agent_decisions") + self._evidence_for(summaries, "nutrition_daily_summaries")
        if len(evidence) <= 1:
            return None
        avg_adherence = self._average([item.adherence_score for item in summaries if item.adherence_score is not None])
        avg_protein = self._average([item.total_protein_g for item in summaries if item.total_protein_g is not None])
        avg_target_protein = self._average([item.target_protein_g for item in summaries if item.target_protein_g is not None])
        protein_target_ratio = None
        if avg_protein is not None and avg_target_protein:
            protein_target_ratio = round(avg_protein / avg_target_protein, 3)
        metrics = {
            "nutrition_days": len(summaries),
            "avg_adherence_score": avg_adherence,
            "avg_protein_g": avg_protein,
            "avg_target_protein_g": avg_target_protein,
            "protein_target_ratio": protein_target_ratio,
        }
        status = self._nutrition_status(avg_adherence, protein_target_ratio)
        summary = (
            f"Decision outcome for {decision.decision_type}: status={status}, "
            f"nutrition_days={len(summaries)}, avg_adherence={avg_adherence}, "
            f"protein_target_ratio={protein_target_ratio}."
        )
        return self._spec("nutrition_outcome", status, summary, metrics, evidence, start, end)

    def _retain_outcome_memory(
        self,
        decision: models.AgentDecision,
        outcome: models.DecisionOutcome,
        spec: dict[str, Any],
    ) -> models.LongTermMemory:
        successful = outcome.outcome_status in {"improved", "successful"}
        failed = outcome.outcome_status in {"worse", "failed"}
        fact_kind = "failed_strategy" if failed else "strategy_experience"
        importance = 0.78 if successful or failed else 0.62
        confidence = float(spec["confidence_score"])
        content = (
            f"Outcome-aware coaching experience: decision_type={decision.decision_type}; "
            f"decision={decision.decision_result}; outcome_status={outcome.outcome_status}; "
            f"outcome_summary={outcome.outcome_summary}"
        )
        evidence = [
            {"table": "decision_outcomes", "id": str(outcome.id), "summary": outcome.outcome_summary, "time": datetime.utcnow().isoformat()},
            *spec["evidence"],
        ]
        return self.memory_manager.retain_memory(
            user_id=decision.user_id,
            content=content,
            memory_network="experience",
            fact_kind=fact_kind,
            category=self._category_for_outcome(outcome.outcome_type),
            evidence=evidence[:40],
            occurred_start=outcome.observed_start_at,
            occurred_end=outcome.observed_end_at,
            importance_score=importance,
            confidence_score=confidence,
            source_type="decision_outcome",
        )

    def _category_for_outcome(self, outcome_type: str) -> str:
        if outcome_type == "nutrition_outcome":
            return "nutrition"
        return "training"

    def _training_status(
        self,
        avg_completion: float | None,
        avg_fatigue: float | None,
        max_symptom: float | None,
    ) -> str:
        if max_symptom is not None and max_symptom >= 7:
            return "worse"
        if avg_fatigue is not None and avg_fatigue >= 8:
            return "worse"
        if avg_completion is not None and avg_completion < 0.5:
            return "worse"
        if (avg_completion is not None and avg_completion >= 0.75) and (max_symptom is None or max_symptom <= 3) and (avg_fatigue is None or avg_fatigue <= 6.5):
            return "improved"
        return "neutral"

    def _nutrition_status(self, avg_adherence: float | None, protein_target_ratio: float | None) -> str:
        if avg_adherence is not None and avg_adherence < 0.45:
            return "worse"
        if avg_adherence is not None and avg_adherence >= 0.75:
            return "improved"
        if protein_target_ratio is not None and protein_target_ratio >= 0.9:
            return "improved"
        return "neutral"

    def _spec(
        self,
        outcome_type: str,
        status: str,
        summary: str,
        metrics: dict[str, Any],
        evidence: list[dict[str, str]],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        confidence = 0.82 if status in {"improved", "worse"} else 0.68
        return {
            "outcome_type": outcome_type,
            "outcome_status": status,
            "outcome_summary": summary,
            "metrics": metrics,
            "evidence": evidence[:30],
            "observed_start_at": start,
            "observed_end_at": end,
            "confidence_score": confidence,
        }

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
        for attr in ("outcome_summary", "summary_text", "notes", "description", "reason", "decision_result", "workout_name", "symptom_type"):
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
        return round(sum(float(value) for value in values) / len(values), 3)

    def _max_value(self, values: list[float]) -> float | None:
        if not values:
            return None
        return max(float(value) for value in values)

    def _outcome_summary(self, outcome: models.DecisionOutcome) -> dict[str, Any]:
        return {
            "id": str(outcome.id),
            "decision_id": str(outcome.decision_id),
            "outcome_type": outcome.outcome_type,
            "outcome_status": outcome.outcome_status,
            "outcome_summary": outcome.outcome_summary,
            "metrics": outcome.metrics or {},
            "evidence": outcome.evidence or [],
            "reflected_memory_id": str(outcome.reflected_memory_id) if outcome.reflected_memory_id else None,
        }

    def _memory_summary(self, memory: models.LongTermMemory) -> dict[str, Any]:
        return {
            "id": str(memory.id),
            "memory_network": memory.memory_network,
            "fact_kind": memory.fact_kind,
            "category": memory.category,
            "summary": memory.summary,
            "evidence": memory.evidence or [],
        }

