"""Finite weekly responsibilities on existing task-state and job tables."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.plan_writes import lock_plan_owner

TASK_TYPE = "weekly_training_review"
JOB_TYPE = "responsibility.weekly_review"
UTC = timezone.utc


def utc(value: datetime) -> datetime:
    # Existing SQLite timestamps and legacy database values can be naive UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def next_weekly(after: datetime, zone: str, weekday: int, hour: int, minute: int) -> datetime:
    local = utc(after).astimezone(ZoneInfo(zone))
    candidate_date = local.date() + timedelta(days=(weekday - local.weekday()) % 7)
    for _ in range(3):
        candidate = datetime.combine(candidate_date, time(hour, minute), ZoneInfo(zone))
        normalized = candidate.astimezone(UTC).astimezone(ZoneInfo(zone))
        # Skip nonexistent local times. For an ambiguous hour, fold=0 is intentional.
        if normalized.replace(tzinfo=None) == candidate.replace(tzinfo=None):
            if candidate.astimezone(UTC) > utc(after):
                return candidate.astimezone(UTC)
        candidate_date += timedelta(days=7)
    raise ValueError("Unable to resolve weekly local schedule")


class ResponsibilityService:
    """Caller owns commit; scheduling and job creation must commit atomically."""

    def __init__(self, db: Session):
        self.db = db

    def create_weekly(
        self,
        user_id: uuid.UUID,
        instruction: str,
        *,
        weeks: int = 4,
        weekday: int = 6,
        hour: int = 18,
        minute: int = 0,
        now: datetime | None = None,
    ) -> models.AgentTaskState:
        if not instruction.strip() or len(instruction) > 2000:
            raise ValueError("A bounded source instruction is required")
        if not 1 <= weeks <= 52 or not 0 <= weekday <= 6:
            raise ValueError("Invalid duration or weekday")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Invalid local review time")
        user = lock_plan_owner(self.db, user_id)
        zone = user.timezone or "Asia/Shanghai"
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("User timezone is invalid") from exc
        start = utc(now or datetime.now(UTC))
        end = (start.astimezone(ZoneInfo(zone)) + timedelta(weeks=weeks)).astimezone(UTC)
        wake = next_weekly(start, zone, weekday, hour, minute)
        task = models.AgentTaskState(
            user_id=user_id,
            task_type=TASK_TYPE,
            title="有限期每周训练复盘",
            objective="按约定周期复盘训练证据；不自动修改训练计划。",
            status="active",
            phase="observe",
            current_step="等待当地复盘时间或新的训练证据",
            constraints={
                "responsibility": {
                    "schema_version": 1,
                    "revision": 1,
                    "source_instruction": instruction.strip(),
                    "timezone": zone,
                    "starts_at": start.isoformat(),
                    "ends_at": end.isoformat(),
                    "weekday": weekday,
                    "hour": hour,
                    "minute": minute,
                    "next_wake_at": wake.isoformat() if wake < end else None,
                    "authority": {
                        "read_training_data": "allow",
                        "create_summary": "allow",
                        "adjust_plan": "ask",
                        "delete_record": "deny",
                    },
                    "notification": {"delivery": "activity_only"},
                }
            },
            success_metrics={"review_delivery": "persisted_review_result"},
            next_actions=[{"action": "weekly_review"}],
            progress_json={"reviews_completed": 0},
            last_observed_at=start,
        )
        self.db.add(task)
        self.db.flush()
        self._event(task, "responsibility.created", {"source_instruction": instruction})
        return task

    def get(self, task_id: uuid.UUID, user_id: uuid.UUID) -> models.AgentTaskState:
        lock_plan_owner(self.db, user_id)
        statement = select(models.AgentTaskState).where(
            models.AgentTaskState.id == task_id,
            models.AgentTaskState.user_id == user_id,
            models.AgentTaskState.task_type == TASK_TYPE,
        )
        task = self.db.scalar(statement.with_for_update())
        if task is None:
            raise ValueError("Responsibility not found")
        return task

    def transition(
        self, task_id: uuid.UUID, user_id: uuid.UUID, action: str, now: datetime | None = None
    ) -> models.AgentTaskState:
        task = self.get(task_id, user_id)
        current = utc(now or datetime.now(UTC))
        spec = dict(task.constraints["responsibility"])
        if task.status not in {"active", "paused"}:
            raise ValueError("Responsibility is terminal")
        if current >= datetime.fromisoformat(spec["ends_at"]):
            task.status = "expired"
        elif action == "pause" and task.status == "active":
            task.status = "paused"
        elif action == "resume" and task.status == "paused":
            task.status = "active"
            wake = next_weekly(
                current, spec["timezone"], spec["weekday"], spec["hour"], spec["minute"]
            )
            spec["next_wake_at"] = (
                wake.isoformat() if wake < datetime.fromisoformat(spec["ends_at"]) else None
            )
        elif action == "cancel":
            task.status = "cancelled"
        else:
            raise ValueError("Invalid responsibility transition")
        spec["revision"] += 1
        if task.status != "active":
            spec["next_wake_at"] = None
        task.constraints = {**task.constraints, "responsibility": spec}
        self._event(task, "responsibility." + task.status, {"action": action})
        self.db.flush()
        return task

    def scan_due(self, now: datetime | None = None) -> dict[str, int]:
        current = utc(now or datetime.now(UTC))
        tasks = self.db.scalars(
            select(models.AgentTaskState)
            .where(
                models.AgentTaskState.task_type == TASK_TYPE,
                models.AgentTaskState.status.in_(["active", "paused"]),
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).all()
        queued = expired = 0
        for task in tasks:
            spec = dict(task.constraints["responsibility"])
            if current >= datetime.fromisoformat(spec["ends_at"]):
                task.status = "expired"
                spec["next_wake_at"] = None
                expired += 1
                self._event(task, "responsibility.expired", {})
            elif task.status == "active" and spec["next_wake_at"]:
                due = datetime.fromisoformat(spec["next_wake_at"])
                if due <= current:
                    following = next_weekly(
                        due, spec["timezone"], spec["weekday"], spec["hour"], spec["minute"]
                    )
                    while following <= current:
                        due = following
                        following = next_weekly(
                            due, spec["timezone"], spec["weekday"], spec["hour"], spec["minute"]
                        )
                    self.db.add(
                        models.BackgroundTask(
                            user_id=task.user_id,
                            task_type=JOB_TYPE,
                            status="queued",
                            max_attempts=1,
                            payload_json={
                                "responsibility_id": str(task.id),
                                "revision": spec["revision"],
                                "scheduled_at": due.isoformat(),
                            },
                        )
                    )
                    spec["last_wake_at"] = due.isoformat()
                    spec["next_wake_at"] = (
                        following.isoformat()
                        if following < datetime.fromisoformat(spec["ends_at"])
                        else None
                    )
                    queued += 1
                    self._event(task, "responsibility.queued", {"scheduled_at": due.isoformat()})
            task.constraints = {**task.constraints, "responsibility": spec}
        self.db.flush()
        return {"queued": queued, "expired": expired}

    def execute_review(
        self, job: models.BackgroundTask, now: datetime | None = None
    ) -> dict[str, Any]:
        from fast_api.app.services.reflection_service import ReflectionService

        current = utc(now or datetime.now(UTC))
        payload = job.payload_json
        from fast_api.app.services.execution_events import execution_event

        events = []
        task = self.get(uuid.UUID(payload["responsibility_id"]), job.user_id)
        spec = task.constraints["responsibility"]
        if (
            task.status != "active"
            or payload["revision"] != spec["revision"]
            or current >= datetime.fromisoformat(spec["ends_at"])
        ):
            return {"status": "skipped", "reason": "inactive_expired_or_stale_revision"}
        if any(
            spec["authority"].get(action) != "allow"
            for action in ("read_training_data", "create_summary")
        ):
            return {"status": "skipped", "reason": "authority_not_granted"}
        scheduled = datetime.fromisoformat(payload["scheduled_at"])
        if scheduled > current:
            raise ValueError("Cannot execute a future review")
        cutoff = scheduled - timedelta(microseconds=1)
        local_end = cutoff.astimezone(ZoneInfo(spec["timezone"])).date()
        window_start = (
            scheduled.astimezone(ZoneInfo(spec["timezone"])) - timedelta(days=7)
        ).astimezone(UTC)
        local_start = max(
            local_end - timedelta(days=6),
            datetime.fromisoformat(spec["starts_at"]).astimezone(ZoneInfo(spec["timezone"])).date(),
        )
        result = ReflectionService(self.db).reflect_weekly(
            job.user_id,
            local_start,
            local_end,
            timezone_name=spec["timezone"],
            as_of=cutoff,
            not_before=datetime.fromisoformat(spec["starts_at"]),
            window_start=window_start,
        )
        signal = result.get("adjustment_signal", {})
        events.append(
            execution_event(
                "review.evidence",
                "completed",
                "已读取约定窗口的训练、恢复及症状记录，并保留来源。",
                details={
                    "scheduled_at": scheduled.isoformat(),
                    "window_start": window_start.isoformat(),
                    "cutoff": cutoff.isoformat(),
                    "recovery_evidence": signal.get("evidence", []),
                },
            )
        )
        events.append(
            execution_event(
                "review.decision",
                "completed",
                "已按演示规则判断是否提出减量草案；不是模型思考或临床结论。",
                source="rule",
                details={
                    "policy": signal.get("policy", "demo_fatigue_v1_not_clinical"),
                    "eligible": signal.get("eligible", False),
                    "reason": signal.get("reason"),
                    "average_fatigue": signal.get("average_fatigue"),
                },
            )
        )
        result["adjustment_proposal"] = (
            {"status": "not_proposed", "reason": "review_evidence_stale"}
            if current - scheduled > timedelta(hours=24)
            else self._draft_review_adjustment(task, result, current)
        )
        proposal = result["adjustment_proposal"]
        events.append(
            execution_event(
                "review.proposal",
                "blocked" if proposal["status"] == "waiting_approval" else "skipped",
                "已生成指定日期的草案，等待用户审批。"
                if proposal["status"] == "waiting_approval"
                else "未生成调整草案，计划保持不变。",
                details=proposal,
            )
        )
        result["execution_events"] = events
        if proposal.get("approval_id"):
            row = self.db.get(models.PendingApproval, uuid.UUID(proposal["approval_id"]))
            row.context_json = {
                **row.context_json,
                "execution_events": sorted(
                    [*events, *row.context_json.get("execution_events", [])],
                    key=lambda entry: entry["recorded_at"],
                ),
            }
        task.progress_json = {
            **task.progress_json,
            "reviews_completed": task.progress_json.get("reviews_completed", 0) + 1,
            "last_review": {
                "job_id": str(job.id),
                "scheduled_at": scheduled.isoformat(),
                "week_start": local_start.isoformat(),
                "week_end": local_end.isoformat(),
                **result,
            },
        }
        self._event(task, "responsibility.reviewed", {"job_id": str(job.id), "result": result})
        self.db.flush()
        return {"status": "reviewed", "responsibility_id": str(task.id), **result}

    def _draft_review_adjustment(self, task, result, current):
        from datetime import date

        from fast_api.app.services.approval_manager import ApprovalManager
        from fast_api.app.services.approved_plan_adjustments import ApprovedPlanAdjustmentService

        signal = result.get("adjustment_signal", {})
        if not signal.get("eligible"):
            return {"status": "not_proposed", "reason": signal.get("reason", "no_signal")}
        spec = task.constraints["responsibility"]
        if spec["authority"].get("adjust_plan") != "ask":
            return {"status": "not_proposed", "reason": "authority_not_granted"}
        existing = self.db.scalars(
            select(models.PendingApproval).where(
                models.PendingApproval.user_id == task.user_id,
                models.PendingApproval.tool_name == "plan.reduce_sets",
                models.PendingApproval.status.in_(["pending", "approved", "executing"]),
            )
        ).all()
        manager = ApprovalManager(self.db)
        unresolved = [manager.get(str(row.id)) for row in existing]
        if any(item and item.status in {"pending", "approved", "executing"} for item in unresolved):
            return {"status": "not_proposed", "reason": "unresolved_adjustment_exists"}
        plans = self.db.scalars(
            select(models.TrainingPlan).where(
                models.TrainingPlan.user_id == task.user_id, models.TrainingPlan.status == "active"
            )
        ).all()
        if len(plans) != 1:
            return {"status": "not_proposed", "reason": "missing_or_ambiguous_plan"}
        plan = plans[0]
        today = current.astimezone(ZoneInfo(spec["timezone"])).date()
        end_date = (
            datetime.fromisoformat(spec["ends_at"]).astimezone(ZoneInfo(spec["timezone"])).date()
        )
        candidates = []
        for day in plan.plan_json.get("training_days", []):
            try:
                day_date = date.fromisoformat(day.get("date", ""))
            except (ValueError, TypeError):
                continue
            if today < day_date < min(today + timedelta(days=8), end_date):
                candidates.append(day_date)
        if not candidates:
            return {"status": "not_proposed", "reason": "no_future_dated_training"}
        try:
            # A proposal failure must not leave an orphan job or undo the review itself.
            with self.db.begin_nested():
                proposal = ApprovedPlanAdjustmentService(self.db).propose(
                    task.user_id,
                    task.id,
                    plan.id,
                    min(candidates).isoformat(),
                    1,
                    signal.get("proposal_reason")
                    or "演示策略：至少3天恢复评分均值达到7，建议下一次训练各动作减1组；非临床结论，须人工确认。",
                )
                row = manager._row(proposal["approval"]["approval_id"])
                row.context_json = {**row.context_json, "review_signal": signal}
            return {
                "status": "waiting_approval",
                "approval_id": proposal["approval"]["approval_id"],
                "job_id": proposal["job_id"],
                "day_date": min(candidates).isoformat(),
                "reduce_by": 1,
            }
        except ValueError as exc:
            return {
                "status": "not_proposed",
                "reason": "proposal_validation_failed",
                "detail": str(exc),
            }

    def list_for_user(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        tasks = self.db.scalars(
            select(models.AgentTaskState)
            .where(
                models.AgentTaskState.user_id == user_id,
                models.AgentTaskState.task_type == TASK_TYPE,
            )
            .order_by(models.AgentTaskState.created_at.desc())
            .limit(100)
        ).all()
        return [self.snapshot(task) for task in tasks]

    @staticmethod
    def snapshot(task: models.AgentTaskState) -> dict[str, Any]:
        return {
            "id": str(task.id),
            "status": task.status,
            "objective": task.objective,
            "configuration": task.constraints["responsibility"],
            "progress": task.progress_json or {},
        }

    def _event(self, task: models.AgentTaskState, kind: str, payload: dict[str, Any]) -> None:
        self.db.add(
            models.AgentTaskEvent(
                task_id=task.id,
                user_id=task.user_id,
                event_type=kind,
                summary=kind,
                payload_json=payload,
            )
        )
