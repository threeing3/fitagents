"""One concrete approval/resume slice: reduce sets on one dated training day."""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.approval_manager import ApprovalManager
from fast_api.app.services.plan_adjustment_policy import PlanAdjustmentPolicy
from fast_api.app.services.plan_writes import replace_plan_content
from fast_api.app.services.responsibilities import ResponsibilityService

TOOL = "plan.reduce_sets"
JOB = "responsibility.plan_adjustment"


class ApprovedPlanAdjustmentService:
    def __init__(self, db: Session):
        self.db = db

    def propose(
        self,
        user_id: uuid.UUID,
        responsibility_id: uuid.UUID,
        plan_id: uuid.UUID,
        day_date: str,
        reduce_by: int,
        reason: str,
    ) -> dict[str, Any]:
        policy = PlanAdjustmentPolicy(self.db)
        dependencies = policy.capture(user_id)
        task = ResponsibilityService(self.db).get(responsibility_id, user_id)
        spec = task.constraints["responsibility"]
        if (
            task.status != "active"
            or datetime.now(timezone.utc) >= datetime.fromisoformat(spec["ends_at"])
            or spec["authority"].get("adjust_plan") != "ask"
        ):
            raise ValueError("Responsibility is inactive or does not permit a reviewed adjustment")
        plan = self.db.scalar(
            select(models.TrainingPlan)
            .where(
                models.TrainingPlan.id == plan_id,
                models.TrainingPlan.user_id == user_id,
                models.TrainingPlan.status == "active",
            )
            .with_for_update()
        )
        if plan is None:
            raise ValueError("Active plan not found")
        if not 1 <= reduce_by <= 3 or not reason.strip():
            raise ValueError("A bounded reduction and reason are required")
        requested_date = date.fromisoformat(day_date)
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(dependencies["timezone"])).date()
        end_date = (
            datetime.fromisoformat(spec["ends_at"])
            .astimezone(ZoneInfo(dependencies["timezone"]))
            .date()
        )
        if not today < requested_date < end_date:
            raise ValueError("Adjustment requires a future date within the delegation")
        if (
            dependencies["risks"]
            or dependencies["symptoms"]
            or (dependencies["profile"] or {}).get("injuries")
        ):
            raise ValueError("Current risk evidence requires separate review")
        baseline = copy.deepcopy(plan.plan_json)
        candidate = copy.deepcopy(baseline)
        days = [day for day in candidate.get("training_days", []) if day.get("date") == day_date]
        if len(days) != 1 or not days[0].get("exercises"):
            raise ValueError("Exactly one dated training day with exercises is required")
        for exercise in days[0]["exercises"]:
            sets = exercise.get("sets")
            if isinstance(sets, bool) or not isinstance(sets, int) or sets <= reduce_by:
                raise ValueError("Reduction must leave at least one set on every exercise")
            exercise["sets"] = sets - reduce_by
        job = models.BackgroundTask(
            user_id=user_id,
            task_type=JOB,
            status="waiting_approval",
            max_attempts=1,
            payload_json={},
        )
        self.db.add(job)
        self.db.flush()
        payload = {
            "plan_id": str(plan.id),
            "day_date": day_date,
            "reduce_by": reduce_by,
            "reason": reason.strip(),
            "request_key": str(job.id),
        }
        approval = ApprovalManager(self.db).create_approval(
            user_id,
            None,
            TOOL,
            "仅减少指定日期的训练组数，其他内容保持不变",
            "write",
            payload,
            input_json=payload,
            job_id=job.id,
            ttl_seconds=86400,
            context={
                "responsibility_id": str(task.id),
                "revision": spec["revision"],
                "baseline_plan": baseline,
                "candidate_plan": candidate,
                "dependencies": dependencies,
            },
        )
        job.payload_json = {"approval_id": approval.approval_id}
        self.db.flush()
        return {
            "status": "waiting_approval",
            "job_id": str(job.id),
            "approval": approval.to_dict(),
            "candidate_plan": candidate,
        }

    def execute(self, job: models.BackgroundTask) -> dict[str, Any]:
        from fast_api.app.services.execution_events import append_approval_event

        manager = ApprovalManager(self.db)
        policy = PlanAdjustmentPolicy(self.db)
        # Owner gate precedes approval and plan locks on this slice.
        policy.capture(job.user_id)
        approval_id = job.payload_json["approval_id"]
        approval = manager.get(approval_id)
        if approval is None or approval.user_id != job.user_id:
            raise ValueError("Approval owner does not match execution job")
        if approval.status != "approved":
            return {"status": "skipped", "reason": "approval_not_active"}
        task = ResponsibilityService(self.db).get(
            uuid.UUID(approval.context["responsibility_id"]), job.user_id
        )
        spec = task.constraints["responsibility"]
        plan = self.db.scalar(
            select(models.TrainingPlan)
            .where(
                models.TrainingPlan.id == uuid.UUID(approval.input_summary["plan_id"]),
                models.TrainingPlan.user_id == job.user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stale = (
            task.status != "active"
            or spec["revision"] != approval.context["revision"]
            or datetime.now(timezone.utc) >= datetime.fromisoformat(spec["ends_at"])
            or spec["authority"].get("adjust_plan") != "ask"
            or plan is None
            or plan.status != "active"
            or plan.plan_json != approval.context["baseline_plan"]
        )
        dependency_changed = policy.changed(job.user_id, approval.context.get("dependencies"))
        local_today = (
            datetime.now(timezone.utc)
            .astimezone(ZoneInfo(approval.context.get("dependencies", {}).get("timezone", "UTC")))
            .date()
        )
        date_elapsed = date.fromisoformat(approval.input_summary["day_date"]) <= local_today
        stale = stale or dependency_changed or date_elapsed
        if stale:
            row = manager._row(approval_id)
            row.status = "stale"
            append_approval_event(
                row,
                "execution.recheck",
                "blocked",
                "责任、计划、依据或执行日期已变化，旧草案失效，不执行写入。",
            )
            return {
                "status": "skipped",
                "reason": "dependencies_changed"
                if dependency_changed
                else "execution_date_elapsed"
                if date_elapsed
                else "responsibility_or_plan_changed",
            }

        row = manager._row(approval_id)
        append_approval_event(
            row,
            "execution.recheck",
            "completed",
            "已核对责任、未来日期、完整计划及最新档案、恢复与风险依据。",
            details={
                "day_date": approval.input_summary["day_date"],
                "reduce_by": approval.input_summary["reduce_by"],
                "tool": TOOL,
            },
        )

        def apply(payload: dict[str, Any]) -> dict[str, Any]:
            # No independent commit: plan, approval consumption, run and job commit together.
            replace_plan_content(
                self.db,
                job.user_id,
                plan.id,
                approval.context["baseline_plan"],
                approval.context["candidate_plan"],
            )
            self.db.flush()
            self.db.refresh(plan)
            if plan.plan_json != approval.context["candidate_plan"]:
                raise ValueError("Persisted plan does not match approved candidate")
            append_approval_event(
                row,
                "execution.verify",
                "completed",
                "写后重新读取计划，与批准草案一致；其他日期与内容未改变。",
                source="tool",
                details={"changed_date": payload["day_date"], "verified": True},
            )
            return {"plan_id": str(plan.id), "changed_date": payload["day_date"], "verified": True}

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name=TOOL,
                description="Apply one approved dated set reduction",
                permission_level="write",
                side_effects=True,
                idempotency_key_fields=["request_key"],
                input_schema={
                    "type": "object",
                    "required": ["request_key", "plan_id", "day_date", "reduce_by", "reason"],
                    "properties": {
                        "request_key": {"type": "string"},
                        "plan_id": {"type": "string"},
                        "day_date": {"type": "string"},
                        "reduce_by": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                },
                output_schema={
                    "type": "object",
                    "required": ["verified"],
                    "properties": {"verified": {"type": "boolean"}},
                },
            ),
            apply,
        )
        append_approval_event(
            row,
            "execution.tool",
            "running",
            "开始执行单次获批的指定日期减组工具。",
            source="tool",
            details={"tool": TOOL},
        )
        self.db.flush()
        result, approved = asyncio.run(
            registry.execute_awaiting_approval(
                TOOL,
                approval.input_summary,
                manager,
                user_id=job.user_id,
                approval_id=approval_id,
                job_id=job.id,
            )
        )
        if result.status == "blocked":
            append_approval_event(
                row,
                "execution.write",
                "blocked",
                "保存时发现计划已变化，旧草案未写入；需重新生成并审批。",
                source="tool",
            )
            return {"status": "skipped", "reason": "plan_changed_before_write"}
        if not approved:
            raise ValueError("Approved adjustment failed; no automatic replay permitted")
        from fast_api.app.services.decision_logger import DecisionLogger

        baseline_recovery = max(
            approval.context["dependencies"]["recovery"],
            key=lambda item: item["log_date"],
            default=None,
        )
        decision = DecisionLogger(self.db).log_decision(
            job.user_id,
            {
                "decision_type": "approved_plan_adjustment",
                "input_summary": approval.input_summary["reason"],
                "context_used": {
                    "approval_id": approval_id,
                    "plan_id": str(plan.id),
                    "day_date": approval.input_summary["day_date"],
                    "dependencies": approval.context["dependencies"],
                    "implementation_status": "plan_saved_not_training_confirmed",
                    "baseline_recovery_log_id": baseline_recovery["id"]
                    if baseline_recovery
                    else None,
                },
                "decision_result": "approved_dated_reduction_persisted",
                "reason": approval.input_summary["reason"],
                "confidence_score": 1.0,
                "accepted_by_user": True,
            },
        )
        append_approval_event(
            row,
            "execution.complete",
            "completed",
            "工具与写后校验完成；将随计划、审批和任务在同一事务提交。",
            source="tool",
            details={"tool": TOOL, "latency_ms": result.latency_ms},
        )
        run = models.AgentRun(
            user_id=job.user_id,
            run_type=JOB,
            status="completed",
            nodes=[*row.context_json.get("execution_events", []), result.to_trace()],
            completed_at=datetime.now(timezone.utc),
            summary="Approved dated reduction persisted and verified",
        )
        self.db.add(run)
        self.db.flush()
        self.db.add(
            models.AgentTaskEvent(
                task_id=task.id,
                user_id=job.user_id,
                agent_run_id=run.id,
                event_type="responsibility.plan_adjusted",
                summary="Approved scoped adjustment executed",
                payload_json={
                    "job_id": str(job.id),
                    "approval_id": approval_id,
                    "result": result.output_json,
                },
            )
        )
        return {
            "status": "adjusted",
            "agent_run_id": str(run.id),
            "decision_id": str(decision.id),
            **result.output_json,
        }
