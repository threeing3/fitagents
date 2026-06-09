import json
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fast_api.app.core.config import get_settings


class AgentRunLogger:
    """Collect runtime trace events and write readable per-run logs."""

    SECRET_KEYS = {"api_key", "authorization", "token", "password", "secret"}

    def __init__(
        self,
        run_type: str,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        request_id: str | None = None,
        log_dir: str | None = None,
    ):
        settings = get_settings()
        self.run_type = run_type
        self.user_id = str(user_id)
        self.session_id = str(session_id) if session_id else None
        self.request_id = request_id or str(uuid.uuid4())
        self.started_at = datetime.utcnow()
        self.events: list[dict[str, Any]] = []
        self.log_dir = Path(log_dir or settings.agent_log_dir)

    def node(
        self,
        name: str,
        start: float,
        output: dict[str, Any] | None = None,
        input_summary: dict[str, Any] | None = None,
        status: str = "completed",
        error: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "node": name,
            "event_id": str(uuid.uuid4()),
            "request_id": self.request_id,
            "status": status,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "timestamp_utc": datetime.utcnow().isoformat(),
            "input_summary": self._sanitize(input_summary or {}),
            "output": self._sanitize(output or {}),
            "error": error,
        }
        self.events.append(event)
        return event

    def event(self, name: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        item = {
            "node": name,
            "event_id": str(uuid.uuid4()),
            "request_id": self.request_id,
            "status": "completed",
            "latency_ms": 0,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "input_summary": {},
            "output": self._sanitize(details or {}),
            "error": None,
        }
        self.events.append(item)
        return item

    def write_run_log(
        self,
        run_id: uuid.UUID | str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> str:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.started_at.strftime("%Y%m%d-%H%M%S")
        path = self.log_dir / f"{timestamp}-{run_id}.log"
        payload = {
            "run_id": str(run_id),
            "request_id": self.request_id,
            "run_type": self.run_type,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "status": status,
            "started_at_utc": self.started_at.isoformat(),
            "completed_at_utc": datetime.utcnow().isoformat(),
            "summary": summary,
            "error": error,
            "nodes": self.events,
        }
        return self._write_readable_run_log(path, payload, run_id, status, summary, error)

    def _write_readable_run_log(
        self,
        path: Path,
        payload: dict[str, Any],
        run_id: uuid.UUID | str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> str:
        with path.open("w", encoding="utf-8") as file:
            file.write(f"AI 私教 Agent 运行日志：{run_id}\n")
            file.write("=" * 72 + "\n")
            file.write(
                "阅读目标：看清本轮 Agent 如何理解当前问题、规划步骤、调用工具、"
                "构建上下文、执行校验、修复问题并保存结果。\n\n"
            )
            file.write(f"请求 ID：{self.request_id}\n")
            file.write(f"运行类型：{self.run_type}\n")
            file.write(f"用户 ID：{self.user_id}\n")
            file.write(f"会话 ID：{self.session_id}\n")
            file.write(f"运行状态：{status}\n")
            if summary:
                file.write(f"最终回复摘要：{summary}\n")
            if error:
                file.write(f"错误摘要：{error}\n")
            file.write("\n一、执行时间线\n")
            file.write("按顺序阅读这一段，可以理解 Agent 从规划、执行、校验到持久化的完整链路。\n")
            for event in self.events:
                file.write(
                    f"- 节点：{event['node']} | 状态：{event['status']} | "
                    f"耗时：{event['latency_ms']}ms | 时间：{event['timestamp_utc']}\n"
                )
                if event.get("input_summary"):
                    file.write(
                        "  输入摘要："
                        + json.dumps(
                            self._compact_for_timeline(event["node"], event["input_summary"]),
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
                if event.get("output"):
                    file.write(
                        "  输出摘要："
                        + json.dumps(
                            self._compact_for_timeline(event["node"], event["output"]),
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
                if event.get("error"):
                    file.write(f"  错误：{event['error']}\n")
            file.write("\n二、完整 JSON（用于深度调试）\n")
            file.write(
                "这一段保留完整结构化事件，适合检查前端 trace、agent_runs.nodes、"
                "tool call 输入输出摘要等细节。\n"
            )
            file.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            file.write("\n")
        return str(path)

    def _legacy_write_run_log(self, path: Path, payload: dict[str, Any], run_id: uuid.UUID | str, status: str) -> str:
        with path.open("w", encoding="utf-8") as file:
            file.write(f"AI 私教 Agent 运行日志：{run_id}\n")
            file.write(f"请求 ID：{self.request_id}\n")
            file.write(f"运行类型：{self.run_type}\n")
            file.write(f"用户 ID：{self.user_id}\n")
            file.write(f"会话 ID：{self.session_id}\n")
            file.write(f"运行状态：{status}\n")
            file.write("\n执行时间线\n")
            for event in self.events:
                file.write(
                    f"- 节点：{event['node']} | 状态：{event['status']} | "
                    f"耗时：{event['latency_ms']}ms | 时间：{event['timestamp_utc']}\n"
                )
                if event.get("input_summary"):
                    file.write(
                        "  输入摘要："
                        + json.dumps(
                            self._compact_for_timeline(event["node"], event["input_summary"]),
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
                if event.get("output"):
                    file.write(
                        "  输出摘要："
                        + json.dumps(
                            self._compact_for_timeline(event["node"], event["output"]),
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
                if event.get("error"):
                    file.write(f"  错误：{event['error']}\n")
            file.write("\n完整 JSON（用于深度调试）\n")
            file.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            file.write("\n")
        return str(path)

    def _compact_for_timeline(self, node: str, value: Any) -> Any:
        if not isinstance(value, dict):
            return self._truncate(value)
        if node == "ContextBuilder":
            knowledge = value.get("knowledge_context") or {}
            return {
                "intent": value.get("intent"),
                "current_request_policy": value.get("current_request_policy") or {},
                "context_summary": value.get("context_summary"),
                "core_profile_present": bool(value.get("core_profile")),
                "active_plan_present": bool(value.get("active_plan")),
                "relevant_memory_count": len(value.get("relevant_memories") or []),
                "active_risk_count": len(value.get("active_risk_notes") or []),
                "knowledge_debug": knowledge.get("debug", {}),
            }
        if node == "AgentTaskTimeline":
            return {
                "timeline_id": value.get("timeline_id"),
                "request_id": value.get("request_id"),
                "goal": self._truncate(value.get("goal")),
                "step_count": len(value.get("steps") or []),
                "steps": [
                    {
                        "name": step.get("name"),
                        "status": step.get("status"),
                        "tool_name": step.get("tool_name"),
                        "reason": step.get("reason"),
                    }
                    for step in (value.get("steps") or [])
                    if isinstance(step, dict)
                ],
            }
        if node == "AgentPlanner":
            return {
                "plan_id": value.get("plan_id"),
                "objective": self._truncate(value.get("objective")),
                "strategy": value.get("strategy"),
                "intent": value.get("intent"),
                "planner_mode": value.get("planner_mode"),
                "safety_level": value.get("safety_level"),
                "plan_generation_allowed": value.get("plan_generation_allowed"),
                "reasoning_summary": self._truncate(value.get("reasoning_summary")),
                "planner_repair_actions": value.get("planner_repair_actions") or [],
                "planner_fallback_reason": self._truncate(value.get("planner_fallback_reason")),
                "assumptions": value.get("assumptions") or [],
                "step_count": len(value.get("steps") or []),
                "steps": [
                    {
                        "key": step.get("key"),
                        "name": step.get("name"),
                        "tool_name": step.get("tool_name"),
                        "stage": step.get("stage"),
                        "required": step.get("required"),
                        "condition": step.get("condition"),
                    }
                    for step in (value.get("steps") or [])
                    if isinstance(step, dict)
                ],
            }
        if node == "LLMPlanner":
            raw = value.get("raw_output") or {}
            return {
                "planner_mode": value.get("planner_mode"),
                "planner_fallback": value.get("planner_fallback", False),
                "intent": raw.get("intent") if isinstance(raw, dict) else None,
                "tool_order": raw.get("tool_order") if isinstance(raw, dict) else [],
                "reasoning_summary": self._truncate(raw.get("reasoning_summary") if isinstance(raw, dict) else None),
            }
        if node == "PlannerVerifier":
            plan = value.get("verified_plan") or {}
            return {
                "intent": plan.get("intent"),
                "planner_mode": plan.get("planner_mode"),
                "repair_actions": value.get("repair_actions") or [],
                "step_count": len(plan.get("steps") or []),
                "tool_order": [
                    step.get("tool_name")
                    for step in (plan.get("steps") or [])
                    if isinstance(step, dict) and step.get("tool_name")
                ],
            }
        if node == "PlannerFallback":
            return {
                "planner_fallback": value.get("planner_fallback", False),
                "reason": self._truncate(value.get("reason")),
            }
        if node == "ToolRegistry":
            return {
                "tool_count": len(value.get("tools") or []),
                "tools": [
                    {
                        "name": tool.get("name"),
                        "permission_level": tool.get("permission_level"),
                        "side_effects": tool.get("side_effects"),
                        "retry_count": tool.get("retry_count"),
                        "has_input_schema": bool(tool.get("input_schema")),
                        "has_output_schema": bool(tool.get("output_schema")),
                        "has_repair_handler": tool.get("has_repair_handler"),
                    }
                    for tool in (value.get("tools") or [])
                    if isinstance(tool, dict)
                ],
            }
        if node == "ToolExecutor":
            return {
                "tool_name": value.get("tool_name"),
                "status": value.get("status"),
                "latency_ms": value.get("latency_ms"),
                "attempts": value.get("attempts"),
                "repaired": value.get("repaired"),
                "repair_actions": value.get("repair_actions") or [],
                "validation_errors": value.get("validation_errors") or [],
                "error": self._truncate(value.get("error")),
                "input_json": self._truncate(value.get("input_json")),
                "output_json": self._truncate(value.get("output_json")),
            }
        if node == "MemoryVerifier":
            return {
                "passed": value.get("passed"),
                "accepted_count": value.get("accepted_count", 0),
                "rejected_count": value.get("rejected_count", 0),
                "issue_count": value.get("issue_count", len(value.get("issues") or [])),
                "repair_actions": value.get("repair_actions") or [],
                "issues": [
                    {
                        "issue_id": issue.get("issue_id"),
                        "severity": issue.get("severity"),
                        "action": issue.get("action"),
                        "message": self._truncate(issue.get("message")),
                    }
                    for issue in (value.get("issues") or [])[:6]
                    if isinstance(issue, dict)
                ],
            }
        if node in {"DecisionRules", "TemplateSelector"}:
            return {
                key: self._truncate(item)
                for key, item in value.items()
                if key.startswith("matched_") or key in {"rules", "templates"}
            }
        return self._truncate(value)

    def _truncate(self, value: Any, depth: int = 0) -> Any:
        if depth > 3:
            return "[truncated]"
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 12:
                    result["_truncated_keys"] = len(value) - index
                    break
                result[str(key)] = self._truncate(item, depth + 1)
            return result
        if isinstance(value, list):
            result = [self._truncate(item, depth + 1) for item in value[:8]]
            if len(value) > 8:
                result.append({"_truncated_items": len(value) - 8})
            return result
        if isinstance(value, str) and len(value) > 500:
            return value[:500] + "...[truncated]"
        return value

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(secret in lowered for secret in self.SECRET_KEYS):
                    sanitized[key] = "[REDACTED]"
                else:
                    sanitized[key] = self._sanitize(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value
