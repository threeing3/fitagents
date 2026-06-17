from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolInput:
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    skip_reason: str | None = None


@dataclass
class ToolRuntimeState:
    """Mutable per-turn state shared by the code-driven tool dispatcher."""

    message: str
    message_chars: int
    onboarding_complete: bool = False
    requires_static_safety: bool = False
    active_plan_exists: bool = False
    context_packet: dict[str, Any] = field(default_factory=dict)
    extraction: dict[str, Any] = field(
        default_factory=lambda: {"profile_patch": {}, "corrections": [], "ignored_candidates": []}
    )
    memory_verification: dict[str, Any] = field(
        default_factory=lambda: {
            "passed": True,
            "accepted_candidates": [],
            "accepted_corrections": [],
            "rejected_candidates": [],
        }
    )
    memories_written: list[str] = field(default_factory=list)
    plan_decision: dict[str, Any] = field(default_factory=dict)
    plan_output: dict[str, Any] = field(default_factory=dict)
    plan_verification: dict[str, Any] = field(default_factory=dict)
    response_verification: dict[str, Any] = field(default_factory=dict)
    response_repair: dict[str, Any] = field(default_factory=dict)
    assistant_message: str = ""
    guardrail: dict[str, Any] = field(default_factory=lambda: {"action": "pass", "flag_count": 0, "flags": []})
    persisted: bool = False
    executed_tools: list[str] = field(default_factory=list)
    skipped_tools: list[dict[str, Any]] = field(default_factory=list)


class ToolInputBuilder:
    """Build tool payloads from runtime state, without executing tools."""

    def __init__(self, state: ToolRuntimeState):
        self.state = state

    def build(self, tool_name: str) -> ToolInput:
        if tool_name == "profile.extract":
            return ToolInput(tool_name, {"message_chars": self.state.message_chars})
        if tool_name == "memory.verify":
            return ToolInput(tool_name, {"extraction": self.state.extraction})
        if tool_name == "memory.write":
            return ToolInput(
                tool_name,
                {"extraction": self.state.extraction, "verification": self.state.memory_verification},
            )
        if tool_name == "context.build":
            if not self.state.onboarding_complete or self.state.requires_static_safety:
                return ToolInput(tool_name, skip_reason="onboarding_or_static_safety")
            return ToolInput(tool_name, {"message_chars": self.state.message_chars})
        if tool_name == "plan.decide":
            if not self.state.context_packet:
                return ToolInput(tool_name, skip_reason="context_not_available")
            return ToolInput(tool_name, {"context_packet": self.state.context_packet})
        if tool_name == "plan.generate":
            if self.state.active_plan_exists or not bool(self.state.plan_decision.get("should_generate_plan")):
                return ToolInput(tool_name, skip_reason="active_plan_exists_or_generation_not_allowed")
            return ToolInput(tool_name, {"reason": self.state.plan_decision.get("reason")})
        if tool_name == "plan.verify":
            if not self.state.plan_output:
                return ToolInput(tool_name, skip_reason="plan_not_generated")
            return ToolInput(
                tool_name,
                {
                    "plan_payload": self.state.plan_output.get("active_plan") or {},
                    "context_packet": self.state.context_packet,
                },
            )
        if tool_name == "plan.repair":
            if not self.state.plan_verification.get("repair_actions"):
                return ToolInput(tool_name, skip_reason="plan_repair_not_required")
            return ToolInput(
                tool_name,
                {
                    "plan_id": self.state.plan_output.get("plan_id"),
                    "plan_payload": self.state.plan_output.get("active_plan") or {},
                    "verification": self.state.plan_verification,
                    "context_packet": self.state.context_packet,
                },
            )
        if tool_name == "response.verify":
            if not self.state.context_packet:
                return ToolInput(tool_name, skip_reason="context_not_available")
            return ToolInput(
                tool_name,
                {
                    "assistant_message": self.state.assistant_message[:6000],
                    "context_packet": self.state.context_packet,
                },
            )
        if tool_name == "response.repair":
            if not self.state.response_verification.get("repair_actions"):
                return ToolInput(tool_name, skip_reason="response_repair_not_required")
            return ToolInput(
                tool_name,
                {
                    "verification": self.state.response_verification,
                    "context_packet": self.state.context_packet,
                },
            )
        if tool_name == "guardrail.check":
            return ToolInput(tool_name, {"assistant_message": self.state.assistant_message[:4000]})
        if tool_name == "response.persist":
            return ToolInput(tool_name, {"assistant_message": self.state.assistant_message})
        return ToolInput(tool_name, {})


class ToolOutputReducer:
    """Reduce tool outputs into runtime state. Domain side effects stay in Host code."""

    def __init__(self, state: ToolRuntimeState):
        self.state = state

    def reduce(self, tool_name: str, output: dict[str, Any]) -> None:
        self.state.executed_tools.append(tool_name)
        if tool_name == "profile.extract":
            self.state.extraction = output
        elif tool_name == "memory.verify":
            self.state.memory_verification = output
        elif tool_name == "memory.write":
            self.state.memories_written = output.get("written") or []
        elif tool_name == "context.build":
            self.state.context_packet = output
        elif tool_name == "plan.decide":
            self.state.plan_decision = output
        elif tool_name == "plan.generate":
            self.state.plan_output = output
            if output.get("active_plan"):
                self.state.context_packet["active_plan"] = output.get("active_plan")
        elif tool_name == "plan.verify":
            self.state.plan_verification = output
        elif tool_name == "plan.repair":
            if output.get("active_plan"):
                self.state.context_packet["active_plan"] = output.get("active_plan")
        elif tool_name == "response.verify":
            self.state.response_verification = output
        elif tool_name == "response.repair":
            self.state.response_repair = output
            repair_text = output.get("repair_text") or ""
            if repair_text:
                self.state.assistant_message = (self.state.assistant_message + repair_text).strip()
        elif tool_name == "guardrail.check":
            self.state.guardrail = {
                "action": output.get("action"),
                "flag_count": output.get("flag_count", 0),
                "flags": output.get("flags", []),
            }
        elif tool_name == "response.persist":
            self.state.persisted = True

    def skip(self, tool_name: str, reason: str) -> dict[str, Any]:
        payload = {"tool_name": tool_name, "reason": reason}
        self.state.skipped_tools.append(payload)
        return payload


class ReplayRunner:
    """Build replay/debug packets for planner and dispatcher outputs."""

    @staticmethod
    def tool_order_from_plan(execution_plan: Any) -> list[str]:
        return [
            step.tool_name
            for step in getattr(execution_plan, "steps", [])
            if getattr(step, "tool_name", None)
        ]

    @staticmethod
    def tool_plan_json(
        execution_plan: Any,
        timeline: Any,
        tool_contracts: list[dict[str, Any]],
        contract_issues: list[dict[str, Any]],
        planner_debug: dict[str, Any],
        dispatcher_state: ToolRuntimeState | None = None,
    ) -> dict[str, Any]:
        payload = {
            "execution_plan": execution_plan.to_dict(),
            "llm_planner_raw": planner_debug.get("llm_planner_raw"),
            "planner_verified_plan": planner_debug.get("planner_verified_plan"),
            "planner_repair_actions": planner_debug.get("planner_repair_actions", []),
            "planner_fallback_reason": planner_debug.get("planner_fallback_reason"),
            "timeline": timeline.to_dict(),
            "tool_contracts": tool_contracts,
            "contract_issues": contract_issues,
            "verified_tool_order": ReplayRunner.tool_order_from_plan(execution_plan),
        }
        if dispatcher_state is not None:
            payload["dispatcher"] = {
                "executed_tools": dispatcher_state.executed_tools,
                "skipped_tools": dispatcher_state.skipped_tools,
                "persisted": dispatcher_state.persisted,
                "guardrail": dispatcher_state.guardrail,
            }
        return payload
