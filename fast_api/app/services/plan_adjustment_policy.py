"""Conservative dependency gate for the approved, dated plan-adjustment slice."""

from __future__ import annotations

import copy
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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


class PlanAdjustmentPolicy:
    """No commits, model calls, or inferred delegation. Caller owns the transaction."""

    def __init__(self, db: Session):
        self.db = db

    def capture(self, user_id: uuid.UUID, since: str | None = None) -> dict[str, Any]:
        self.db.flush()
        user = self.db.scalar(
            select(models.User)
            .where(models.User.id == user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise ValueError("Adjustment owner not found")
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(user.timezone)).date()
        start = date.fromisoformat(since) if since else today - timedelta(days=7)
        profile = self.db.scalar(
            select(models.UserProfile)
            .where(models.UserProfile.user_id == user_id)
            .execution_options(populate_existing=True)
        )

        def records(model, filters, fields):
            rows = self.db.scalars(
                select(model)
                .where(model.user_id == user_id, *filters)
                .order_by(model.id)
                .execution_options(populate_existing=True)
            ).all()
            return [
                {
                    key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key in ("id", *fields)
                    for value in [getattr(row, key)]
                }
                for row in rows
            ]

        result = {
            "version": 1,
            "since": start.isoformat(),
            "timezone": user.timezone,
            "profile": {key: copy.deepcopy(getattr(profile, key)) for key in PROFILE_FIELDS}
            if profile
            else None,
            "recovery": records(
                models.RecoveryLog,
                [models.RecoveryLog.log_date >= start, models.RecoveryLog.log_date <= today],
                (
                    "log_date",
                    "sleep_hours",
                    "fatigue_score",
                    "soreness_score",
                    "stress_score",
                    "notes",
                ),
            ),
            "risks": records(
                models.RiskNote,
                [models.RiskNote.status == "active"],
                ("risk_type", "description", "severity_score", "status"),
            ),
            "symptoms": records(
                models.SymptomLog,
                [models.SymptomLog.status == "active"],
                ("symptom_date", "symptom_type", "severity_score", "status"),
            ),
        }
        # UUIDs are persisted as strings in the approval JSON contract.
        for group in ("recovery", "risks", "symptoms"):
            for row in result[group]:
                row["id"] = str(row["id"])
        return result

    def changed(self, user_id: uuid.UUID, baseline: dict[str, Any] | None) -> bool:
        return (
            not baseline
            or baseline.get("version") != 1
            or self.capture(user_id, baseline["since"]) != baseline
        )

    def invalidate_changed(self, user_id: uuid.UUID) -> list[str]:
        from fast_api.app.services.decision_dependencies import DecisionDependencyService

        DecisionDependencyService(self.db).invalidate_changed(user_id)
        from fast_api.app.services.execution_events import append_approval_event

        # Maintain the same owner -> approval order as proposal/execution.
        self.capture(user_id)
        rows = self.db.scalars(
            select(models.PendingApproval)
            .where(
                models.PendingApproval.user_id == user_id,
                models.PendingApproval.tool_name == "plan.reduce_sets",
                models.PendingApproval.status.in_(["pending", "approved"]),
            )
            .with_for_update()
        ).all()
        invalidated = []
        for row in rows:
            if self.changed(user_id, (row.context_json or {}).get("dependencies")):
                row.status = "stale"
                if row.job_id:
                    job = self.db.get(models.BackgroundTask, row.job_id)
                    if job and job.status in {"waiting_approval", "queued"}:
                        job.status = "cancelled"
                append_approval_event(
                    row,
                    "execution.dependencies",
                    "blocked",
                    "目标、约束或依据已变化，旧草案失效；计划未修改。",
                )
                invalidated.append(str(row.id))
        self.db.flush()
        return invalidated

    def propose_from_checkin(self, user_id: uuid.UUID, checkin: models.DailyCheckin) -> dict:
        from fast_api.app.services.responsibilities import TASK_TYPE, ResponsibilityService

        state = self.capture(user_id)
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(state["timezone"])).date()
        if checkin.checkin_date != today:
            return {"status": "not_proposed", "reason": "historical_or_future_checkin"}
        if state["risks"] or state["symptoms"] or (state["profile"] or {}).get("injuries"):
            return {"status": "not_proposed", "reason": "risk_requires_separate_review"}
        if checkin.notes:
            return {"status": "not_proposed", "reason": "unstructured_notes_require_review"}
        tasks = self.db.scalars(
            select(models.AgentTaskState).where(
                models.AgentTaskState.user_id == user_id,
                models.AgentTaskState.task_type == TASK_TYPE,
                models.AgentTaskState.status == "active",
            )
        ).all()
        if len(tasks) != 1:
            return {"status": "not_proposed", "reason": "missing_or_ambiguous_delegation"}
        spec = tasks[0].constraints["responsibility"]
        now = datetime.now(timezone.utc)
        if (
            not datetime.fromisoformat(spec["starts_at"])
            <= now
            < datetime.fromisoformat(spec["ends_at"])
        ):
            return {"status": "not_proposed", "reason": "delegation_outside_window"}
        # This is a synthetic product rule, not a validated health threshold.
        return ResponsibilityService(self.db)._draft_review_adjustment(
            tasks[0],
            {
                "adjustment_signal": {
                    "eligible": True,
                    "policy": "demo_checkin_v1_not_clinical",
                    "proposal_reason": "演示策略：本次打卡触发恢复复核，建议下一次训练各动作减1组；非临床结论，须人工确认。",
                    "evidence": [{"table": "daily_checkins", "id": str(checkin.id)}],
                }
            },
            now,
        )

    def require_explicit_replacement(self, user_id: uuid.UUID) -> None:
        """Legacy whole-plan replacement must not bypass an active ask/deny contract."""
        from fast_api.app.services.responsibilities import TASK_TYPE

        self.capture(user_id)
        tasks = self.db.scalars(
            select(models.AgentTaskState).where(
                models.AgentTaskState.user_id == user_id,
                models.AgentTaskState.task_type == TASK_TYPE,
                models.AgentTaskState.status == "active",
            )
        ).all()
        now = datetime.now(timezone.utc)
        for task in tasks:
            spec = task.constraints["responsibility"]
            if datetime.fromisoformat(spec["starts_at"]) <= now < datetime.fromisoformat(
                spec["ends_at"]
            ) and spec["authority"].get("adjust_plan") in {"ask", "deny"}:
                raise ValueError(
                    "Whole-plan replacement requires a separate reviewed proposal; "
                    "use the dated adjustment approval flow"
                )
