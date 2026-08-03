from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.outcome_reflection_service import OutcomeReflectionService


ACTIVE_STATUSES = {"scheduled", "collecting", "waiting_user", "ready"}


class DecisionEvaluationService:
    """Plan, collect, and finalize outcome evaluation for durable agent decisions."""

    def __init__(self, db: Session):
        self.db = db

    def create_for_decision(self, decision: models.AgentDecision) -> models.DecisionEvaluationPlan:
        existing = self.db.scalar(
            select(models.DecisionEvaluationPlan).where(
                models.DecisionEvaluationPlan.decision_id == decision.id
            )
        )
        if existing is not None:
            return existing
        spec = self._plan_spec(decision)
        start = decision.created_at or datetime.utcnow()
        plan = models.DecisionEvaluationPlan(
            user_id=decision.user_id,
            decision_id=decision.id,
            status="scheduled",
            evaluation_type=spec["evaluation_type"],
            baseline_snapshot=decision.context_used or {},
            expected_action=spec["expected_action"],
            objective_metrics=spec["objective_metrics"],
            subjective_questions=spec["subjective_questions"],
            minimum_evidence=spec["minimum_evidence"],
            window_start=start,
            window_end=start + timedelta(days=spec["window_days"]),
            next_check_at=start + timedelta(days=spec["first_check_days"]),
        )
        self.db.add(plan)
        self.db.flush()
        return plan

    def on_user_event(self, user_id: uuid.UUID, event_type: str) -> list[dict[str, Any]]:
        plans = list(
            self.db.scalars(
                select(models.DecisionEvaluationPlan)
                .where(
                    models.DecisionEvaluationPlan.user_id == user_id,
                    models.DecisionEvaluationPlan.status.in_(ACTIVE_STATUSES),
                )
                .order_by(models.DecisionEvaluationPlan.window_end)
            )
        )
        return [self.refresh_plan(plan, trigger_type=event_type) for plan in plans]

    def scan_due(self, user_id: uuid.UUID | None = None, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.utcnow()
        filters = [
            models.DecisionEvaluationPlan.status.in_(ACTIVE_STATUSES),
            models.DecisionEvaluationPlan.next_check_at <= now,
        ]
        if user_id is not None:
            filters.append(models.DecisionEvaluationPlan.user_id == user_id)
        plans = list(
            self.db.scalars(
                select(models.DecisionEvaluationPlan)
                .where(*filters)
                .order_by(models.DecisionEvaluationPlan.next_check_at)
                .limit(100)
            )
        )
        results = [self.refresh_plan(plan, trigger_type="scheduled_scan", now=now) for plan in plans]
        return {"processed": len(results), "results": results}

    def refresh_plan(
        self,
        plan: models.DecisionEvaluationPlan,
        trigger_type: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.utcnow()
        decision = self.db.get(models.AgentDecision, plan.decision_id)
        if decision is None:
            plan.status = "cancelled"
            return self._plan_payload(plan, reason="decision_not_found")

        evidence = self._collect_evidence(plan, decision)
        plan.evidence_snapshot = evidence
        plan.last_evidence_at = now if evidence["total_records"] else plan.last_evidence_at
        implementation = self._implementation_status(plan, evidence)
        plan.implementation_status = implementation

        if evidence["safety_escalation"]:
            plan.status = "escalated"
            self._ensure_followup(plan, "safety_check", trigger_type, urgent=True)
            return self._plan_payload(plan, reason="safety_escalation")

        enough_objective = self._has_minimum_evidence(plan, evidence)
        needs_confirmation = bool((plan.expected_action or {}).get("requires_user_confirmation"))
        if enough_objective and (not needs_confirmation or implementation in {"implemented", "partially_implemented"}):
            plan.status = "ready"
            return self._finalize(plan, decision)

        expired = now >= plan.window_end
        if needs_confirmation and plan.followup_count < 2:
            self._ensure_followup(plan, "strategy_execution", trigger_type)
            plan.status = "waiting_user"
            plan.next_check_at = min(plan.window_end, now + timedelta(days=2))
            return self._plan_payload(plan, reason="waiting_for_user_confirmation")

        if expired:
            plan.status = "insufficient_evidence"
            plan.outcome_status = "insufficient_evidence"
            plan.completed_at = now
            return self._plan_payload(plan, reason="evaluation_window_expired")

        plan.status = "collecting"
        plan.next_check_at = min(plan.window_end, now + timedelta(days=1))
        return self._plan_payload(plan, reason="collecting_evidence")

    def answer_followup(
        self,
        followup_id: uuid.UUID,
        user_id: uuid.UUID,
        answer: dict[str, Any],
    ) -> dict[str, Any]:
        followup = self.db.get(models.DecisionFollowup, followup_id)
        if followup is None or followup.user_id != user_id:
            raise ValueError("Decision follow-up not found")
        if followup.status == "answered":
            return self._followup_payload(followup)
        followup.answer_json = answer
        followup.status = "answered"
        followup.answered_at = datetime.utcnow()
        self.db.flush()
        plan = self.db.get(models.DecisionEvaluationPlan, followup.evaluation_plan_id)
        if plan is None:
            raise ValueError("Decision evaluation plan not found")
        implementation = str(answer.get("implementation_status") or "unknown")
        if implementation in {"not_started", "abandoned"}:
            plan.implementation_status = implementation
            plan.status = "insufficient_evidence"
            plan.outcome_status = "not_applicable"
            plan.completed_at = datetime.utcnow()
        else:
            if implementation in {"implemented", "partially_implemented"}:
                plan.implementation_status = implementation
            self.refresh_plan(plan, trigger_type="user_followup")
        self.db.flush()
        return self._followup_payload(followup)

    def list_plans(self, user_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        plans = list(
            self.db.scalars(
                select(models.DecisionEvaluationPlan)
                .where(models.DecisionEvaluationPlan.user_id == user_id)
                .order_by(desc(models.DecisionEvaluationPlan.created_at))
                .limit(max(1, min(limit, 100)))
            )
        )
        return [self._plan_payload(plan) for plan in plans]

    def list_pending_followups(self, user_id: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
        followups = list(
            self.db.scalars(
                select(models.DecisionFollowup)
                .where(
                    models.DecisionFollowup.user_id == user_id,
                    models.DecisionFollowup.status == "pending",
                )
                .order_by(models.DecisionFollowup.scheduled_at)
                .limit(max(1, min(limit, 50)))
            )
        )
        return [self._followup_payload(item) for item in followups]

    def next_followup_for_delivery(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        followup = self.db.scalar(
            select(models.DecisionFollowup)
            .where(
                models.DecisionFollowup.user_id == user_id,
                models.DecisionFollowup.status == "pending",
                models.DecisionFollowup.attempt_count < 2,
                models.DecisionFollowup.scheduled_at <= datetime.utcnow(),
            )
            .order_by(models.DecisionFollowup.scheduled_at)
        )
        if followup is None:
            return None
        followup.attempt_count = (followup.attempt_count or 0) + 1
        followup.sent_at = datetime.utcnow()
        self.db.flush()
        return self._followup_payload(followup)

    def _finalize(
        self,
        plan: models.DecisionEvaluationPlan,
        decision: models.AgentDecision,
    ) -> dict[str, Any]:
        followup_evidence = self._answered_followup_evidence(plan.id)
        followup_answer = self._latest_followup_answer(plan.id)
        result = OutcomeReflectionService(self.db).reflect_decision(
            decision.id,
            implementation_status=plan.implementation_status,
            outcome_window_days=max(1, (plan.window_end - plan.window_start).days),
            extra_evidence=followup_evidence,
            subjective_outcome=followup_answer.get("subjective_outcome"),
            safety_status=followup_answer.get("safety_status"),
        )
        if result["outcome"] is None:
            reason = result["reason"]
            if reason == "outcome_already_exists":
                existing = self.db.scalar(
                    select(models.DecisionOutcome).where(models.DecisionOutcome.decision_id == decision.id)
                )
                plan.status = "completed"
                plan.outcome_status = existing.outcome_status if existing else "completed"
                plan.completed_at = datetime.utcnow()
            elif reason == "insufficient_followup_evidence":
                plan.status = "collecting"
                plan.next_check_at = min(plan.window_end, datetime.utcnow() + timedelta(days=1))
            return self._plan_payload(plan, reason=reason)
        plan.status = "completed"
        plan.outcome_status = result["outcome"].outcome_status
        plan.completed_at = datetime.utcnow()
        return self._plan_payload(plan, reason="outcome_reflected")

    def _collect_evidence(
        self,
        plan: models.DecisionEvaluationPlan,
        decision: models.AgentDecision,
    ) -> dict[str, Any]:
        start, end = plan.window_start, plan.window_end
        workouts = list(self.db.scalars(
            select(models.WorkoutLog).where(
                models.WorkoutLog.user_id == plan.user_id,
                models.WorkoutLog.performed_at >= start,
                models.WorkoutLog.performed_at <= end,
            )
        ))
        recovery = list(self.db.scalars(
            select(models.RecoveryLog).where(
                models.RecoveryLog.user_id == plan.user_id,
                models.RecoveryLog.log_date >= start.date(),
                models.RecoveryLog.log_date <= end.date(),
            )
        ))
        symptoms = list(self.db.scalars(
            select(models.SymptomLog).where(
                models.SymptomLog.user_id == plan.user_id,
                models.SymptomLog.symptom_date >= start.date(),
                models.SymptomLog.symptom_date <= end.date(),
            )
        ))
        nutrition = list(self.db.scalars(
            select(models.NutritionDailySummary).where(
                models.NutritionDailySummary.user_id == plan.user_id,
                models.NutritionDailySummary.summary_date >= start.date(),
                models.NutritionDailySummary.summary_date <= end.date(),
            )
        ))
        max_symptom = max(
            [float(item.severity_score) for item in symptoms if item.severity_score is not None],
            default=None,
        )
        max_fatigue = max(
            [float(item.fatigue_score) for item in recovery if item.fatigue_score is not None],
            default=None,
        )
        return {
            "decision_type": decision.decision_type,
            "workout_count": len(workouts),
            "recovery_count": len(recovery),
            "symptom_count": len(symptoms),
            "nutrition_days": len(nutrition),
            "max_symptom_severity": max_symptom,
            "max_fatigue_score": max_fatigue,
            "total_records": len(workouts) + len(recovery) + len(symptoms) + len(nutrition),
            "safety_escalation": bool(
                (max_symptom is not None and max_symptom >= 7)
                or (max_fatigue is not None and max_fatigue >= 9)
            ),
        }

    def _implementation_status(
        self,
        plan: models.DecisionEvaluationPlan,
        evidence: dict[str, Any],
    ) -> str:
        answered = self._latest_implementation_answer(plan.id)
        if answered:
            return answered
        if plan.implementation_status != "unknown":
            return plan.implementation_status
        if plan.evaluation_type == "plan":
            return "implemented" if evidence["workout_count"] >= 1 else "unknown"
        if plan.evaluation_type == "nutrition":
            return "partially_implemented" if evidence["nutrition_days"] >= 2 else "unknown"
        return "unknown"

    def _has_minimum_evidence(
        self,
        plan: models.DecisionEvaluationPlan,
        evidence: dict[str, Any],
    ) -> bool:
        requirements = plan.minimum_evidence or {}
        return all(
            int(evidence.get(key, 0) or 0) >= int(value)
            for key, value in requirements.items()
        )

    def _ensure_followup(
        self,
        plan: models.DecisionEvaluationPlan,
        question_type: str,
        trigger_type: str,
        urgent: bool = False,
    ) -> models.DecisionFollowup:
        existing = self.db.scalar(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan.id,
                models.DecisionFollowup.question_type == question_type,
                models.DecisionFollowup.status == "pending",
            )
        )
        if existing is not None:
            return existing
        questions = plan.subjective_questions or []
        payload = next(
            (item for item in questions if item.get("question_type") == question_type),
            {
                "question_type": question_type,
                "prompt": (
                    "执行这项建议后，是否出现疼痛、头晕、胸闷或明显恢复恶化？"
                    if urgent
                    else "你是否按照这项建议执行？执行后状态是改善、没有变化还是变差？"
                ),
                "options": (
                    ["没有异常", "轻微异常", "明显加重"]
                    if urgent
                    else ["完全执行", "部分执行", "没有执行"]
                ),
            },
        )
        followup = models.DecisionFollowup(
            evaluation_plan_id=plan.id,
            user_id=plan.user_id,
            question_type=question_type,
            question_payload=payload,
            trigger_type=trigger_type,
            status="pending",
            scheduled_at=datetime.utcnow(),
        )
        self.db.add(followup)
        plan.followup_count = (plan.followup_count or 0) + 1
        self.db.flush()
        return followup

    def _latest_implementation_answer(self, plan_id: uuid.UUID) -> str | None:
        answer = self._latest_followup_answer(plan_id)
        value = str(answer.get("implementation_status") or "")
        return value if value in {
            "implemented", "partially_implemented", "not_started", "abandoned"
        } else None

    def _latest_followup_answer(self, plan_id: uuid.UUID) -> dict[str, Any]:
        followup = self.db.scalar(
            select(models.DecisionFollowup)
            .where(
                models.DecisionFollowup.evaluation_plan_id == plan_id,
                models.DecisionFollowup.status == "answered",
            )
            .order_by(desc(models.DecisionFollowup.answered_at))
        )
        if followup is None:
            return {}
        return followup.answer_json or {}

    def _answered_followup_evidence(self, plan_id: uuid.UUID) -> list[dict[str, Any]]:
        followups = list(self.db.scalars(
            select(models.DecisionFollowup).where(
                models.DecisionFollowup.evaluation_plan_id == plan_id,
                models.DecisionFollowup.status == "answered",
            )
        ))
        return [
            {
                "table": "decision_followups",
                "id": str(item.id),
                "summary": str(item.answer_json or {})[:180],
                "time": item.answered_at.isoformat() if item.answered_at else datetime.utcnow().isoformat(),
            }
            for item in followups
        ]

    def _plan_spec(self, decision: models.AgentDecision) -> dict[str, Any]:
        decision_type = (decision.decision_type or "").lower()
        if any(term in decision_type for term in ["nutrition", "diet", "meal"]):
            return {
                "evaluation_type": "nutrition",
                "window_days": 10,
                "first_check_days": 3,
                "expected_action": {"requires_user_confirmation": True},
                "objective_metrics": ["nutrition_days", "avg_adherence_score", "protein_target_ratio"],
                "minimum_evidence": {"nutrition_days": 3},
                "subjective_questions": [self._execution_question("这个饮食建议是否实际执行，执行起来是否方便？")],
            }
        if any(term in decision_type for term in ["risk", "pain", "injury", "adjustment", "progression"]):
            return {
                "evaluation_type": "training_adjustment",
                "window_days": 7,
                "first_check_days": 1,
                "expected_action": {"requires_user_confirmation": True},
                "objective_metrics": ["workout_count", "avg_completion_rate", "avg_fatigue_score", "max_symptom_severity"],
                "minimum_evidence": {"workout_count": 1, "recovery_count": 1},
                "subjective_questions": [
                    self._execution_question("你是否按建议调整了训练？调整后症状是改善、无变化还是加重？"),
                    {
                        "question_type": "safety_check",
                        "prompt": "执行建议后，是否出现疼痛、头晕、胸闷或明显恢复恶化？",
                        "options": ["没有异常", "轻微异常", "明显加重"],
                    },
                ],
            }
        return {
            "evaluation_type": "plan",
            "window_days": 14,
            "first_check_days": 3,
            "expected_action": {"requires_user_confirmation": False},
            "objective_metrics": ["workout_count", "avg_completion_rate", "avg_fatigue_score"],
            "minimum_evidence": {"workout_count": 2},
            "subjective_questions": [self._execution_question("这份计划是否容易执行并愿意继续？")],
        }

    def _execution_question(self, prompt: str) -> dict[str, Any]:
        return {
            "question_type": "strategy_execution",
            "prompt": prompt,
            "options": ["完全执行", "部分执行", "没有执行"],
            "expected_answer_fields": ["implementation_status", "subjective_outcome", "comment"],
        }

    def _plan_payload(
        self,
        plan: models.DecisionEvaluationPlan,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": str(plan.id),
            "decision_id": str(plan.decision_id),
            "status": plan.status,
            "evaluation_type": plan.evaluation_type,
            "implementation_status": plan.implementation_status,
            "outcome_status": plan.outcome_status,
            "window_start": plan.window_start.isoformat() if plan.window_start else None,
            "window_end": plan.window_end.isoformat() if plan.window_end else None,
            "next_check_at": plan.next_check_at.isoformat() if plan.next_check_at else None,
            "followup_count": plan.followup_count,
            "evidence": plan.evidence_snapshot or {},
            "reason": reason,
        }

    def _followup_payload(self, item: models.DecisionFollowup) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "evaluation_plan_id": str(item.evaluation_plan_id),
            "question_type": item.question_type,
            "question": item.question_payload or {},
            "trigger_type": item.trigger_type,
            "status": item.status,
            "answer": item.answer_json or {},
            "attempt_count": item.attempt_count,
            "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
            "sent_at": item.sent_at.isoformat() if item.sent_at else None,
            "answered_at": item.answered_at.isoformat() if item.answered_at else None,
        }
