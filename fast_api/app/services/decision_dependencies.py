"""Preserve historical outcomes while revoking stale current applicability."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models

PROFILE_FIELDS = (
    "goal",
    "injuries",
    "equipment_available",
    "workout_frequency",
    "workout_duration",
    "experience_level",
    "dietary_preferences",
    "allergies",
)
RECOVERY_FIELDS = ("sleep_hours", "fatigue_score", "soreness_score", "stress_score", "notes")


class DecisionDependencyService:
    """Caller owns transaction. Invalidated history is never silently reactivated."""

    def __init__(self, db: Session):
        self.db = db

    def capture(self, user_id: uuid.UUID, context: dict[str, Any]) -> dict[str, Any]:
        profile = self.db.get(models.UserProfile, user_id)
        result: dict[str, Any] = {"version": 1}
        result["risks"] = self._active_risks(user_id)
        if profile is not None:
            result["profile"] = {
                key: copy.deepcopy(getattr(profile, key)) for key in PROFILE_FIELDS
            }
        raw_id = context.get("baseline_recovery_log_id")
        if raw_id:
            try:
                recovery = self.db.get(models.RecoveryLog, uuid.UUID(str(raw_id)))
            except (ValueError, TypeError):
                recovery = None
            if recovery is not None and recovery.user_id == user_id:
                result["recovery"] = {
                    "id": str(recovery.id),
                    **{key: getattr(recovery, key) for key in RECOVERY_FIELDS},
                }
        return result

    def _active_risks(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            {
                "id": str(row.id),
                "description": row.description,
                "severity": row.severity_score,
                "status": row.status,
            }
            for row in self.db.scalars(
                select(models.RiskNote)
                .where(
                    models.RiskNote.user_id == user_id,
                    models.RiskNote.status.in_(["active", "monitoring"]),
                )
                .order_by(models.RiskNote.id)
                .execution_options(populate_existing=True)
            )
        ]

    def changed_fields(self, decision: models.AgentDecision) -> list[str]:
        context = decision.context_used or {}
        dependencies = context.get("domain_dependencies") or {}
        # Legacy records may have explicit profile evidence. Do not invent it.
        baseline = dependencies.get("profile")
        if baseline is None:
            baseline = context.get("profile") or (context.get("dependencies") or {}).get("profile")
        profile = self.db.get(models.UserProfile, decision.user_id)
        changes = []
        if "risks" in dependencies and dependencies["risks"] != self._active_risks(
            decision.user_id
        ):
            changes.append("risk_evidence.changed")
        if isinstance(baseline, dict):
            changes.extend(
                "profile." + key
                for key in PROFILE_FIELDS
                if key in baseline and (profile is None or baseline[key] != getattr(profile, key))
            )
        recovery = dependencies.get("recovery")
        if isinstance(recovery, dict):
            try:
                row = self.db.get(
                    models.RecoveryLog, uuid.UUID(str(recovery.get("id"))), populate_existing=True
                )
            except (ValueError, TypeError):
                row = None
            if row is None or row.user_id != decision.user_id:
                changes.append("baseline_recovery.missing")
            else:
                changes.extend(
                    "baseline_recovery." + key
                    for key in RECOVERY_FIELDS
                    if recovery.get(key) != getattr(row, key)
                )
        return changes

    @staticmethod
    def invalidated(decision: models.AgentDecision) -> bool:
        return (decision.context_used or {}).get("dependency_validity", {}).get(
            "status"
        ) == "invalidated"

    def invalidate_changed(
        self, user_id: uuid.UUID, corrected_source_ids: list[str] | None = None
    ) -> list[str]:
        from fast_api.app.services.plan_writes import lock_plan_owner

        lock_plan_owner(self.db, user_id)
        self.db.flush()
        profile = self.db.get(models.UserProfile, user_id)
        if profile is not None:
            self.db.refresh(profile)
        decisions = self.db.scalars(
            select(models.AgentDecision)
            .where(models.AgentDecision.user_id == user_id)
            .execution_options(populate_existing=True)
        ).all()
        invalidated = []
        affected_categories: set[str] = set()
        sources = set(map(str, corrected_source_ids or []))
        for decision in decisions:
            if self.invalidated(decision):
                continue
            changes = self.changed_fields(decision)
            if sources and self._references(decision.context_used, sources):
                changes.append("source.corrected")
            if not changes:
                continue
            validity = {
                "status": "invalidated",
                "changed_fields": changes,
                "at": datetime.now(timezone.utc).isoformat(),
                "historical_result_preserved": True,
                "recompute_policy": "new_decision_required_no_automatic_relabel",
            }
            decision.context_used = {
                **(decision.context_used or {}),
                "dependency_validity": validity,
            }
            evaluations = self.db.scalars(
                select(models.DecisionEvaluationPlan).where(
                    models.DecisionEvaluationPlan.user_id == user_id,
                    models.DecisionEvaluationPlan.decision_id == decision.id,
                )
            ).all()
            for evaluation in evaluations:
                evaluation.evidence_snapshot = {
                    **(evaluation.evidence_snapshot or {}),
                    "dependency_validity": validity,
                }
                if evaluation.status in {"scheduled", "collecting", "waiting_user", "ready"}:
                    evaluation.status = "invalidated"
                    evaluation.outcome_status = "dependencies_changed"
                for followup in self.db.scalars(
                    select(models.DecisionFollowup).where(
                        models.DecisionFollowup.user_id == user_id,
                        models.DecisionFollowup.evaluation_plan_id == evaluation.id,
                        models.DecisionFollowup.status == "pending",
                    )
                ):
                    followup.status = "cancelled"
            for outcome in self.db.scalars(
                select(models.DecisionOutcome).where(
                    models.DecisionOutcome.user_id == user_id,
                    models.DecisionOutcome.decision_id == decision.id,
                )
            ):
                outcome.metrics = {**(outcome.metrics or {}), "dependency_validity": validity}
                if outcome.reflected_memory_id:
                    memory = self.db.get(models.LongTermMemory, outcome.reflected_memory_id)
                    if memory is not None and memory.user_id == user_id:
                        memory.status = "superseded"
                        memory.memory_metadata = {
                            **(memory.memory_metadata or {}),
                            "dependency_validity": validity,
                        }
                        if memory.category:
                            affected_categories.add(memory.category)
            invalidated.append(str(decision.id))
        self.db.flush()
        if invalidated:
            from fast_api.app.services.memory_dependencies import invalidate_derived_memories

            invalidate_derived_memories(
                self.db,
                user_id,
                [{"table": "agent_decisions", "id": value} for value in invalidated],
                "decision_dependencies_changed",
            )
        if affected_categories:
            from fast_api.app.services.memory_system import MemoryManager

            manager = MemoryManager(self.db)
            for category in affected_categories:
                manager.update_memory_catalog(user_id, category)
        return invalidated

    @classmethod
    def _references(cls, value: Any, sources: set[str]) -> bool:
        if isinstance(value, dict):
            return any(cls._references(item, sources) for item in value.values())
        if isinstance(value, list):
            return any(cls._references(item, sources) for item in value)
        return isinstance(value, str) and value in sources
