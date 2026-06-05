from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models


class AgentTaskStateService:
    """Durable, cross-turn task state for the coach agent.

    AgentTaskTimeline explains one run. AgentTaskState explains what the agent is
    still trying to improve across runs, such as a 12-week fat-loss cycle or a
    bench-press fatigue experiment.
    """

    def __init__(self, db: Session):
        self.db = db

    def update_from_chat_turn(
        self,
        user_id: uuid.UUID,
        message: str,
        profile: models.UserProfile,
        context_packet: dict[str, Any] | None = None,
        state_updates: dict[str, Any] | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        context_packet = context_packet or {}
        state_updates = state_updates or {}
        intent = str(context_packet.get("intent") or state_updates.get("context_intent") or "")
        updates: list[dict[str, Any]] = []

        if profile.goal:
            task = self._upsert_task(
                user_id=user_id,
                task_type="fitness_cycle",
                title=self._goal_title(profile.goal),
                objective=self._goal_objective(profile),
                phase="execute",
                current_step="按当前档案执行训练/饮食计划，并根据 check-in 与训练日志调整。",
                success_metrics={
                    "primary_goal": profile.goal,
                    "weekly_training_frequency": profile.workout_frequency,
                    "target_calories": profile.target_calories,
                    "target_protein_g": profile.target_protein_g,
                },
                constraints={
                    "injuries": profile.injuries or [],
                    "dietary_preferences": profile.dietary_preferences or [],
                    "equipment_available": profile.equipment_available or [],
                },
                next_actions=self._next_actions_for_goal(profile),
                progress_patch={
                    "last_intent": intent,
                    "missing_slots": state_updates.get("missing_slots") or [],
                    "generated_plan_id": state_updates.get("generated_plan_id"),
                },
                agent_run_id=agent_run_id,
            )
            updates.append(self._task_payload(task))

        if self._looks_like_training_experiment(message, intent):
            task = self._upsert_task(
                user_id=user_id,
                task_type="training_experiment",
                title="训练质量调整实验",
                objective="解决主项后续动作质量下降、疲劳过高或训练续航不足的问题。",
                phase="observe",
                current_step="下次训练按建议调整主项容量或强度，并记录后续动作完成质量、RPE 和疲劳。",
                success_metrics={
                    "observe_next_sessions": 2,
                    "metrics": ["后续动作完成质量", "主项RPE", "训练后疲劳", "第二天酸痛"],
                },
                constraints={"current_request": message[:500]},
                next_actions=[
                    {
                        "action": "record_next_workout",
                        "description": "下次训练后记录主项重量/次数、后续动作完成质量和RPE。",
                    },
                    {
                        "action": "compare_fatigue",
                        "description": "如果连续两次后续动作质量仍差，继续降低主项总组数或调整动作顺序。",
                    },
                ],
                progress_patch={"last_training_issue_message": message[:800], "last_intent": intent},
                agent_run_id=agent_run_id,
            )
            updates.append(self._task_payload(task))

        return updates

    def update_from_checkin(
        self,
        user_id: uuid.UUID,
        checkin: models.DailyCheckin,
        auto_adjusted: bool,
    ) -> dict[str, Any]:
        task = self._upsert_task(
            user_id=user_id,
            task_type="recovery_monitor",
            title="恢复状态监控",
            objective="根据疲劳、酸痛、睡眠和训练完成度调整训练压力。",
            phase="observe",
            current_step="继续记录每日 check-in；疲劳或酸痛高时优先降低训练量。",
            success_metrics={
                "fatigue_target": "<=6",
                "soreness_target": "<=6",
                "sleep_target_hours": ">=7",
            },
            constraints={},
            next_actions=[
                {
                    "action": "daily_checkin",
                    "description": "明天继续记录睡眠、疲劳、酸痛和训练完成度。",
                }
            ],
            progress_patch={
                "latest_checkin_id": str(checkin.id),
                "fatigue": checkin.fatigue,
                "soreness": checkin.soreness,
                "sleep_hours": checkin.sleep_hours,
                "auto_adjusted": auto_adjusted,
            },
            agent_run_id=None,
        )
        return self._task_payload(task)

    def list_active(self, user_id: uuid.UUID, limit: int = 10) -> list[dict[str, Any]]:
        tasks = self.db.scalars(
            select(models.AgentTaskState)
            .where(models.AgentTaskState.user_id == user_id, models.AgentTaskState.status == "active")
            .order_by(desc(models.AgentTaskState.updated_at))
            .limit(max(1, min(limit, 50)))
        ).all()
        return [self._task_payload(task) for task in tasks]

    def record_replay_snapshot(
        self,
        agent_run: models.AgentRun,
        request_json: dict[str, Any],
        state_snapshot: dict[str, Any],
        tool_plan_json: dict[str, Any],
        response_snapshot: dict[str, Any],
        config_snapshot: dict[str, Any],
    ) -> models.AgentRunReplay:
        replay = self.db.scalar(
            select(models.AgentRunReplay).where(models.AgentRunReplay.agent_run_id == agent_run.id)
        )
        if replay is None:
            replay = models.AgentRunReplay(
                agent_run_id=agent_run.id,
                user_id=agent_run.user_id,
                session_id=agent_run.session_id,
            )
            self.db.add(replay)
        replay.request_json = request_json
        replay.state_snapshot = state_snapshot
        replay.tool_plan_json = tool_plan_json
        replay.response_snapshot = response_snapshot
        replay.config_snapshot = config_snapshot
        replay.replay_status = "recorded"
        return replay

    def replay_packet(self, run_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
        replay = self.db.scalar(
            select(models.AgentRunReplay).where(
                models.AgentRunReplay.agent_run_id == run_id,
                models.AgentRunReplay.user_id == user_id,
            )
        )
        if replay is None:
            raise ValueError("Replay packet not found")
        return {
            "id": str(replay.id),
            "agent_run_id": str(replay.agent_run_id),
            "user_id": str(replay.user_id),
            "session_id": str(replay.session_id) if replay.session_id else None,
            "request": replay.request_json,
            "state_snapshot": replay.state_snapshot,
            "tool_plan": replay.tool_plan_json,
            "response_snapshot": replay.response_snapshot,
            "config_snapshot": replay.config_snapshot,
            "replay_status": replay.replay_status,
            "created_at": replay.created_at,
        }

    def _upsert_task(
        self,
        user_id: uuid.UUID,
        task_type: str,
        title: str,
        objective: str,
        phase: str,
        current_step: str,
        success_metrics: dict[str, Any],
        constraints: dict[str, Any],
        next_actions: list[dict[str, Any]],
        progress_patch: dict[str, Any],
        agent_run_id: uuid.UUID | None,
    ) -> models.AgentTaskState:
        task = self.db.scalar(
            select(models.AgentTaskState).where(
                models.AgentTaskState.user_id == user_id,
                models.AgentTaskState.task_type == task_type,
                models.AgentTaskState.status == "active",
            )
        )
        if task is None:
            task = models.AgentTaskState(
                user_id=user_id,
                task_type=task_type,
                title=title,
                objective=objective,
            )
            self.db.add(task)
            event_type = "created"
        else:
            event_type = "updated"

        task.title = title
        task.objective = objective
        task.phase = phase
        task.current_step = current_step
        task.success_metrics = success_metrics
        task.constraints = constraints
        task.next_actions = next_actions
        task.progress_json = {**(task.progress_json or {}), **{k: v for k, v in progress_patch.items() if v is not None}}
        task.source_run_id = agent_run_id or task.source_run_id
        task.last_observed_at = datetime.utcnow()
        self.db.flush()
        self.db.add(
            models.AgentTaskEvent(
                task_id=task.id,
                user_id=user_id,
                agent_run_id=agent_run_id,
                event_type=event_type,
                summary=f"{title}: {current_step}",
                payload_json={
                    "phase": phase,
                    "progress_patch": progress_patch,
                    "next_actions": next_actions,
                },
            )
        )
        return task

    def _goal_title(self, goal: str) -> str:
        if goal == "fat_loss":
            return "减脂周期目标"
        if goal == "muscle_gain":
            return "增肌周期目标"
        return "长期健身目标"

    def _goal_objective(self, profile: models.UserProfile) -> str:
        goal = profile.goal or "general_fitness"
        frequency = profile.workout_frequency or 3
        return f"围绕 {goal} 持续推进训练和饮食闭环，每周训练约 {frequency} 次。"

    def _next_actions_for_goal(self, profile: models.UserProfile) -> list[dict[str, Any]]:
        actions = [
            {"action": "log_workout", "description": "每次训练后记录动作、重量、次数、RPE 和完成度。"},
            {"action": "daily_checkin", "description": "每天记录睡眠、疲劳、酸痛和饮食执行。"},
        ]
        if profile.goal in {"fat_loss", "muscle_gain"}:
            actions.append({"action": "weekly_review", "description": "每周复盘体重趋势、训练表现和饮食执行。"})
        return actions

    def _looks_like_training_experiment(self, message: str, intent: str) -> bool:
        lowered = message.lower()
        issue_terms = ["没力", "没有力量", "质量不好", "疲劳", "卡住", "瓶颈", "rpe", "力竭", "没劲"]
        training_terms = ["卧推", "深蹲", "硬拉", "训练", "练胸", "bench", "squat", "deadlift"]
        return intent == "training_log" and any(term in lowered for term in issue_terms) and any(
            term in lowered for term in training_terms
        )

    def _task_payload(self, task: models.AgentTaskState) -> dict[str, Any]:
        return {
            "id": str(task.id),
            "task_type": task.task_type,
            "title": task.title,
            "objective": task.objective,
            "status": task.status,
            "phase": task.phase,
            "current_step": task.current_step,
            "success_metrics": task.success_metrics or {},
            "constraints": task.constraints or {},
            "next_actions": task.next_actions or [],
            "progress": task.progress_json or {},
            "source_run_id": str(task.source_run_id) if task.source_run_id else None,
            "last_observed_at": task.last_observed_at.isoformat() if task.last_observed_at else None,
        }
