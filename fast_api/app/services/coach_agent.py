import json
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.schemas.agent import (
    DailyCheckinRequest,
    PlanAdjustRequest,
    PlanGenerateRequest,
    UserProfileInput,
    WorkoutLogRequest,
)
from fast_api.app.services.fitness_math import (
    adjustment_multiplier,
    calculate_macro_targets,
)
from fast_api.app.services.context_builder import ContextBuilder
from fast_api.app.services.agent_observability import AgentRunLogger
from fast_api.app.services.agent_runtime import (
    AgentExecutor,
    AgentPlanner,
    AgentTaskTimeline,
    LLMPlanner,
    PlannerVerifier,
    TaskStep,
    ToolRegistry,
    ToolSpec,
)
from fast_api.app.services.agent_tool_dispatcher import (
    ReplayRunner,
    ToolInputBuilder,
    ToolOutputReducer,
    ToolRuntimeState,
)
from fast_api.app.services.agent_verifier import AgentVerifier
from fast_api.app.services.decision_logger import DecisionLogger
from fast_api.app.services.memory_verifier import MemoryVerifier
from fast_api.app.core.guardrails import run_guardrails, Severity as GuardrailSeverity
from fast_api.app.core.prompts import registry
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService, KNOWLEDGE_DIR
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.core.metrics import track_llm_call
from fast_api.app.core.config import get_settings
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.semantic_cache import SemanticCacheService
from fast_api.app.services.feedback_learner_integration import get_adaptive_system_prompt
from fast_api.app.services.llm_agent import LLMAgentService as LLMAgent
from fast_api.app.services.agent_task_state import AgentTaskStateService
from fast_api.app.services.memory_conflict_resolver import MemoryConflictResolver
from fast_api.app.services.runtime_router import RuntimeRoute, RuntimeRouter


REQUIRED_ONBOARDING_SLOTS = [
    "age",
    "height_cm",
    "weight_kg",
    "goal",
    "experience_level",
    "equipment_available",
]


class CoachAgentService:
    def __init__(self, db: Session, model_provider: ModelProvider | None = None):
        self.db = db
        self.model_provider = model_provider or ModelProvider()
        self.cache = SemanticCacheService(db, self.model_provider)
        self.runtime_router = RuntimeRouter()

    def create_session(
        self,
        user_id: uuid.UUID | None,
        display_name: str,
        title: str,
    ) -> models.ConversationSession:
        user = self.ensure_user(user_id=user_id, display_name=display_name)
        session = models.ConversationSession(user_id=user.id, title=title)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def ensure_user(
        self,
        user_id: uuid.UUID | None,
        display_name: str = "Fitness User",
    ) -> models.User:
        """Look up an existing user. Users must be created via the auth API first."""
        if user_id is None:
            raise ValueError("Authenticated user required — please register or log in first.")
        user = self.db.get(models.User, user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found. Register via POST /v1/auth/register first.")
        return user

    def upsert_profile(self, profile_input: UserProfileInput) -> models.UserProfile:
        user = self.ensure_user(profile_input.user_id, profile_input.display_name)
        profile = self._get_or_create_profile(user.id)
        self._apply_profile_payload(profile, profile_input.model_dump(exclude={"user_id", "display_name"}))
        self._refresh_macro_targets(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    async def handle_chat_message(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
    ) -> dict[str, Any]:
        """Dispatch each turn through the lightweight RuntimeRouter."""
        route = self._route_runtime(message)
        if route.mode == "llm_driven":
            result = await self._handle_chat_llm_agent(session_id, user_id, message, route)
        else:
            result = await self._handle_chat_code_driven(session_id, user_id, message, route)
        result["runtime_route"] = route.to_dict()
        return result

    def _route_runtime(self, message: str) -> RuntimeRoute:
        settings = get_settings()
        if settings.agent_runtime_mode == "llm_driven":
            return RuntimeRoute(
                mode="llm_driven",
                reason="AGENT_RUNTIME_MODE=llm_driven 强制走 LLM-driven。",
                matched_rules=["config.force_llm_driven"],
                confidence=1.0,
            )
        if settings.agent_runtime_mode == "code_driven":
            return RuntimeRoute(
                mode="code_driven",
                reason="AGENT_RUNTIME_MODE=code_driven 强制走 Code-driven。",
                matched_rules=["config.force_code_driven"],
                confidence=1.0,
            )
        if settings.use_llm_driven_agent:
            route = self.runtime_router.route(message)
            if route.mode == "llm_driven":
                route.reason = "兼容 USE_LLM_DRIVEN_AGENT=true：轻量请求允许走 LLM-driven；强规则仍保持 Code-driven。"
                route.matched_rules = ["legacy.use_llm_driven_agent"] + route.matched_rules
            return route
        return self.runtime_router.route(message)

    async def _build_code_driven_execution_plan(
        self,
        message: str,
        tool_registry: ToolRegistry,
        profile: models.UserProfile,
        runtime_route: RuntimeRoute | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        settings = get_settings()
        available_tools = tool_registry.list_specs()
        debug: dict[str, Any] = {
            "planner_mode": settings.code_driven_planner,
            "llm_planner_raw": None,
            "planner_verified_plan": None,
            "planner_repair_actions": [],
            "planner_fallback_reason": None,
            "planner_fallback": False,
        }

        if settings.code_driven_planner == "llm":
            try:
                active_plan = self.get_active_plan(profile.user_id)
                active_plan_summary = None
                if active_plan is not None:
                    active_plan_summary = {
                        "plan_id": str(active_plan.id),
                        "title": active_plan.title,
                        "status": active_plan.status,
                    }
                decision = await LLMPlanner(self.model_provider).plan(
                    message=message,
                    available_tools=available_tools,
                    runtime_route=runtime_route.to_dict() if runtime_route else None,
                    profile_summary=self._profile_snapshot(profile),
                    active_plan_summary=active_plan_summary,
                )
                debug["llm_planner_raw"] = decision.raw_output
                verified = PlannerVerifier().verify_and_repair(
                    decision,
                    available_tools=available_tools,
                    message=message,
                    runtime_route=runtime_route.to_dict() if runtime_route else None,
                )
                execution_plan = verified.to_execution_plan(message)
                debug["planner_verified_plan"] = execution_plan.to_dict()
                debug["planner_repair_actions"] = verified.repair_actions
                return execution_plan, debug
            except Exception as exc:
                if settings.code_driven_planner_fallback != "rule":
                    raise
                debug["planner_fallback"] = True
                debug["planner_fallback_reason"] = str(exc)

        execution_plan = AgentPlanner().plan_chat_turn(message, available_tools)
        if debug["planner_fallback_reason"]:
            execution_plan.planner_mode = "rule_fallback"
            execution_plan.planner_fallback_reason = debug["planner_fallback_reason"]
            execution_plan.planner_repair_actions = ["fallback_to_rule_planner"]
        debug["planner_verified_plan"] = execution_plan.to_dict()
        return execution_plan, debug

    async def _handle_chat_code_driven(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
        runtime_route: RuntimeRoute | None = None,
    ) -> dict[str, Any]:
        """Legacy code-driven pipeline: ToolRegistry -> AgentPlanner -> AgentExecutor."""
        started_at = datetime.utcnow()
        nodes: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        state_updates: dict[str, Any] = {}

        session = self.db.get(models.ConversationSession, session_id)
        if not session:
            raise ValueError("Conversation session not found")
        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)
        run_logger = AgentRunLogger("chat", user.id, session.id)
        timeline = AgentTaskTimeline(message, request_id=run_logger.request_id)
        tool_registry = self._build_chat_tool_registry(user.id, session.id, profile, message)
        executor = AgentExecutor()
        execution_plan, planner_debug = await self._build_code_driven_execution_plan(
            message, tool_registry, profile, runtime_route
        )
        timeline_steps = {
            planned.key: timeline.add_step(planned.name, planned.tool_name, planned.reason)
            for planned in execution_plan.steps
        }

        # ---- Register runtime environment ----
        registry_node = run_logger.event("ToolRegistry", {"tools": tool_registry.list_specs()})
        nodes.append(registry_node)
        contract_issues = tool_registry.validate_contracts()
        nodes.append(run_logger.event("ToolContractAudit", {"issues": contract_issues}))
        state_updates["tool_contract_issues"] = contract_issues
        nodes.append(run_logger.event("LLMPlanner", {
            "raw_output": planner_debug.get("llm_planner_raw"),
            "planner_mode": execution_plan.planner_mode,
            "planner_fallback": planner_debug.get("planner_fallback", False),
        }))
        if planner_debug.get("planner_fallback"):
            nodes.append(run_logger.event("PlannerFallback", {
                "planner_fallback": True,
                "reason": planner_debug.get("planner_fallback_reason"),
            }))
        nodes.append(run_logger.event("PlannerVerifier", {
            "verified_plan": planner_debug.get("planner_verified_plan"),
            "repair_actions": planner_debug.get("planner_repair_actions", []),
        }))
        state_updates["planner"] = {
            "mode": execution_plan.planner_mode,
            "fallback": planner_debug.get("planner_fallback", False),
            "fallback_reason": planner_debug.get("planner_fallback_reason"),
            "repair_actions": planner_debug.get("planner_repair_actions", []),
        }
        planner_node = run_logger.event("AgentPlanner", execution_plan.to_dict())
        nodes.append(planner_node)
        timeline_node = run_logger.event("AgentTaskTimeline", timeline.to_dict())
        nodes.append(timeline_node)
        run_logger.event(
            "RequestReceived",
            {
                "message_chars": len(message),
                "provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
                "embedding_mode": self.model_provider.embedding_mode(),
            },
        )
        if runtime_route is not None:
            route_payload = runtime_route.to_dict()
            nodes.append(run_logger.event("RuntimeRouter", route_payload))
            state_updates["agent_mode"] = runtime_route.mode
            state_updates["runtime_route"] = route_payload

        self._save_message(session.id, user.id, "user", message)

        # ---- Tool helper (non-streaming: collect instead of yield) ----
        def planned_or_new_step(key: str, name: str, tool_name: str, reason: str) -> TaskStep:
            return timeline_steps.get(key) or timeline.add_step(name, tool_name, reason)

        async def execute_tool(tool_name: str, input_json: dict[str, Any], timeline_step: TaskStep):
            execution = await executor.execute(tool_registry, timeline, timeline_step, input_json)
            nodes.append(run_logger.event("TaskStep", execution.started_event))
            result = execution.result
            tool_payload = self._summarize_tool_execution(result.to_trace())
            tool_calls.append({
                "tool_name": result.tool_name,
                "status": result.status,
                "input": tool_payload.get("input_json", {}),
                "output": tool_payload.get("output_json", {}),
                "latency_ms": result.latency_ms,
                "attempts": result.attempts,
                "validation_errors": result.validation_errors,
                "repaired": result.repaired,
                "repair_actions": result.repair_actions,
                "contract": result.contract,
                "idempotency_key": result.idempotency_key,
            })
            nodes.append(run_logger.event("ToolExecutor", tool_payload))
            timeline_step.output_summary = tool_payload.get("output_json", {})
            completed_event = dict(execution.completed_event)
            completed_event["output_summary"] = tool_payload.get("output_json", {})
            nodes.append(run_logger.event("TaskStep", completed_event))
            if result.status != "success":
                raise RuntimeError(result.error or f"Tool failed: {tool_name}")
            return result.output_json

        tool_steps = {
            planned.tool_name: timeline_steps[planned.key]
            for planned in execution_plan.steps
            if planned.tool_name and planned.key in timeline_steps
        }
        execution_order = [planned.tool_name for planned in execution_plan.steps if planned.tool_name]
        nodes.append(run_logger.event("AgentToolOrderDispatch", {
            "tool_order": execution_order,
            "planner_mode": execution_plan.planner_mode,
        }))

        chunks: list[str] = []
        context_packet: dict[str, Any] = {}
        extraction: dict[str, Any] = {"profile_patch": {}, "corrections": [], "ignored_candidates": []}
        memory_verify_output: dict[str, Any] = {
            "passed": True,
            "accepted_candidates": [],
            "accepted_corrections": [],
            "rejected_candidates": [],
        }
        memories_written: list[str] = []
        missing_slots = self.missing_onboarding_slots(profile)
        onboarding_complete = not missing_slots
        plan_decision: dict[str, Any] = {}
        plan_output: dict[str, Any] = {}
        plan_verify_output: dict[str, Any] = {}
        assistant_message = ""
        assistant_msg = None
        guardrail_payload: dict[str, Any] = {"action": "pass", "flag_count": 0, "flags": []}
        dispatcher_state = ToolRuntimeState(
            message=message,
            message_chars=len(message),
            onboarding_complete=onboarding_complete,
            requires_static_safety=self._requires_immediate_safety_reply(message),
            active_plan_exists=self.get_active_plan(user.id) is not None,
        )
        input_builder = ToolInputBuilder(dispatcher_state)
        output_reducer = ToolOutputReducer(dispatcher_state)

        try:
            for tool_name in execution_order:
                step = tool_steps.get(tool_name)
                if step is None:
                    continue
                dispatcher_state.onboarding_complete = onboarding_complete
                dispatcher_state.active_plan_exists = self.get_active_plan(user.id) is not None
                dispatcher_state.context_packet = context_packet
                dispatcher_state.extraction = extraction
                dispatcher_state.memory_verification = memory_verify_output
                dispatcher_state.plan_decision = plan_decision
                dispatcher_state.plan_output = plan_output
                dispatcher_state.plan_verification = plan_verify_output
                dispatcher_state.assistant_message = "".join(chunks).strip() or assistant_message
                tool_input = input_builder.build(tool_name)
                if tool_input.skip_reason:
                    nodes.append(run_logger.event("ToolSkipped", output_reducer.skip(tool_name, tool_input.skip_reason)))
                    continue
                if tool_name == "profile.extract":
                    node_start = time.perf_counter()
                    extraction = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, extraction)
                    if extraction.get("profile_patch") or extraction.get("corrections"):
                        self._apply_profile_extraction(profile, extraction)
                        self._refresh_macro_targets(profile)
                        state_updates["profile_updates"] = extraction.get("profile_patch", {})
                        state_updates["corrections"] = extraction.get("corrections", [])
                    nodes.append(run_logger.node("ProfileExtractorAgent", node_start, extraction, {"message": message[:240]}))
                elif tool_name == "memory.verify":
                    memory_verify_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, memory_verify_output)
                    state_updates["memory_verification"] = memory_verify_output
                    nodes.append(run_logger.event("MemoryVerifier", memory_verify_output))
                elif tool_name == "memory.write":
                    node_start_mem = time.perf_counter()
                    memory_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, memory_output)
                    memories_written = memory_output.get("written") or []
                    nodes.append(run_logger.node("MemoryAgent", node_start_mem, {"written": memories_written}))
                    missing_slots = self.missing_onboarding_slots(profile)
                    onboarding_complete = not missing_slots
                    nodes.append(run_logger.event(
                        "IntentRouter",
                        {"onboarding_complete": onboarding_complete, "missing_slots": missing_slots},
                    ))
                elif tool_name == "context.build":
                    node_start_ctx = time.perf_counter()
                    context_packet = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, context_packet)
                    nodes.append(run_logger.node("ContextBuilder", node_start_ctx, context_packet))
                    knowledge_context = context_packet.get("knowledge_context") or {}
                    nodes.append(run_logger.event("KnowledgeRetrieval", knowledge_context.get("debug", {})))
                    nodes.append(run_logger.event("DecisionRules", {
                        "matched_rule_ids": knowledge_context.get("debug", {}).get("matched_rule_ids", []),
                        "rules": knowledge_context.get("decision_rules", []),
                    }))
                    nodes.append(run_logger.event("TemplateSelector", {
                        "matched_template_ids": knowledge_context.get("debug", {}).get("matched_template_ids", []),
                        "templates": knowledge_context.get("plan_templates", []),
                    }))
                    state_updates["context_intent"] = context_packet.get("intent")
                    state_updates["knowledge_debug"] = knowledge_context.get("debug", {})
                elif tool_name == "plan.decide":
                    plan_decision = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, plan_decision)
                    nodes.append(run_logger.event("CurrentRequestPolicy", context_packet.get("current_request_policy", {})))
                    nodes.append(run_logger.event("PlanGenerationDecision", plan_decision))
                elif tool_name == "plan.generate":
                    plan_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, plan_output)
                    context_packet["active_plan"] = plan_output.get("active_plan")
                    state_updates["generated_plan_id"] = plan_output.get("plan_id")
                    if self._build_plan_reflection_prompt(plan_output, context_packet, profile):
                        plan_output["_reflection"] = "Requested"
                        nodes.append(run_logger.event("PlanSelfCorrection", {
                            "step": "reflection_requested",
                            "plan_id": plan_output.get("plan_id"),
                        }))
                elif tool_name == "plan.verify":
                    plan_verify_output = await execute_tool(
                        tool_name,
                        tool_input.payload,
                        step,
                    )
                    output_reducer.reduce(tool_name, plan_verify_output)
                    state_updates["plan_verification"] = plan_verify_output
                    nodes.append(run_logger.event("PlanVerifier", plan_verify_output))
                elif tool_name == "plan.repair":
                    plan_repair_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, plan_repair_output)
                    if plan_repair_output.get("active_plan"):
                        context_packet["active_plan"] = plan_repair_output.get("active_plan")
                    state_updates["plan_repair"] = plan_repair_output
                    nodes.append(run_logger.event("PlanRepair", plan_repair_output))
                elif tool_name == "coach.reply":
                    node_start_reply = time.perf_counter()
                    timeline.start(step)
                    if self._requires_immediate_safety_reply(message):
                        assistant_message = self._safety_reply()
                        coach_payload = {"safety": True, "mode": "static_safety"}
                    elif not onboarding_complete:
                        assistant_message = await self._live_onboarding_reply(profile, missing_slots, message)
                        coach_payload = {
                            "mode": "onboarding",
                            "live_model": self.model_provider.has_live_model(),
                            "missing_slots": missing_slots,
                        }
                    else:
                        assistant_message = await self._coaching_reply(user.id, message, context_packet)
                        coach_payload = {
                            "safety": False,
                            "live_model": self.model_provider.has_live_model(),
                            "response_chars": len(assistant_message),
                        }
                    chunks = [assistant_message]
                    timeline.complete(step, {"response_chars": len(assistant_message), **coach_payload}, round((time.perf_counter() - node_start_reply) * 1000))
                    nodes.append(run_logger.node("CoachLLM", node_start_reply, coach_payload))
                elif tool_name == "response.verify":
                    assistant_message = "".join(chunks).strip() or registry.get("error_coach_stream_empty")
                    response_verify_output = await execute_tool(
                        tool_name,
                        tool_input.payload,
                        step,
                    )
                    output_reducer.reduce(tool_name, response_verify_output)
                    state_updates["response_verification"] = response_verify_output
                    nodes.append(run_logger.event("ResponseVerifier", response_verify_output))
                elif tool_name == "response.repair":
                    response_repair_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, response_repair_output)
                    repair_text = response_repair_output.get("repair_text") or ""
                    if repair_text:
                        assistant_message = (assistant_message + repair_text).strip()
                        chunks = [assistant_message]
                    state_updates["response_repair"] = response_repair_output
                    nodes.append(run_logger.event("ResponseRepair", response_repair_output))
                elif tool_name == "guardrail.check":
                    assistant_message = "".join(chunks).strip() or assistant_message or registry.get("error_coach_stream_empty")
                    dispatcher_state.assistant_message = assistant_message
                    tool_input = input_builder.build(tool_name)
                    guardrail_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, guardrail_output)
                    guardrail_payload = {
                        "action": guardrail_output.get("action"),
                        "flag_count": guardrail_output.get("flag_count", 0),
                        "flags": guardrail_output.get("flags", []),
                    }
                    nodes.append(run_logger.event("GuardrailCheck", guardrail_payload))
                    if guardrail_output.get("action") == GuardrailSeverity.BLOCK.value:
                        assistant_message = guardrail_output.get("replacement") or assistant_message
                        chunks = [assistant_message]
                elif tool_name == "response.persist":
                    assistant_message = "".join(chunks).strip() or assistant_message or registry.get("error_coach_stream_empty")
                    dispatcher_state.assistant_message = assistant_message
                    tool_input = input_builder.build(tool_name)
                    persist_output = await execute_tool(tool_name, tool_input.payload, step)
                    output_reducer.reduce(tool_name, persist_output)
                    assistant_msg = self._save_message(session.id, user.id, "assistant", assistant_message)
                    nodes.append(run_logger.event("ResponsePersisted", {"response_chars": len(assistant_message)}))
        except Exception as exc:
            error_text = f"\n\n{self._model_call_error_message(exc)} 请稍后重试。"
            chunks.append(error_text)
            nodes.append(run_logger.event("RuntimeError", {"error": str(exc)}))

        assistant_message = "".join(chunks).strip() or assistant_message or registry.get("error_coach_stream_empty")
        if assistant_msg is None:
            assistant_msg = self._save_message(session.id, user.id, "assistant", assistant_message)
            nodes.append(run_logger.event("ResponsePersisted", {"response_chars": len(assistant_message), "fallback_persist": True}))

        run = models.AgentRun(
            user_id=user.id,
            session_id=session.id,
            run_type="chat",
            status="completed",
            nodes=nodes,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            summary=assistant_message[:500],
        )
        self.db.add(run)
        self.db.flush()

        task_service = AgentTaskStateService(self.db)
        long_term_tasks = task_service.update_from_chat_turn(
            user_id=user.id,
            message=message,
            profile=profile,
            context_packet=context_packet,
            state_updates=state_updates,
            agent_run_id=run.id,
        )
        state_updates["long_term_tasks"] = long_term_tasks
        task_node = run_logger.event("LongTermTaskState", {"tasks": long_term_tasks})
        nodes.append(task_node)
        run.nodes = nodes
        task_service.record_replay_snapshot(
            agent_run=run,
            request_json={
                "session_id": str(session.id),
                "message": message,
                "message_chars": len(message),
                "runtime_route": runtime_route.to_dict() if runtime_route else None,
            },
            state_snapshot={
                "profile": self._profile_snapshot(profile),
                "context_packet": self._truncate_trace_payload(context_packet),
                "state_updates": self._truncate_trace_payload(state_updates),
                "active_tasks": long_term_tasks,
            },
            tool_plan_json=ReplayRunner.tool_plan_json(
                execution_plan=execution_plan,
                timeline=timeline,
                tool_contracts=tool_registry.list_specs(),
                contract_issues=contract_issues,
                planner_debug=planner_debug,
                dispatcher_state=dispatcher_state,
            ),
            response_snapshot={
                "assistant_message": assistant_message,
                "guardrail": guardrail_payload,
                "tool_calls": tool_calls,
            },
            config_snapshot={
                "llm_provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
                "embedding_mode": self.model_provider.embedding_mode(),
                "agent_runtime_mode": get_settings().agent_runtime_mode,
                "code_driven_planner": get_settings().code_driven_planner,
                "code_driven_planner_fallback": get_settings().code_driven_planner_fallback,
            },
        )

        for call in tool_calls:
            self.db.add(models.ToolCall(
                agent_run_id=run.id,
                tool_name=call["tool_name"],
                input_json=call.get("input", {}),
                output_json=call.get("output", {}),
                latency_ms=call.get("latency_ms", 0),
                status=call.get("status", "success"),
            ))

        self.db.commit()
        self.db.refresh(run)
        log_path = run_logger.write_run_log(run.id, "completed", assistant_message[:500])
        run.log_path = log_path
        self.db.commit()
        state_updates["agent_log_path"] = log_path

        return {
            "user_id": user.id,
            "assistant_message": assistant_message,
            "agent_run_id": run.id,
            "feedback_message_id": assistant_msg.id,
            "onboarding_complete": onboarding_complete,
            "missing_slots": missing_slots,
            "memories_written": memories_written,
            "tool_calls": tool_calls,
            "state_updates": state_updates,
            "guardrail": {
                "action": guardrail_payload.get("action"),
                "passed": guardrail_payload.get("action") != "block",
                "flags": guardrail_payload.get("flags", []),
            },
        }


    async def stream_chat_message(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
    ):
        started_at = datetime.utcnow()
        nodes: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        state_updates: dict[str, Any] = {}
        chunks: list[str] = []

        session = self.db.get(models.ConversationSession, session_id)
        if not session:
            yield "Conversation session not found"
            return

        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)
        run_logger = AgentRunLogger("chat_stream", user.id, session.id)
        run_logger.event(
            "RequestReceived",
            {
                "message_chars": len(message),
                "provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
                "embedding_mode": self.model_provider.embedding_mode(),
            },
        )
        self._save_message(session.id, user.id, "user", message)

        node_start = time.perf_counter()
        extraction = await self.profile_extractor_agent(profile, message)
        if extraction["profile_patch"] or extraction["corrections"]:
            self._apply_profile_extraction(profile, extraction)
            self._refresh_macro_targets(profile)
            state_updates["profile_updates"] = extraction["profile_patch"]
            state_updates["corrections"] = extraction["corrections"]
        nodes.append(run_logger.node("ProfileExtractorAgent", node_start, extraction, {"message": message[:240]}))

        node_start = time.perf_counter()
        memories_written = self.write_memories_from_message(user.id, message, extraction)
        nodes.append(run_logger.node("MemoryAgent", node_start, {"written": memories_written}))

        missing_slots = self.missing_onboarding_slots(profile)
        onboarding_complete = not missing_slots
        nodes.append(
            run_logger.event(
                "IntentRouter",
                {"onboarding_complete": onboarding_complete, "missing_slots": missing_slots},
            )
        )

        try:
            if self._requires_immediate_safety_reply(message):
                node_start = time.perf_counter()
                async for chunk in self._stream_static_text(self._safety_reply()):
                    chunks.append(chunk)
                    yield chunk
                nodes.append(run_logger.node("CoachLLM", node_start, {"safety": True, "mode": "static_safety"}))
            elif not onboarding_complete:
                node_start = time.perf_counter()
                async for chunk in self._live_onboarding_reply_stream(
                    profile, missing_slots, message
                ):
                    chunks.append(chunk)
                    yield chunk
                nodes.append(
                    run_logger.node(
                        "CoachLLM",
                        node_start,
                        {
                            "mode": "onboarding",
                            "live_model": self.model_provider.has_live_model(),
                            "missing_slots": missing_slots,
                        },
                    )
                )
            else:
                node_start = time.perf_counter()
                context_packet = ContextBuilder(self.db, self.model_provider).build_context_packet(user.id, message)
                nodes.append(run_logger.node("ContextBuilder", node_start, context_packet))

                active_plan = self.get_active_plan(user.id)
                should_generate_plan = self._should_generate_plan_for_context(context_packet)
                plan_decision = {
                    "intent": context_packet.get("intent"),
                    "active_plan_exists": active_plan is not None,
                    "should_generate_plan": should_generate_plan,
                    "reason": (
                        "current_message_explicitly_requests_plan"
                        if should_generate_plan
                        else "current_message_does_not_request_plan"
                    ),
                }
                nodes.append(run_logger.event("CurrentRequestPolicy", context_packet.get("current_request_policy", {})))
                nodes.append(run_logger.event("PlanGenerationDecision", plan_decision))
                if active_plan is None and should_generate_plan:
                    plan_result = self.generate_plan(PlanGenerateRequest(user_id=user.id))
                    context_packet["active_plan"] = self._plan_context_payload(plan_result)
                    state_updates["generated_plan_id"] = str(plan_result.id)
                    tool_calls.append(
                        {
                            "tool_name": "generate_training_plan",
                            "status": "success",
                            "output": {"plan_id": str(plan_result.id)},
                        }
                    )
                knowledge_context = context_packet.get("knowledge_context") or {}
                nodes.append(run_logger.event("KnowledgeRetrieval", knowledge_context.get("debug", {})))
                nodes.append(
                    run_logger.event(
                        "DecisionRules",
                        {
                            "matched_rule_ids": knowledge_context.get("debug", {}).get("matched_rule_ids", []),
                            "rules": knowledge_context.get("decision_rules", []),
                        },
                    )
                )
                nodes.append(
                    run_logger.event(
                        "TemplateSelector",
                        {
                            "matched_template_ids": knowledge_context.get("debug", {}).get("matched_template_ids", []),
                            "templates": knowledge_context.get("plan_templates", []),
                        },
                    )
                )
                state_updates["context_intent"] = context_packet.get("intent")
                state_updates["knowledge_debug"] = knowledge_context.get("debug", {})
                if "feedback_debug" in locals() and feedback_debug.get("enhanced"):
                    state_updates["feedback_learning"] = feedback_debug
                node_start = time.perf_counter()
                async for chunk in self._coaching_reply_stream(user.id, message, context_packet):
                    chunks.append(chunk)
                    yield chunk
                nodes.append(
                    run_logger.node(
                        "CoachLLM",
                        node_start,
                        {
                            "safety": False,
                            "live_model": self.model_provider.has_live_model(),
                            "response_chars": len("".join(chunks)),
                        },
                    )
                )
        except Exception as exc:
            error_text = f"\n\n{self._model_call_error_message(exc)} 请稍后重试。"
            chunks.append(error_text)
            nodes.append(run_logger.event("RuntimeError", {"error": str(exc)}))
            yield error_text

        assistant_message = "".join(chunks).strip()
        if not assistant_message:
            assistant_message = registry.get("error_coach_stream_empty")
            yield assistant_message

        # ---- Safety guardrail check ----
        guardrail_result = run_guardrails(assistant_message, user_message=message, profile=profile)
        nodes.append(run_logger.event("GuardrailCheck", {
            "action": guardrail_result.action.value,
            "flag_count": len(guardrail_result.flags),
            "flags": [{"rule_id": f.rule_id, "severity": f.severity.value, "category": f.category} for f in guardrail_result.flags],
        }))
        if guardrail_result.action == GuardrailSeverity.BLOCK:
            assistant_message = guardrail_result.blocked_replacement or assistant_message
            # Yield the replacement so the user sees the safe version
            yield assistant_message

        self._save_message(session.id, user.id, "assistant", assistant_message)
        nodes.append(run_logger.event("ResponsePersisted", {"response_chars": len(assistant_message)}))
        run = models.AgentRun(
            user_id=user.id,
            session_id=session.id,
            run_type="chat_stream",
            status="completed",
            nodes=nodes,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            summary=assistant_message[:500],
        )
        self.db.add(run)
        self.db.flush()
        task_service = AgentTaskStateService(self.db)
        long_term_tasks = task_service.update_from_chat_turn(
            user_id=user.id,
            message=message,
            profile=profile,
            context_packet=context_packet if "context_packet" in locals() else {},
            state_updates=state_updates,
            agent_run_id=run.id,
        )
        state_updates["long_term_tasks"] = long_term_tasks
        task_node = run_logger.event("LongTermTaskState", {"tasks": long_term_tasks})
        nodes.append(task_node)
        yield step_event("LongTermTaskState", task_node, task_node.get("output", {}))
        run.nodes = nodes
        task_service.record_replay_snapshot(
            agent_run=run,
            request_json={
                "session_id": str(session.id),
                "message": message,
                "message_chars": len(message),
                "stream": True,
            },
            state_snapshot={
                "profile": self._profile_snapshot(profile),
                "context_packet": self._truncate_trace_payload(context_packet if "context_packet" in locals() else {}),
                "state_updates": self._truncate_trace_payload(state_updates),
                "active_tasks": long_term_tasks,
            },
            tool_plan_json={
                "execution_plan": execution_plan.to_dict(),
                "timeline": timeline.to_dict(),
                "tool_contracts": tool_registry.list_specs(),
                "contract_issues": contract_issues,
            },
            response_snapshot={
                "assistant_message": assistant_message,
                "guardrail": guardrail_payload,
                "tool_calls": tool_calls,
            },
            config_snapshot={
                "llm_provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
                "embedding_mode": self.model_provider.embedding_mode(),
            },
        )
        for call in tool_calls:
            self.db.add(
                models.ToolCall(
                    agent_run_id=run.id,
                    tool_name=call["tool_name"],
                    input_json=call.get("input", {}),
                output_json=call.get("output", {}),
                latency_ms=call.get("latency_ms", 0),
                status=call.get("status", "success"),
            ))

        self.db.commit()
        self.db.refresh(run)
        run.log_path = run_logger.write_run_log(run.id, "completed", assistant_message[:500])
        self.db.commit()

    async def stream_chat_events(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
    ):
        """Dispatch streaming to LLM-driven or code-driven pipeline per turn."""
        def event(event_type: str, **payload):
            return json.dumps({"type": event_type, **payload}, ensure_ascii=False, default=str) + "\n"

        route = self._route_runtime(message)
        yield event("runtime_route", **route.to_dict())
        if route.mode == "llm_driven":
            async for chunk in self._stream_chat_llm_agent(session_id, user_id, message, route):
                yield chunk
            return
        async for chunk in self._stream_chat_code_driven(session_id, user_id, message, route):
            yield chunk

    async def _stream_chat_llm_agent(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
        runtime_route: RuntimeRoute | None = None,
    ):
        def event(event_type: str, **payload):
            return json.dumps({"type": event_type, **payload}, ensure_ascii=False, default=str) + "\n"

        yield event("status", text="正在思考")
        result = await self._handle_chat_llm_agent(session_id, user_id, message, runtime_route)
        assistant_message = result.get("assistant_message") or ""
        for index in range(0, len(assistant_message), 3):
            yield event("answer_delta", text=assistant_message[index : index + 3])
        yield event(
            "done",
            run_id=str(result.get("agent_run_id")),
            state_updates=result.get("state_updates") or {},
            tool_calls=result.get("tool_calls") or [],
        )

    async def _stream_chat_code_driven(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
        runtime_route: RuntimeRoute | None = None,
    ):
        started_at = datetime.utcnow()
        nodes: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        state_updates: dict[str, Any] = {}
        chunks: list[str] = []

        def event(event_type: str, **payload: Any) -> str:
            return json.dumps({"type": event_type, **payload}, ensure_ascii=False, default=str) + "\n"

        def step_summary(name: str, payload: dict[str, Any]) -> str:
            if name == "AgentTaskTimeline":
                steps = payload.get("steps") or []
                return f"规划执行时间线 {len(steps)} 步"
            if name == "ToolRegistry":
                tools = payload.get("tools") or []
                return f"注册 Agent 工具 {len(tools)} 个"
            if name == "AgentPlanner":
                steps = payload.get("steps") or []
                intent = payload.get("intent") or "general_chat"
                return f"Planner 识别意图 {intent}，本轮计划 {len(steps)} 个步骤"
            if name == "LLMPlanner":
                if payload.get("planner_fallback"):
                    return "LLM Planner 不可用，准备切换规则 planner"
                return f"LLM Planner 已输出工具计划，mode={payload.get('planner_mode') or 'llm'}"
            if name == "PlannerVerifier":
                plan = payload.get("verified_plan") or {}
                repairs = payload.get("repair_actions") or []
                return f"PlannerVerifier 完成校验，步骤 {len(plan.get('steps') or [])} 个，修复 {len(repairs)} 项"
            if name == "PlannerFallback":
                return f"PlannerFallback 已启用：{payload.get('reason') or 'unknown'}"
            if name == "ToolExecutor":
                extra = ""
                if payload.get("attempts", 1) and payload.get("attempts", 1) > 1:
                    extra += f"，重试 {payload.get('attempts')} 次"
                if payload.get("repaired"):
                    extra += "，已执行 schema repair"
                return f"执行工具 {payload.get('tool_name') or 'unknown'}: {payload.get('status') or 'unknown'}{extra}"
            if name == "TaskStep":
                return f"{payload.get('name') or '任务步骤'}: {payload.get('status') or 'unknown'}"
            if name == "PlanVerifier":
                return f"计划自检：{'通过' if payload.get('passed') else '需修复'}，问题 {payload.get('issue_count', 0)} 个"
            if name == "PlanRepair":
                return f"计划修复：{'已修复' if payload.get('repaired') else '无需修复'}"
            if name == "ResponseVerifier":
                return f"回复自检：{'通过' if payload.get('passed') else '需修复'}，问题 {payload.get('issue_count', 0)} 个"
            if name == "ResponseRepair":
                return f"回复修复：{'已补充' if payload.get('repaired') else '无需补充'}"
            if name == "ProfileExtractorAgent":
                patch = payload.get("profile_patch") or {}
                corrections = payload.get("corrections") or []
                if patch or corrections:
                    return f"档案更新 {len(patch)} 项，纠错 {len(corrections)} 项"
                return "未发现新的档案字段"
            if name == "MemoryAgent":
                return f"写入长期记忆 {len(payload.get('written') or [])} 条"
            if name == "MemoryVerifier":
                return (
                    f"记忆校验：通过 {payload.get('accepted_count', 0)} 条，"
                    f"拒绝 {payload.get('rejected_count', 0)} 条，问题 {payload.get('issue_count', 0)} 个"
                )
            if name == "IntentRouter":
                missing = payload.get("missing_slots") or []
                if missing:
                    return "建档未完成，缺少：" + ", ".join(missing)
                return "档案完整，可以进入计划/问答流程"
            if name == "ContextBuilder":
                return f"构建上下文包，intent={payload.get('intent') or 'unknown'}"
            if name == "KnowledgeRetrieval":
                return self._knowledge_debug_summary(payload)
            if name == "DecisionRules":
                return f"命中规则 {len(payload.get('matched_rule_ids') or [])} 条"
            if name == "TemplateSelector":
                return f"选择模板 {len(payload.get('matched_template_ids') or [])} 个"
            if name == "CoachLLM":
                if payload.get("mode") == "onboarding":
                    return "生成建档追问"
                if payload.get("safety"):
                    return "触发安全边界回复"
                return "生成教练回复"
            if name == "ResponsePersisted":
                return "回复、trace 和日志已保存"
            if name == "GuardrailCheck":
                action = payload.get("action", "pass")
                if action == "block":
                    return f"安全护栏拦截，{payload.get('flag_count', 0)} 条规则命中"
                if action == "warn":
                    return f"安全护栏警告，{payload.get('flag_count', 0)} 条规则触发"
                return "安全护栏检查通过"
            return "步骤完成"

        def step_event(name: str, node: dict[str, Any], payload: dict[str, Any]) -> str:
            return event(
                "step",
                name=name,
                status="completed",
                summary=step_summary(name, payload),
                latency_ms=node.get("latency_ms"),
                metadata=self._public_trace_metadata(name, payload),
            )

        def timeline_event(step: TaskStep) -> str:
            payload = timeline.step_event(step)
            node = run_logger.event("TaskStep", payload)
            nodes.append(node)
            return step_event("TaskStep", node, payload)

        def planned_or_new_step(key: str, name: str, tool_name: str, reason: str) -> TaskStep:
            return timeline_steps.get(key) or timeline.add_step(name, tool_name, reason)

        async def execute_tool(tool_name: str, input_json: dict[str, Any], timeline_step: TaskStep):
            emitted_events: list[str] = []
            execution = await executor.execute(tool_registry, timeline, timeline_step, input_json)
            step_started_node = run_logger.event("TaskStep", execution.started_event)
            nodes.append(step_started_node)
            emitted_events.append(step_event("TaskStep", step_started_node, execution.started_event))
            result = execution.result
            tool_payload = self._summarize_tool_execution(result.to_trace())
            tool_calls.append(
                {
                    "tool_name": result.tool_name,
                    "status": result.status,
                    "input": tool_payload.get("input_json", {}),
                    "output": tool_payload.get("output_json", {}),
                    "latency_ms": result.latency_ms,
                    "attempts": result.attempts,
                    "validation_errors": result.validation_errors,
                    "repaired": result.repaired,
                    "repair_actions": result.repair_actions,
                    "contract": result.contract,
                    "idempotency_key": result.idempotency_key,
                }
            )
            tool_node = run_logger.event("ToolExecutor", tool_payload)
            nodes.append(tool_node)
            emitted_events.append(step_event("ToolExecutor", tool_node, tool_payload))
            timeline_step.output_summary = tool_payload.get("output_json", {})
            completed_event = dict(execution.completed_event)
            completed_event["output_summary"] = tool_payload.get("output_json", {})
            step_completed_node = run_logger.event("TaskStep", completed_event)
            nodes.append(step_completed_node)
            emitted_events.append(step_event("TaskStep", step_completed_node, execution.completed_event))
            if result.status != "success":
                raise RuntimeError(result.error or f"Tool failed: {tool_name}")
            return result.output_json, emitted_events

        session = self.db.get(models.ConversationSession, session_id)
        if not session:
            yield event("error", message="Conversation session not found")
            return

        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)
        run_logger = AgentRunLogger("chat_stream", user.id, session.id)
        timeline = AgentTaskTimeline(message, request_id=run_logger.request_id)
        tool_registry = self._build_chat_tool_registry(user.id, session.id, profile, message)
        executor = AgentExecutor()
        execution_plan, planner_debug = await self._build_code_driven_execution_plan(
            message, tool_registry, profile, runtime_route
        )
        timeline_steps = {
            planned.key: timeline.add_step(planned.name, planned.tool_name, planned.reason)
            for planned in execution_plan.steps
        }
        registry_node = run_logger.event("ToolRegistry", {"tools": tool_registry.list_specs()})
        nodes.append(registry_node)
        yield step_event("ToolRegistry", registry_node, registry_node.get("output", {}))
        contract_issues = tool_registry.validate_contracts()
        contract_node = run_logger.event("ToolContractAudit", {"issues": contract_issues})
        nodes.append(contract_node)
        state_updates["tool_contract_issues"] = contract_issues
        yield step_event("ToolContractAudit", contract_node, contract_node.get("output", {}))
        llm_planner_node = run_logger.event("LLMPlanner", {
            "raw_output": planner_debug.get("llm_planner_raw"),
            "planner_mode": execution_plan.planner_mode,
            "planner_fallback": planner_debug.get("planner_fallback", False),
        })
        nodes.append(llm_planner_node)
        yield step_event("LLMPlanner", llm_planner_node, llm_planner_node.get("output", {}))
        if planner_debug.get("planner_fallback"):
            fallback_node = run_logger.event("PlannerFallback", {
                "planner_fallback": True,
                "reason": planner_debug.get("planner_fallback_reason"),
            })
            nodes.append(fallback_node)
            yield step_event("PlannerFallback", fallback_node, fallback_node.get("output", {}))
        verifier_node = run_logger.event("PlannerVerifier", {
            "verified_plan": planner_debug.get("planner_verified_plan"),
            "repair_actions": planner_debug.get("planner_repair_actions", []),
        })
        nodes.append(verifier_node)
        yield step_event("PlannerVerifier", verifier_node, verifier_node.get("output", {}))
        state_updates["planner"] = {
            "mode": execution_plan.planner_mode,
            "fallback": planner_debug.get("planner_fallback", False),
            "fallback_reason": planner_debug.get("planner_fallback_reason"),
            "repair_actions": planner_debug.get("planner_repair_actions", []),
        }
        planner_node = run_logger.event("AgentPlanner", execution_plan.to_dict())
        nodes.append(planner_node)
        yield step_event("AgentPlanner", planner_node, planner_node.get("output", {}))
        timeline_node = run_logger.event("AgentTaskTimeline", timeline.to_dict())
        nodes.append(timeline_node)
        yield step_event("AgentTaskTimeline", timeline_node, timeline_node.get("output", {}))
        request_event = run_logger.event(
            "RequestReceived",
            {
                "message_chars": len(message),
                "provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
                "embedding_mode": self.model_provider.embedding_mode(),
            },
        )
        nodes.append(request_event)
        yield event(
            "status",
            text="正在接收请求并准备 Agent 运行环境",
            provider=self.model_provider.settings.llm_provider,
            chat_model=self.model_provider.settings.chat_model,
            embedding_mode=self.model_provider.embedding_mode(),
        )
        yield step_event("RequestReceived", request_event, request_event.get("output", {}))
        if runtime_route is not None:
            route_payload = runtime_route.to_dict()
            route_node = run_logger.event("RuntimeRouter", route_payload)
            nodes.append(route_node)
            state_updates["agent_mode"] = runtime_route.mode
            state_updates["runtime_route"] = route_payload
            yield step_event("RuntimeRouter", route_node, route_payload)

        self._save_message(session.id, user.id, "user", message)

        tool_steps = {
            planned.tool_name: timeline_steps[planned.key]
            for planned in execution_plan.steps
            if planned.tool_name and planned.key in timeline_steps
        }
        execution_order = [planned.tool_name for planned in execution_plan.steps if planned.tool_name]
        dispatch_node = run_logger.event("AgentToolOrderDispatch", {
            "tool_order": execution_order,
            "planner_mode": execution_plan.planner_mode,
        })
        nodes.append(dispatch_node)
        yield step_event("AgentToolOrderDispatch", dispatch_node, dispatch_node.get("output", {}))

        extraction: dict[str, Any] = {"profile_patch": {}, "corrections": [], "ignored_candidates": []}
        memory_verify_output: dict[str, Any] = {
            "passed": True,
            "accepted_candidates": [],
            "accepted_corrections": [],
            "rejected_candidates": [],
        }
        memories_written: list[str] = []
        missing_slots = self.missing_onboarding_slots(profile)
        onboarding_complete = not missing_slots
        context_packet: dict[str, Any] = {}
        plan_decision: dict[str, Any] = {}
        plan_output: dict[str, Any] = {}
        plan_verify_output: dict[str, Any] = {}
        assistant_message = ""
        guardrail_payload: dict[str, Any] = {"action": "pass", "flag_count": 0, "flags": []}
        persisted = False

        try:
            for tool_name in execution_order:
                step = tool_steps.get(tool_name)
                if step is None:
                    continue
                if tool_name == "profile.extract":
                    node_start = time.perf_counter()
                    yield event("status", text="正在抽取档案字段和纠错信息")
                    extraction, emitted = await execute_tool(tool_name, {"message_chars": len(message)}, step)
                    for item in emitted:
                        yield item
                    if extraction.get("profile_patch") or extraction.get("corrections"):
                        self._apply_profile_extraction(profile, extraction)
                        self._refresh_macro_targets(profile)
                        state_updates["profile_updates"] = extraction.get("profile_patch", {})
                        state_updates["corrections"] = extraction.get("corrections", [])
                    node = run_logger.node("ProfileExtractorAgent", node_start, extraction, {"message": message[:240]})
                    nodes.append(node)
                    yield step_event("ProfileExtractorAgent", node, extraction)
                elif tool_name == "memory.verify":
                    yield event("status", text="正在校验长期记忆候选")
                    memory_verify_output, emitted = await execute_tool(tool_name, {"extraction": extraction}, step)
                    for item in emitted:
                        yield item
                    state_updates["memory_verification"] = memory_verify_output
                    memory_verify_node = run_logger.event("MemoryVerifier", memory_verify_output)
                    nodes.append(memory_verify_node)
                    yield step_event("MemoryVerifier", memory_verify_node, memory_verify_output)
                elif tool_name == "memory.write":
                    node_start = time.perf_counter()
                    yield event("status", text="正在写入已校验的长期记忆")
                    memory_output, emitted = await execute_tool(
                        tool_name,
                        {"extraction": extraction, "verification": memory_verify_output},
                        step,
                    )
                    for item in emitted:
                        yield item
                    memories_written = memory_output.get("written") or []
                    memory_payload = {"written": memories_written}
                    node = run_logger.node("MemoryAgent", node_start, memory_payload)
                    nodes.append(node)
                    yield step_event("MemoryAgent", node, memory_payload)
                    missing_slots = self.missing_onboarding_slots(profile)
                    onboarding_complete = not missing_slots
                    intent_payload = {"onboarding_complete": onboarding_complete, "missing_slots": missing_slots}
                    intent_node = run_logger.event("IntentRouter", intent_payload)
                    nodes.append(intent_node)
                    yield step_event("IntentRouter", intent_node, intent_payload)
                elif tool_name == "context.build":
                    if not onboarding_complete or self._requires_immediate_safety_reply(message):
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "onboarding_or_static_safety"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    node_start = time.perf_counter()
                    yield event("status", text="正在按 Planner 顺序构建上下文包")
                    context_packet, emitted = await execute_tool(tool_name, {"message_chars": len(message)}, step)
                    for item in emitted:
                        yield item
                    node = run_logger.node("ContextBuilder", node_start, context_packet)
                    nodes.append(node)
                    yield step_event("ContextBuilder", node, context_packet)
                    knowledge_context = context_packet.get("knowledge_context") or {}
                    knowledge_payload = knowledge_context.get("debug", {})
                    knowledge_node = run_logger.event("KnowledgeRetrieval", knowledge_payload)
                    nodes.append(knowledge_node)
                    yield step_event("KnowledgeRetrieval", knowledge_node, knowledge_payload)
                    rules_payload = {
                        "matched_rule_ids": knowledge_context.get("debug", {}).get("matched_rule_ids", []),
                        "rules": knowledge_context.get("decision_rules", []),
                    }
                    rules_node = run_logger.event("DecisionRules", rules_payload)
                    nodes.append(rules_node)
                    yield step_event("DecisionRules", rules_node, rules_payload)
                    template_payload = {
                        "matched_template_ids": knowledge_context.get("debug", {}).get("matched_template_ids", []),
                        "templates": knowledge_context.get("plan_templates", []),
                    }
                    template_node = run_logger.event("TemplateSelector", template_payload)
                    nodes.append(template_node)
                    yield step_event("TemplateSelector", template_node, template_payload)
                    state_updates["context_intent"] = context_packet.get("intent")
                    state_updates["knowledge_debug"] = knowledge_context.get("debug", {})
                elif tool_name == "plan.decide":
                    if not context_packet:
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "context_not_available"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    plan_decision, emitted = await execute_tool(tool_name, {"context_packet": context_packet}, step)
                    for item in emitted:
                        yield item
                    policy_payload = context_packet.get("current_request_policy", {})
                    policy_node = run_logger.event("CurrentRequestPolicy", policy_payload)
                    nodes.append(policy_node)
                    yield step_event("CurrentRequestPolicy", policy_node, policy_payload)
                    plan_node = run_logger.event("PlanGenerationDecision", plan_decision)
                    nodes.append(plan_node)
                    yield step_event("PlanGenerationDecision", plan_node, plan_decision)
                elif tool_name == "plan.generate":
                    active_plan = self.get_active_plan(user.id)
                    if active_plan is not None or not bool(plan_decision.get("should_generate_plan")):
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "active_plan_exists_or_generation_not_allowed"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    yield event("status", text="当前消息明确请求计划，正在生成训练计划")
                    plan_output, emitted = await execute_tool(tool_name, {"reason": plan_decision.get("reason")}, step)
                    for item in emitted:
                        yield item
                    context_packet["active_plan"] = plan_output.get("active_plan")
                    state_updates["generated_plan_id"] = plan_output.get("plan_id")
                    if self._build_plan_reflection_prompt(plan_output, context_packet, profile):
                        plan_output["_reflection"] = "Requested"
                        reflection_node = run_logger.event("PlanSelfCorrection", {
                            "step": "reflection_requested",
                            "plan_id": plan_output.get("plan_id"),
                        })
                        nodes.append(reflection_node)
                        yield step_event("PlanSelfCorrection", reflection_node, reflection_node.get("output", {}))
                elif tool_name == "plan.verify":
                    if not plan_output:
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "plan_not_generated"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    plan_verify_output, emitted = await execute_tool(
                        tool_name,
                        {"plan_payload": plan_output.get("active_plan") or {}, "context_packet": context_packet},
                        step,
                    )
                    for item in emitted:
                        yield item
                    state_updates["plan_verification"] = plan_verify_output
                    verify_node = run_logger.event("PlanVerifier", plan_verify_output)
                    nodes.append(verify_node)
                    yield step_event("PlanVerifier", verify_node, plan_verify_output)
                elif tool_name == "plan.repair":
                    if not plan_verify_output.get("repair_actions"):
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "plan_repair_not_required"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    plan_repair_output, emitted = await execute_tool(
                        tool_name,
                        {
                            "plan_id": plan_output.get("plan_id"),
                            "plan_payload": plan_output.get("active_plan") or {},
                            "verification": plan_verify_output,
                            "context_packet": context_packet,
                        },
                        step,
                    )
                    for item in emitted:
                        yield item
                    if plan_repair_output.get("active_plan"):
                        context_packet["active_plan"] = plan_repair_output.get("active_plan")
                    state_updates["plan_repair"] = plan_repair_output
                    repair_node = run_logger.event("PlanRepair", plan_repair_output)
                    nodes.append(repair_node)
                    yield step_event("PlanRepair", repair_node, plan_repair_output)
                elif tool_name == "coach.reply":
                    node_start = time.perf_counter()
                    yield event("status", text="正在按 Planner 顺序生成最终教练回复")
                    timeline.start(step)
                    yield timeline_event(step)
                    if self._requires_immediate_safety_reply(message):
                        async for chunk in self._stream_static_text(self._safety_reply()):
                            chunks.append(chunk)
                            yield event("answer_delta", text=chunk)
                        coach_payload = {"safety": True, "mode": "static_safety"}
                    elif not onboarding_complete:
                        async for chunk in self._live_onboarding_reply_stream(profile, missing_slots, message):
                            chunks.append(chunk)
                            yield event("answer_delta", text=chunk)
                        coach_payload = {
                            "mode": "onboarding",
                            "live_model": self.model_provider.has_live_model(),
                            "missing_slots": missing_slots,
                        }
                    else:
                        async for chunk in self._coaching_reply_stream(user.id, message, context_packet):
                            chunks.append(chunk)
                            yield event("answer_delta", text=chunk)
                        coach_payload = {
                            "safety": False,
                            "live_model": self.model_provider.has_live_model(),
                            "response_chars": len("".join(chunks)),
                        }
                    assistant_message = "".join(chunks).strip()
                    timeline.complete(step, {"response_chars": len(assistant_message), **coach_payload}, round((time.perf_counter() - node_start) * 1000))
                    yield timeline_event(step)
                    node = run_logger.node("CoachLLM", node_start, coach_payload)
                    nodes.append(node)
                    yield step_event("CoachLLM", node, coach_payload)
                elif tool_name == "response.verify":
                    assistant_message = "".join(chunks).strip() or registry.get("error_coach_stream_empty")
                    if not context_packet:
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "context_not_available"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    response_verify_output, emitted = await execute_tool(
                        tool_name,
                        {"assistant_message": assistant_message[:6000], "context_packet": context_packet},
                        step,
                    )
                    for item in emitted:
                        yield item
                    state_updates["response_verification"] = response_verify_output
                    response_verify_node = run_logger.event("ResponseVerifier", response_verify_output)
                    nodes.append(response_verify_node)
                    yield step_event("ResponseVerifier", response_verify_node, response_verify_output)
                elif tool_name == "response.repair":
                    response_verification = state_updates.get("response_verification") or {}
                    if not response_verification.get("repair_actions"):
                        skip_node = run_logger.event("ToolSkipped", {"tool_name": tool_name, "reason": "response_repair_not_required"})
                        nodes.append(skip_node)
                        yield step_event("ToolSkipped", skip_node, skip_node.get("output", {}))
                        continue
                    response_repair_output, emitted = await execute_tool(
                        tool_name,
                        {"verification": response_verification, "context_packet": context_packet},
                        step,
                    )
                    for item in emitted:
                        yield item
                    repair_text = response_repair_output.get("repair_text") or ""
                    if repair_text:
                        assistant_message = (assistant_message + repair_text).strip()
                        chunks = [assistant_message]
                        yield event("answer_delta", text=repair_text)
                    state_updates["response_repair"] = response_repair_output
                    response_repair_node = run_logger.event("ResponseRepair", response_repair_output)
                    nodes.append(response_repair_node)
                    yield step_event("ResponseRepair", response_repair_node, response_repair_output)
                elif tool_name == "guardrail.check":
                    assistant_message = "".join(chunks).strip() or assistant_message or registry.get("error_coach_stream_empty")
                    guardrail_output, emitted = await execute_tool(tool_name, {"assistant_message": assistant_message[:4000]}, step)
                    for item in emitted:
                        yield item
                    guardrail_payload = {
                        "action": guardrail_output.get("action"),
                        "flag_count": guardrail_output.get("flag_count", 0),
                        "flags": guardrail_output.get("flags", []),
                    }
                    guardrail_node = run_logger.event("GuardrailCheck", guardrail_payload)
                    nodes.append(guardrail_node)
                    yield step_event("GuardrailCheck", guardrail_node, guardrail_payload)
                    if guardrail_output.get("action") == GuardrailSeverity.BLOCK.value:
                        assistant_message = guardrail_output.get("replacement") or assistant_message
                        chunks = [assistant_message]
                        yield event("guardrail_block", replacement=assistant_message[:200])
                elif tool_name == "response.persist":
                    assistant_message = "".join(chunks).strip() or assistant_message or registry.get("error_coach_stream_empty")
                    _, emitted = await execute_tool(tool_name, {"assistant_message": assistant_message}, step)
                    for item in emitted:
                        yield item
                    self._save_message(session.id, user.id, "assistant", assistant_message)
                    response_payload = {"response_chars": len(assistant_message)}
                    response_node = run_logger.event("ResponsePersisted", response_payload)
                    nodes.append(response_node)
                    yield step_event("ResponsePersisted", response_node, response_payload)
                    persisted = True
        except Exception as exc:
            error_text = f"\n\n{self._model_call_error_message(exc)} 请稍后重试。"
            chunks.append(error_text)
            nodes.append(run_logger.event("RuntimeError", {"error": str(exc)}))
            yield event("error", message=str(exc), summary="Agent 运行时发生错误")
            yield event("answer_delta", text=error_text)

        assistant_message = "".join(chunks).strip() or assistant_message or registry.get("error_coach_stream_empty")
        if not persisted:
            self._save_message(session.id, user.id, "assistant", assistant_message)
            response_payload = {"response_chars": len(assistant_message), "fallback_persist": True}
            response_node = run_logger.event("ResponsePersisted", response_payload)
            nodes.append(response_node)
            yield step_event("ResponsePersisted", response_node, response_payload)

        run = models.AgentRun(
            user_id=user.id,
            session_id=session.id,
            run_type="chat_stream",
            status="completed",
            nodes=nodes,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            summary=assistant_message[:500],
        )
        self.db.add(run)
        self.db.flush()
        for call in tool_calls:
            self.db.add(
                models.ToolCall(
                    agent_run_id=run.id,
                    tool_name=call["tool_name"],
                    input_json=call.get("input", {}),
                    output_json=call.get("output", {}),
                    latency_ms=call.get("latency_ms", 0),
                    status=call.get("status", "success"),
                )
            )
        self.db.commit()
        run.log_path = run_logger.write_run_log(run.id, "completed", assistant_message[:500])
        self.db.commit()
        yield event(
            "done",
            run_id=str(run.id),
            log_path=run.log_path,
            state_updates=state_updates,
            tool_calls=tool_calls,
        )

    def record_daily_checkin(self, request: DailyCheckinRequest) -> dict[str, Any]:
        user = self.ensure_user(request.user_id)
        checkin_date = request.checkin_date or date.today()
        existing = self.db.scalar(
            select(models.DailyCheckin).where(
                models.DailyCheckin.user_id == user.id,
                models.DailyCheckin.checkin_date == checkin_date,
            )
        )
        checkin = existing or models.DailyCheckin(user_id=user.id, checkin_date=checkin_date)
        for key, value in request.model_dump(exclude={"user_id", "checkin_date"}).items():
            setattr(checkin, key, value)
        self.db.add(checkin)
        recovery_log = self.db.scalar(
            select(models.RecoveryLog).where(
                models.RecoveryLog.user_id == user.id,
                models.RecoveryLog.log_date == checkin_date,
            )
        )
        if recovery_log is None:
            recovery_log = models.RecoveryLog(user_id=user.id, log_date=checkin_date)
            self.db.add(recovery_log)
        recovery_log.sleep_hours = request.sleep_hours
        recovery_log.fatigue_score = request.fatigue
        recovery_log.soreness_score = request.soreness
        recovery_log.stress_score = request.stress
        recovery_log.notes = request.notes

        memory_content = self._checkin_memory(checkin)
        if memory_content:
            self._write_memory(user.id, "recent_state", memory_content, "daily_checkin", 0.7)

        auto_adjusted = False
        if self._should_adjust_from_checkin(checkin):
            self.db.flush()
            self.adjust_plan(PlanAdjustRequest(user_id=user.id, reason="daily check-in signals"))
            auto_adjusted = True

        task_update = AgentTaskStateService(self.db).update_from_checkin(user.id, checkin, auto_adjusted)
        self.db.commit()
        return {
            "checkin_id": str(checkin.id),
            "auto_adjusted": auto_adjusted,
            "long_term_task": task_update,
        }

    def record_workout_log(self, request: WorkoutLogRequest) -> models.WorkoutLog:
        user = self.ensure_user(request.user_id)
        log = models.WorkoutLog(
            user_id=user.id,
            performed_at=request.performed_at or datetime.utcnow(),
            workout_name=request.workout_name,
            exercises=request.exercises,
            duration_minutes=request.duration_minutes,
            rpe=request.rpe,
            completion_rate=request.completion_rate,
            notes=request.notes,
        )
        self.db.add(log)
        self.db.flush()
        session = models.WorkoutSession(
            user_id=user.id,
            session_date=(request.performed_at or datetime.utcnow()).date(),
            session_name=request.workout_name,
            started_at=request.performed_at,
            completion_score=request.completion_rate,
            fatigue_score=request.rpe,
            notes=request.notes,
        )
        self.db.add(session)
        self.db.flush()
        for exercise in request.exercises:
            self._write_exercise_logs_from_payload(user.id, session.id, exercise)
        self._write_memory(
            user.id,
            "training_performance",
            f"Completed {request.workout_name}; RPE={request.rpe}; notes={request.notes or ''}",
            "workout_log",
            0.65,
        )
        self.db.commit()
        self.db.refresh(log)
        return log

    def _write_exercise_logs_from_payload(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        exercise: dict[str, Any],
    ) -> None:
        name = str(exercise.get("name") or exercise.get("exercise_name") or "unknown_exercise")
        sets = exercise.get("sets")
        if isinstance(sets, list) and sets:
            for index, set_item in enumerate(sets, start=1):
                self.db.add(
                    models.ExerciseLog(
                        user_id=user_id,
                        session_id=session_id,
                        exercise_name=name,
                        set_index=int(set_item.get("set_index") or index),
                        reps=set_item.get("reps"),
                        weight=set_item.get("weight") or set_item.get("weight_kg"),
                        rpe=set_item.get("rpe"),
                        completed=bool(set_item.get("completed", True)),
                        pain_score=set_item.get("pain_score"),
                        pain_location=set_item.get("pain_location"),
                        notes=set_item.get("notes"),
                    )
                )
            return
        target_sets = int(exercise.get("sets") or exercise.get("target_sets") or 1)
        for index in range(1, max(target_sets, 1) + 1):
            self.db.add(
                models.ExerciseLog(
                    user_id=user_id,
                    session_id=session_id,
                    exercise_name=name,
                    set_index=index,
                    reps=exercise.get("reps"),
                    weight=exercise.get("weight") or exercise.get("weight_kg"),
                    rpe=exercise.get("rpe"),
                    completed=bool(exercise.get("completed", True)),
                    pain_score=exercise.get("pain_score"),
                    pain_location=exercise.get("pain_location"),
                    notes=exercise.get("notes"),
                )
            )

    def generate_plan(self, request: PlanGenerateRequest) -> models.TrainingPlan:
        user = self.ensure_user(request.user_id)
        profile = self._get_or_create_profile(user.id)
        self._refresh_macro_targets(profile)

        if request.force:
            for plan in self._active_plans(user.id):
                plan.status = "archived"

        plan_json = self._build_plan_json(profile, request.plan_days)
        rationale = self._plan_rationale(profile)
        plan = models.TrainingPlan(
            user_id=user.id,
            status="active",
            week_start=date.today(),
            plan_json=plan_json,
            rationale=rationale,
        )
        self.db.add(plan)
        self._write_memory(user.id, "plan_preference", rationale, "plan_generation", 0.6)
        DecisionLogger(self.db).log_decision(
            user.id,
            {
                "decision_type": "plan_generation",
                "input_summary": f"Generate {request.plan_days}-day plan",
                "context_used": {"profile": self._profile_payload(profile)},
                "decision_result": "created_active_training_plan",
                "reason": rationale,
                "confidence_score": 0.75,
            },
        )
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def adjust_plan(self, request: PlanAdjustRequest) -> models.TrainingPlan:
        user = self.ensure_user(request.user_id)
        profile = self._get_or_create_profile(user.id)
        latest_checkin = self.latest_checkin(user.id)
        active_plan = self.get_active_plan(user.id)
        if active_plan:
            active_plan.status = "archived"

        multiplier, reasons = adjustment_multiplier(
            latest_checkin.fatigue if latest_checkin else None,
            latest_checkin.soreness if latest_checkin else None,
            latest_checkin.sleep_hours if latest_checkin else None,
            latest_checkin.workout_completion if latest_checkin else None,
        )
        plan_json = self._build_plan_json(profile, 7, volume_multiplier=multiplier)
        reason_text = request.reason or ", ".join(reasons) or "routine plan refresh"
        rationale = (
            f"Adjusted plan with volume multiplier {multiplier:.2f} because {reason_text}."
        )
        plan = models.TrainingPlan(
            user_id=user.id,
            status="active",
            week_start=date.today(),
            plan_json=plan_json,
            rationale=rationale,
        )
        self.db.add(plan)
        self._write_memory(user.id, "adjustment", rationale, "plan_adjustment", 0.8)
        DecisionLogger(self.db).log_decision(
            user.id,
            {
                "decision_type": "plan_adjustment",
                "input_summary": reason_text,
                "context_used": {
                    "latest_checkin": self._model_dict(latest_checkin) if latest_checkin else None,
                    "volume_multiplier": multiplier,
                },
                "decision_result": "created_adjusted_active_training_plan",
                "reason": rationale,
                "confidence_score": 0.78,
            },
        )
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def dashboard(self, user_id: uuid.UUID) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)
        active_plan = self.get_active_plan(user.id)
        latest_checkin = self.latest_checkin(user.id)
        memories = self.recent_memories(user.id, limit=5)
        active_tasks = AgentTaskStateService(self.db).list_active(user.id, limit=6)
        workout_count = self.db.scalar(
            select(func.count(models.WorkoutLog.id)).where(models.WorkoutLog.user_id == user.id)
        )

        today_plan = {}
        if active_plan:
            weekday = min(datetime.utcnow().weekday(), len(active_plan.plan_json.get("training_days", [])) - 1)
            if weekday >= 0:
                today_plan = active_plan.plan_json.get("training_days", [])[weekday]

        missing_slots = self.missing_onboarding_slots(profile)
        return {
            "user_id": user.id,
            "profile_complete": not missing_slots,
            "profile": self._profile_payload(profile),
            "missing_slots": missing_slots,
            "today_plan": today_plan,
            "latest_checkin": self._model_dict(latest_checkin) if latest_checkin else None,
            "recent_memories": [self._model_dict(memory) for memory in memories],
            "active_tasks": active_tasks,
            "progress": {
                "workouts_logged": workout_count or 0,
                "active_plan": bool(active_plan),
                "target_calories": profile.target_calories,
            },
            "coach_suggestions": self._suggestions(profile, latest_checkin),
        }

    def agent_run(self, run_id: uuid.UUID) -> dict[str, Any]:
        run = self.db.get(models.AgentRun, run_id)
        if not run:
            raise ValueError("Agent run not found")
        calls = self.db.scalars(
            select(models.ToolCall).where(models.ToolCall.agent_run_id == run.id)
        ).all()
        return {
            "id": run.id,
            "user_id": run.user_id,
            "session_id": run.session_id,
            "run_type": run.run_type,
            "status": run.status,
            "nodes": run.nodes,
            "summary": run.summary,
            "error": run.error,
            "log_path": run.log_path,
            "tool_calls": [self._tool_call_payload(call) for call in calls],
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }

    def _tool_call_payload(self, call: models.ToolCall) -> dict[str, Any]:
        return {
            "id": str(call.id),
            "agent_run_id": str(call.agent_run_id),
            "tool_name": call.tool_name,
            "input_json": call.input_json or {},
            "output_json": call.output_json or {},
            "latency_ms": call.latency_ms,
            "status": call.status,
            "created_at": call.created_at.isoformat() if call.created_at else None,
            "updated_at": call.updated_at.isoformat() if call.updated_at else None,
        }

    def _model_dict(self, model: Any) -> dict[str, Any]:
        if model is None:
            return {}
        payload: dict[str, Any] = {}
        for column in model.__table__.columns:
            value = getattr(model, column.name)
            if isinstance(value, uuid.UUID):
                value = str(value)
            elif isinstance(value, (datetime, date)):
                value = value.isoformat()
            payload[column.name] = value
        return payload

    def _get_or_create_profile(self, user_id: uuid.UUID) -> models.UserProfile:
        profile = self.db.get(models.UserProfile, user_id)
        if profile:
            return profile
        profile = models.UserProfile(user_id=user_id)
        self.db.add(profile)
        self.db.flush()
        return profile

    def _apply_profile_payload(self, profile: models.UserProfile, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if value is None or not hasattr(profile, key):
                continue
            if isinstance(value, list) and not value:
                continue
            setattr(profile, key, value)

    def _apply_profile_extraction(
        self,
        profile: models.UserProfile,
        extraction: dict[str, Any],
    ) -> None:
        patch = extraction.get("profile_patch", {})
        for key, value in patch.items():
            if value is None or not hasattr(profile, key):
                continue
            value = self._normalize_profile_patch_value(key, value)
            if value is None:
                continue
            if isinstance(value, list):
                if not value:
                    continue
                current = getattr(profile, key) or []
                setattr(profile, key, sorted(set(current + value)))
            else:
                setattr(profile, key, value)

        for correction in extraction.get("corrections", []):
            if correction.get("field") != "injuries":
                continue
            if correction.get("action") == "clear":
                profile.injuries = []
                continue
            if correction.get("action") == "remove":
                value = correction.get("value")
                profile.injuries = [item for item in (profile.injuries or []) if item != value]

    def _normalize_profile_patch_value(
        self,
        key: str,
        value: Any,
        source_text: str | None = None,
    ) -> Any:
        if value is None:
            return None
        if key == "goal":
            lowered = str(value).strip().lower()
            if any(token in lowered for token in ["fat_loss", "fat loss", "weight loss", "cut", "lose fat"]):
                return "fat_loss"
            if any(token in lowered for token in ["muscle_gain", "muscle gain", "bulk", "hypertrophy"]):
                return "muscle_gain"
            if any(token in lowered for token in ["maintenance", "maintain"]):
                return "maintenance"
            if any(token in str(value) for token in ["减脂", "降脂", "瘦", "腹肌"]):
                return "fat_loss"
            if any(token in str(value) for token in ["增肌", "长肌肉"]):
                return "muscle_gain"
            return None
        if key == "sex":
            lowered = str(value).strip().lower()
            if lowered in {"male", "man", "m", "男", "男性"}:
                return "male"
            if lowered in {"female", "woman", "f", "女", "女性"}:
                return "female"
            return None
        if key == "experience_level":
            lowered = str(value).strip().lower()
            if any(token in lowered for token in ["beginner", "novice", "new"]):
                return "beginner"
            if any(token in lowered for token in ["intermediate", "regular", "1 year", "one year"]):
                return "intermediate"
            if any(token in lowered for token in ["advanced", "expert"]):
                return "advanced"
            if any(token in str(value) for token in ["新手", "零基础"]):
                return "beginner"
            if any(token in str(value) for token in ["中级", "一年", "1年", "系统训练"]):
                return "intermediate"
            if any(token in str(value) for token in ["高级", "多年"]):
                return "advanced"
            return None
        if key in {"age", "workout_frequency", "workout_duration"}:
            number = self._coerce_int(value)
            if number is None:
                return None
            if key == "age":
                return number if 8 <= number <= 90 else None
            if key == "workout_frequency":
                text_value = str(value).lower()
                if "month" in text_value or "per month" in text_value:
                    return None
                return number if 1 <= number <= 7 else None
            return number if 5 <= number <= 300 else None
        if key in {"height_cm", "weight_kg"}:
            number = self._coerce_float(value)
            if number is None:
                return None
            if key == "height_cm":
                # Auto-convert meter input (e.g. "1.75" → 175 cm)
                if 0.5 < number < 3.5:
                    number = round(number * 100)
                return number if 90 <= number <= 240 else None
            if not self._source_text_supports_body_weight(source_text, number):
                return None
            return number if 25 <= number <= 300 else None
        if key in {"dietary_preferences", "equipment_available", "injuries"}:
            items = value if isinstance(value, list) else [value]
            normalized = []
            for item in items:
                if item in (None, ""):
                    continue
                text = str(item).strip().lower()
                if not text:
                    continue
                normalized.append(text.replace(" ", "_"))
            return sorted(set(normalized))
        return value

    def _coerce_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        match = re.search(r"\d+", str(value))
        return int(match.group(0)) if match else None

    def _coerce_float(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None

    def _refresh_macro_targets(self, profile: models.UserProfile) -> None:
        macros = calculate_macro_targets(
            profile.age,
            profile.weight_kg,
            profile.height_cm,
            profile.activity_level,
            profile.goal,
            profile.sex,
        )
        profile.target_calories = macros.calories
        profile.target_protein_g = macros.protein_g
        profile.target_carbs_g = macros.carbs_g
        profile.target_fat_g = macros.fat_g

    def missing_onboarding_slots(self, profile: models.UserProfile) -> list[str]:
        missing = []
        for slot in REQUIRED_ONBOARDING_SLOTS:
            value = getattr(profile, slot)
            if value is None or value == [] or value == "":
                missing.append(slot)
        return missing

    def _memory_candidates_from_message(
        self,
        message: str,
        extraction: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        lowered = message.lower()
        candidates: list[dict[str, Any]] = []
        extraction = extraction or self._rule_profile_extraction(message)

        medical_keywords = {
            "hyperthyroidism": ["甲亢", "甲状腺", "hyperthyroidism", "thyroid"],
            "asthma": ["哮喘", "asthma"],
            "hypertension": ["高血压", "hypertension", "blood pressure"],
            "diabetes": ["糖尿病", "diabetes", "血糖"],
            "heart_condition": ["心脏", "心率异常", "心悸", "chest pain", "palpitation"],
        }
        matched_conditions = [
            condition
            for condition, keywords in medical_keywords.items()
            if any(keyword in lowered for keyword in keywords)
        ]
        medication_terms = ["吃药", "服药", "用药", "药物", "medication", "medicine"]
        has_medication = any(term in lowered for term in medication_terms)
        medications = []
        if any(term in lowered for term in ["赛治", "甲巯咪唑", "methimazole"]):
            medications.append("methimazole")
        lab_markers = [marker for marker in ["TSH", "FT3", "FT4", "TPOAb", "TgAb"] if marker.lower() in lowered]
        if matched_conditions or has_medication:
            candidates.append(
                {
                    "memory_type": "medical_context",
                    "content": f"用户提到健康或用药背景：{message}",
                    "importance": 0.95,
                    "confidence": 0.9,
                    "memory_metadata": {
                        "conditions": matched_conditions,
                        "medication_mentioned": has_medication,
                        "medications": medications,
                        "lab_markers": lab_markers,
                        "safety_level": "high",
                        "requires_medical_boundary": True,
                    },
                }
            )

        if any(token in lowered for token in ["睡", "疲劳", "酸痛", "recovery", "tired"]):
            candidates.append(
                {
                    "memory_type": "recent_state",
                    "content": f"用户近期状态：{message}",
                    "importance": 0.7,
                    "confidence": 0.8,
                    "memory_metadata": {"category": "recovery_state"},
                }
            )

        training_terms = ["卧推", "引体向上", "推肩", "深蹲", "硬拉", "bench", "pull-up", "squat", "deadlift"]
        if any(token in lowered for token in training_terms):
            candidates.append(
                {
                    "memory_type": "training_performance",
                    "content": f"用户训练表现或力量水平：{message}",
                    "importance": 0.72,
                    "confidence": 0.8,
                    "memory_metadata": {"category": "strength_baseline"},
                }
            )

        nutrition_terms = ["不吃", "过敏", "乳糖", "素食", "低碳", "高蛋白", "外卖", "不自己做饭", "食堂", "外食", "allergy"]
        if any(token in lowered for token in nutrition_terms):
            candidates.append(
                {
                    "memory_type": "nutrition_habit",
                    "content": f"用户饮食习惯或限制：{message}",
                    "importance": 0.75,
                    "confidence": 0.8,
                    "memory_metadata": {"category": "nutrition"},
                }
            )

        preference_terms = ["不喜欢", "喜欢", "偏好", "习惯", "讨厌", "prefer", "dislike"]
        if any(token in lowered for token in preference_terms):
            candidates.append(
                {
                    "memory_type": "stable_preference",
                    "content": f"用户稳定偏好：{message}",
                    "importance": 0.68,
                    "confidence": 0.78,
                    "memory_metadata": {"category": "preference"},
                }
            )

        return candidates
    def _write_memory(
        self,
        user_id: uuid.UUID,
        memory_type: str,
        content: str,
        source: str,
        importance: float,
        memory_metadata: dict[str, Any] | None = None,
        confidence: float = 0.75,
        status: str = "active",
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> uuid.UUID:
        manager = MemoryManager(self.db, self.model_provider)
        category = (memory_metadata or {}).get("category") or manager._category_from_type(memory_type)
        summary = " ".join(content.split())
        if len(summary) > 180:
            summary = summary[:177].rstrip() + "..."
        memory = models.LongTermMemory(
            user_id=user_id,
            memory_type=memory_type,
            category=category,
            content=content,
            summary=summary,
            source=source,
            importance=importance,
            recency_score=1.0,
            memory_metadata=memory_metadata or {},
            confidence=confidence,
            status=status,
            valid_from=valid_from,
            valid_until=valid_until,
            embedding=self.model_provider.embed_text(content),
        )
        self.db.add(memory)
        self.db.flush()
        self._write_risk_note_from_memory(user_id, memory)
        manager.update_memory_catalog(user_id, category)
        manager.update_memory_blocks(user_id)
        return memory.id

    def _write_risk_note_from_memory(
        self,
        user_id: uuid.UUID,
        memory: models.LongTermMemory,
    ) -> None:
        if memory.memory_type not in {"medical_context", "risk_signal"}:
            return
        metadata = memory.memory_metadata or {}
        injuries = metadata.get("injuries") or []
        body_part = injuries[0] if injuries else None
        risk_type = "medical_context" if memory.memory_type == "medical_context" else "training_risk"
        existing = self.db.scalar(
            select(models.RiskNote).where(
                models.RiskNote.user_id == user_id,
                models.RiskNote.risk_type == risk_type,
                models.RiskNote.body_part == body_part,
                models.RiskNote.status.in_(["active", "monitoring"]),
            )
        )
        if existing:
            existing.description = memory.summary or memory.content
            existing.last_seen_at = datetime.utcnow()
            existing.severity_score = max(existing.severity_score or 0.5, 0.85 if risk_type == "medical_context" else 0.75)
            existing.confidence_score = max(existing.confidence_score or 0.75, memory.confidence or 0.75)
            return
        self.db.add(
            models.RiskNote(
                user_id=user_id,
                body_part=body_part,
                risk_type=risk_type,
                description=memory.summary or memory.content,
                severity_score=0.85 if risk_type == "medical_context" else 0.75,
                confidence_score=memory.confidence or 0.75,
                status="active",
            )
        )
        self.db.flush()

    def get_active_plan(self, user_id: uuid.UUID) -> models.TrainingPlan | None:
        return self.db.scalar(
            select(models.TrainingPlan)
            .where(models.TrainingPlan.user_id == user_id, models.TrainingPlan.status == "active")
            .order_by(desc(models.TrainingPlan.created_at))
        )

    def _repair_profile_extract_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = dict(payload.get("output_json") or {})
        if payload.get("phase") == "input_validation":
            return {"input_json": {"message_chars": len(str(payload.get("input_json") or ""))}}
        output.setdefault("profile_patch", {})
        output.setdefault("corrections", [])
        output.setdefault("ignored_candidates", [])
        output.setdefault("model_used", False)
        return {"output_json": output}

    def _repair_memory_verify_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = dict(payload.get("output_json") or {})
        output.setdefault("passed", False)
        output.setdefault("accepted_candidates", [])
        output.setdefault("accepted_corrections", [])
        output.setdefault("rejected_candidates", [])
        output.setdefault("issues", [])
        output.setdefault("repair_actions", ["schema_repair_memory_verify"])
        return {"output_json": output}

    def _repair_context_build_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = dict(payload.get("output_json") or {})
        output.setdefault("intent", "general_chat")
        output.setdefault(
            "current_request_policy",
            {
                "active_instruction_source": "current_user_message",
                "history_is_background_only": True,
                "allow_plan_generation": False,
            },
        )
        output.setdefault("knowledge_context", {})
        return {"output_json": output}

    def _repair_plan_decide_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = dict(payload.get("output_json") or {})
        context_packet = (payload.get("input_json") or {}).get("context_packet") or {}
        output.setdefault("intent", context_packet.get("intent"))
        output.setdefault("active_plan_exists", True)
        output.setdefault("should_generate_plan", False)
        output.setdefault("reason", "schema_repair_default_no_generation")
        return {"output_json": output}

    def _build_chat_tool_registry(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        profile: models.UserProfile,
        message: str,
    ) -> ToolRegistry:
        registry = ToolRegistry()

        registry.register(
            ToolSpec(
                name="profile.extract",
                description="Extract structured profile patch, open memories, and corrections from the current message.",
                input_schema={
                    "type": "object",
                    "required": ["message_chars"],
                    "properties": {"message_chars": {"type": "integer", "minimum": 0}},
                },
                output_schema={
                    "type": "object",
                    "required": ["profile_patch", "corrections", "ignored_candidates"],
                    "properties": {
                        "profile_patch": {"type": "object"},
                        "corrections": {"type": "array"},
                        "ignored_candidates": {"type": "array"},
                        "model_used": {"type": "boolean"},
                    },
                },
                permission_level="write_candidate",
                side_effects=False,
                retry_count=1,
                risk_level="medium",
                tags=["profile", "extraction"],
            ),
            lambda _: self.profile_extractor_agent(profile, message),
            repair_handler=self._repair_profile_extract_tool,
        )
        registry.register(
            ToolSpec(
                name="memory.verify",
                description="Verify long-term memory candidates before durable write.",
                input_schema={
                    "type": "object",
                    "required": ["extraction"],
                    "properties": {"extraction": {"type": "object"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["passed", "accepted_candidates", "accepted_corrections", "rejected_candidates"],
                    "properties": {
                        "passed": {"type": "boolean"},
                        "accepted_candidates": {"type": "array"},
                        "accepted_corrections": {"type": "array"},
                        "rejected_candidates": {"type": "array"},
                        "issues": {"type": "array"},
                        "repair_actions": {"type": "array"},
                    },
                },
                permission_level="read",
                side_effects=False,
                retry_count=1,
                risk_level="medium",
                tags=["memory", "verifier"],
            ),
            lambda payload: self._verify_memory_tool(
                user_id,
                message,
                payload.get("extraction") or {},
                profile,
            ),
            repair_handler=self._repair_memory_verify_tool,
        )
        registry.register(
            ToolSpec(
                name="memory.write",
                description="Write verified stable long-term memory candidates after profile extraction.",
                input_schema={
                    "type": "object",
                    "required": ["extraction", "verification"],
                    "properties": {"extraction": {"type": "object"}, "verification": {"type": "object"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["written"],
                    "properties": {"written": {"type": "array", "items": {"type": "string"}}},
                },
                permission_level="write",
                side_effects=True,
                risk_level="high",
                idempotency_key_fields=["extraction"],
                tags=["memory", "write"],
            ),
            lambda payload: {
                "written": self.write_memories_from_message(
                    user_id,
                    message,
                    payload.get("extraction") or {},
                    payload.get("verification"),
                )
            },
        )
        registry.register(
            ToolSpec(
                name="context.build",
                description="Build a current-intent context packet from profile, memory, knowledge, rules, and templates.",
                input_schema={
                    "type": "object",
                    "required": ["message_chars"],
                    "properties": {"message_chars": {"type": "integer", "minimum": 0}},
                },
                output_schema={
                    "type": "object",
                    "required": ["intent", "current_request_policy"],
                    "properties": {
                        "intent": {"type": "string"},
                        "current_request_policy": {"type": "object"},
                        "knowledge_context": {"type": "object"},
                    },
                },
                permission_level="read",
                side_effects=False,
                retry_count=1,
                risk_level="low",
                tags=["context"],
            ),
            lambda _: ContextBuilder(self.db, self.model_provider).build_context_packet(user_id, message),
            repair_handler=self._repair_context_build_tool,
        )
        registry.register(
            ToolSpec(
                name="plan.decide",
                description="Decide whether this turn may generate or show plan content.",
                input_schema={
                    "type": "object",
                    "required": ["context_packet"],
                    "properties": {"context_packet": {"type": "object"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["intent", "active_plan_exists", "should_generate_plan", "reason"],
                    "properties": {
                        "intent": {"type": ["string", "null"]},
                        "active_plan_exists": {"type": "boolean"},
                        "should_generate_plan": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                },
                permission_level="read",
                side_effects=False,
                retry_count=1,
                risk_level="low",
                tags=["planner"],
            ),
            lambda payload: self._plan_generation_decision(payload.get("context_packet") or {}, user_id),
            repair_handler=self._repair_plan_decide_tool,
        )
        registry.register(
            ToolSpec(
                name="plan.generate",
                description="Generate and persist the first active plan when the current request explicitly asks for it.",
                input_schema={
                    "type": "object",
                    "properties": {"reason": {"type": ["string", "null"]}},
                },
                output_schema={
                    "type": "object",
                    "required": ["plan_id", "active_plan"],
                    "properties": {"plan_id": {"type": "string"}, "active_plan": {"type": "object"}},
                },
                permission_level="write",
                side_effects=True,
                risk_level="high",
                idempotency_key_fields=["reason"],
                tags=["plan", "write"],
            ),
            lambda _: self._generate_plan_tool(user_id),
        )
        registry.register(
            ToolSpec(
                name="plan.verify",
                description="Verify generated plan constraints before the plan is used in the coach response.",
                input_schema={
                    "type": "object",
                    "required": ["plan_payload", "context_packet"],
                    "properties": {"plan_payload": {"type": "object"}, "context_packet": {"type": "object"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["passed", "issues", "repair_actions"],
                    "properties": {
                        "passed": {"type": "boolean"},
                        "issues": {"type": "array"},
                        "repair_actions": {"type": "array"},
                    },
                },
                permission_level="read",
                side_effects=False,
                retry_count=1,
                risk_level="medium",
                tags=["plan", "verifier"],
            ),
            lambda payload: self._verify_plan_tool(
                payload.get("plan_payload") or {},
                payload.get("context_packet") or {},
                profile,
            ),
        )
        registry.register(
            ToolSpec(
                name="plan.repair",
                description="Apply deterministic repairs for verifier findings on a generated plan.",
                input_schema={
                    "type": "object",
                    "required": ["plan_payload", "verification", "context_packet"],
                    "properties": {
                        "plan_id": {"type": ["string", "null"]},
                        "plan_payload": {"type": "object"},
                        "verification": {"type": "object"},
                        "context_packet": {"type": "object"},
                    },
                },
                permission_level="write",
                side_effects=True,
                risk_level="high",
                idempotency_key_fields=["plan_id", "verification"],
                tags=["plan", "repair"],
            ),
            lambda payload: self._repair_plan_tool(
                payload.get("plan_id"),
                payload.get("plan_payload") or {},
                payload.get("verification") or {},
                payload.get("context_packet") or {},
                profile,
            ),
        )
        registry.register(
            ToolSpec(
                name="response.verify",
                description="Verify the assistant response against current-message policy and retrieved context.",
                input_schema={
                    "type": "object",
                    "required": ["assistant_message", "context_packet"],
                    "properties": {"assistant_message": {"type": "string"}, "context_packet": {"type": "object"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["passed", "issues", "repair_actions"],
                    "properties": {
                        "passed": {"type": "boolean"},
                        "issues": {"type": "array"},
                        "repair_actions": {"type": "array"},
                    },
                },
                permission_level="read",
                side_effects=False,
                retry_count=1,
                risk_level="medium",
                tags=["response", "verifier"],
            ),
            lambda payload: self._verify_response_tool(
                payload.get("assistant_message") or "",
                message,
                payload.get("context_packet") or {},
            ),
        )
        registry.register(
            ToolSpec(
                name="response.repair",
                description="Append deterministic repair text when response verification finds fixable issues.",
                input_schema={
                    "type": "object",
                    "required": ["verification", "context_packet"],
                    "properties": {"verification": {"type": "object"}, "context_packet": {"type": "object"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["repaired", "repair_text"],
                    "properties": {"repaired": {"type": "boolean"}, "repair_text": {"type": "string"}},
                },
                permission_level="write_candidate",
                side_effects=False,
                risk_level="medium",
                tags=["response", "repair"],
            ),
            lambda payload: self._repair_response_tool(
                payload.get("verification") or {},
                payload.get("context_packet") or {},
            ),
        )
        registry.register(
            ToolSpec(
                name="guardrail.check",
                description="Run safety guardrails over the draft assistant response.",
                input_schema={
                    "type": "object",
                    "required": ["assistant_message"],
                    "properties": {"assistant_message": {"type": "string"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["action", "flag_count"],
                    "properties": {"action": {"type": "string"}, "flag_count": {"type": "integer"}},
                },
                permission_level="read",
                side_effects=False,
                retry_count=1,
                risk_level="critical",
                tags=["safety", "guardrail"],
            ),
            lambda payload: self._guardrail_tool(payload.get("assistant_message") or "", message, profile),
        )
        registry.register(
            ToolSpec(
                name="response.persist",
                description="Persist assistant message, agent run, tool calls, and readable log path.",
                input_schema={
                    "type": "object",
                    "required": ["assistant_message"],
                    "properties": {"assistant_message": {"type": "string"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["session_id", "response_chars"],
                    "properties": {"session_id": {"type": "string"}, "response_chars": {"type": "integer"}},
                },
                permission_level="write",
                side_effects=True,
                risk_level="medium",
                idempotency_key_fields=["assistant_message"],
                tags=["response", "persist"],
            ),
            lambda payload: {
                "session_id": str(session_id),
                "response_chars": len(payload.get("assistant_message") or ""),
            },
        )
        return registry

    def _plan_generation_decision(self, context_packet: dict[str, Any], user_id: uuid.UUID) -> dict[str, Any]:
        active_plan = self.get_active_plan(user_id)
        should_generate_plan = self._should_generate_plan_for_context(context_packet)
        return {
            "intent": context_packet.get("intent"),
            "active_plan_exists": active_plan is not None,
            "should_generate_plan": should_generate_plan,
            "reason": (
                "current_message_explicitly_requests_plan"
                if should_generate_plan
                else "current_message_does_not_request_plan"
            ),
        }

    def _generate_plan_tool(self, user_id: uuid.UUID) -> dict[str, Any]:
        plan = self.generate_plan(PlanGenerateRequest(user_id=user_id))
        return {
            "plan_id": str(plan.id),
            "active_plan": self._plan_context_payload(plan),
        }

    def _verify_plan_tool(
        self,
        plan_payload: dict[str, Any],
        context_packet: dict[str, Any],
        profile: models.UserProfile,
    ) -> dict[str, Any]:
        return AgentVerifier().verify_plan(
            plan_payload,
            self._profile_payload(profile),
            context_packet,
        ).to_dict()

    def _repair_plan_tool(
        self,
        plan_id: str | None,
        plan_payload: dict[str, Any],
        verification: dict[str, Any],
        context_packet: dict[str, Any],
        profile: models.UserProfile,
    ) -> dict[str, Any]:
        repaired_payload = AgentVerifier().repair_plan(
            plan_payload,
            verification,
            self._profile_payload(profile),
            context_packet,
        )
        repaired = bool(repaired_payload != plan_payload)
        if repaired and plan_id:
            try:
                plan = self.db.get(models.TrainingPlan, uuid.UUID(str(plan_id)))
            except ValueError:
                plan = None
            if plan is not None:
                active_plan = repaired_payload.get("plan") if "plan" in repaired_payload else repaired_payload
                if isinstance(active_plan, dict) and "plan" in active_plan:
                    active_plan = active_plan.get("plan")
                if isinstance(active_plan, dict):
                    plan.plan_json = active_plan
                    self.db.flush()
                    repaired_payload = self._plan_context_payload(plan)
        return {
            "repaired": repaired,
            "repair_actions": verification.get("repair_actions") or [],
            "active_plan": repaired_payload,
        }

    def _verify_response_tool(
        self,
        assistant_message: str,
        user_message: str,
        context_packet: dict[str, Any],
    ) -> dict[str, Any]:
        return AgentVerifier().verify_response(
            assistant_message,
            user_message,
            context_packet,
        ).to_dict()

    def _repair_response_tool(
        self,
        verification: dict[str, Any],
        context_packet: dict[str, Any],
    ) -> dict[str, Any]:
        return AgentVerifier().repair_response(verification, context_packet)

    def _guardrail_tool(
        self,
        assistant_message: str,
        user_message: str,
        profile: models.UserProfile,
    ) -> dict[str, Any]:
        result = run_guardrails(assistant_message, user_message=user_message, profile=profile)
        return {
            "action": result.action.value,
            "passed": result.passed,
            "flag_count": len(result.flags),
            "replacement": result.blocked_replacement,
            "flags": [
                {
                    "rule_id": flag.rule_id,
                    "severity": flag.severity.value,
                    "category": flag.category,
                    "message": flag.message,
                }
                for flag in result.flags
            ],
        }

    def _should_generate_plan_for_context(self, context_packet: dict[str, Any]) -> bool:
        policy = context_packet.get("current_request_policy") or {}
        return bool(policy.get("should_generate_plan"))

    def _allow_plan_content_for_context(self, context_packet: dict[str, Any] | None) -> bool:
        if not context_packet:
            return True
        policy = context_packet.get("current_request_policy") or {}
        return bool(policy.get("allow_plan_content"))

    def _plan_context_payload(self, plan: models.TrainingPlan) -> dict[str, Any]:
        return {
            "id": str(plan.id),
            "status": plan.status,
            "week_start": plan.week_start.isoformat() if plan.week_start else None,
            "plan": plan.plan_json,
            "rationale": plan.rationale,
        }

    def _active_plans(self, user_id: uuid.UUID) -> list[models.TrainingPlan]:
        return list(
            self.db.scalars(
                select(models.TrainingPlan).where(
                    models.TrainingPlan.user_id == user_id,
                    models.TrainingPlan.status == "active",
                )
            )
        )

    def latest_checkin(self, user_id: uuid.UUID) -> models.DailyCheckin | None:
        return self.db.scalar(
            select(models.DailyCheckin)
            .where(models.DailyCheckin.user_id == user_id)
            .order_by(desc(models.DailyCheckin.checkin_date), desc(models.DailyCheckin.created_at))
        )

    def recent_memories(self, user_id: uuid.UUID, limit: int = 6) -> list[models.LongTermMemory]:
        return list(
            self.db.scalars(
                select(models.LongTermMemory)
                .where(
                    models.LongTermMemory.user_id == user_id,
                    models.LongTermMemory.status == "active",
                )
                .order_by(desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                .limit(limit)
            )
        )

    def retrieve_relevant_memories(
        self,
        user_id: uuid.UUID,
        query: str,
        limit: int = 6,
    ) -> list[models.LongTermMemory]:
        vector_candidates: list[models.LongTermMemory] = []
        try:
            query_embedding = self.model_provider.embed_text(query)
            vector_candidates = list(
                self.db.scalars(
                    select(models.LongTermMemory)
                    .where(
                        models.LongTermMemory.user_id == user_id,
                        models.LongTermMemory.status == "active",
                        models.LongTermMemory.embedding.is_not(None),
                    )
                    .order_by(models.LongTermMemory.embedding.cosine_distance(query_embedding))
                    .limit(limit * 3)
                )
            )
        except Exception:
            vector_candidates = []

        candidates_by_id: dict[uuid.UUID, models.LongTermMemory] = {
            memory.id: memory for memory in vector_candidates
        }
        for memory in self.recent_memories(user_id, limit * 3):
            candidates_by_id.setdefault(memory.id, memory)

        ranked = sorted(
            candidates_by_id.values(),
            key=lambda memory: self._memory_relevance_score(memory, query),
            reverse=True,
        )[:limit]
        for memory in ranked:
            memory.last_accessed_at = datetime.utcnow()
            memory.access_count = (memory.access_count or 0) + 1
        return ranked

    def _memory_relevance_score(self, memory: models.LongTermMemory, query: str) -> float:
        lowered = query.lower()
        content = memory.content.lower()
        keywords = [token for token in re.split(r"[\s,，。；;:：]+", lowered) if len(token) >= 2]
        keyword_hits = sum(1 for token in keywords if token in content)
        type_boost = {
            "medical_context": 0.35,
            "risk_signal": 0.3,
            "recent_state": 0.18,
            "training_performance": 0.16,
            "nutrition_habit": 0.14,
            "stable_preference": 0.12,
        }.get(memory.memory_type, 0.08)
        if memory.memory_type in {"medical_context", "risk_signal"} and any(
            token in lowered
            for token in ["hiit", "高强度", "心率", "头晕", "胸闷", "疼", "痛", "药", "疾病"]
        ):
            type_boost += 0.25
        return float(memory.importance or 0.5) + type_boost + keyword_hits * 0.08

    def _build_plan_json(
        self,
        profile: models.UserProfile,
        plan_days: int,
        volume_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        frequency = profile.workout_frequency or 3
        frequency = max(2, min(6, frequency))
        equipment = profile.equipment_available or ["bodyweight", "dumbbells"]
        goal = profile.goal or "maintenance"
        base_sets = max(2, round(3 * volume_multiplier))
        reps = "6-10" if goal == "muscle_gain" else "10-15"

        templates = [
            ("Full Body Strength", ["squat pattern", "push", "pull", "hinge", "core"]),
            ("Upper Body", ["horizontal push", "horizontal pull", "vertical push", "arms"]),
            ("Lower Body", ["squat", "hinge", "single-leg", "calves", "core"]),
            ("Conditioning + Mobility", ["zone 2 cardio", "mobility", "core"]),
            ("Push Focus", ["bench or push-up", "overhead press", "triceps"]),
            ("Pull Focus", ["row", "pull-up or pulldown", "rear delts", "biceps"]),
        ]
        training_days = []
        for index in range(frequency):
            name, movements = templates[index % len(templates)]
            training_days.append(
                {
                    "day": index + 1,
                    "name": name,
                    "focus": movements[0],
                    "equipment": equipment,
                    "exercises": [
                        {
                            "name": movement,
                            "sets": base_sets,
                            "reps": reps,
                            "rest_seconds": 90,
                            "notes": "Keep 1-3 reps in reserve and stop if pain appears.",
                        }
                        for movement in movements
                    ],
                }
            )

        return {
            "goal": goal,
            "plan_days": plan_days,
            "training_days": training_days,
            "nutrition": {
                "target_calories": profile.target_calories,
                "protein_g": profile.target_protein_g,
                "carbs_g": profile.target_carbs_g,
                "fat_g": profile.target_fat_g,
                "principles": [
                    "Anchor each meal around protein.",
                    "Put more carbs near training sessions.",
                    "Adjust portions weekly based on weight trend and adherence.",
                ],
            },
            "review_cadence": "weekly",
        }

    def _plan_rationale(self, profile: models.UserProfile) -> str:
        return (
            f"Plan targets {profile.goal or 'maintenance'} with "
            f"{profile.workout_frequency or 3} sessions per week, "
            f"{profile.target_calories or 2000} kcal/day, and equipment "
            f"{', '.join(profile.equipment_available or ['bodyweight', 'dumbbells'])}."
        )


    def _should_adjust_from_checkin(self, checkin: models.DailyCheckin) -> bool:
        if checkin.fatigue and checkin.fatigue >= 8:
            return True
        if checkin.soreness and checkin.soreness >= 8:
            return True
        if checkin.sleep_hours is not None and checkin.sleep_hours < 6:
            return True
        if checkin.workout_completion is not None and checkin.workout_completion < 60:
            return True
        return False

    def _checkin_memory(self, checkin: models.DailyCheckin) -> str:
        parts = []
        if checkin.sleep_hours is not None:
            parts.append(f"sleep={checkin.sleep_hours}h")
        if checkin.fatigue is not None:
            parts.append(f"fatigue={checkin.fatigue}/10")
        if checkin.soreness is not None:
            parts.append(f"soreness={checkin.soreness}/10")
        if checkin.notes:
            parts.append(f"notes={checkin.notes}")
        return "; ".join(parts)

    def _suggestions(
        self,
        profile: models.UserProfile,
        checkin: models.DailyCheckin | None,
    ) -> list[str]:
        suggestions = []
        if checkin and checkin.sleep_hours is not None and checkin.sleep_hours < 6:
            suggestions.append("Sleep was low; reduce heavy compound volume today.")
        if checkin and checkin.fatigue and checkin.fatigue >= 8:
            suggestions.append("Fatigue is high; swap max-effort work for technique practice.")
        if profile.target_protein_g:
            suggestions.append(f"Aim for about {round(profile.target_protein_g)}g protein today.")
        if not suggestions:
            suggestions.append("Log your workout completion and RPE after training so I can tune the next session.")
        return suggestions

    def _save_message(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        content: str,
    ) -> models.ChatMessage:
        saved_at = datetime.utcnow()
        msg = models.ChatMessage(
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            created_at=saved_at,
            updated_at=saved_at,
        )
        self.db.add(msg)
        session = self.db.get(models.ConversationSession, session_id)
        if session is not None:
            session.updated_at = saved_at
        self.db.flush()
        return msg

    def _node(self, name: str, start: float, output: dict[str, Any]) -> dict[str, Any]:
        return {
            "node": name,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "output": output,
        }

    def _first_number(self, text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_keywords(self, text: str, mapping: dict[str, list[str]]) -> list[str]:
        found = []
        for normalized, keywords in mapping.items():
            if any(keyword in text for keyword in keywords):
                found.append(normalized)
        return found

    def _write_eval_log(self, suite_name: str, results: list[dict[str, Any]]) -> str:
        log_dir = Path("logs/experiments")
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"{timestamp}-{suite_name}.log"
        with log_path.open("w", encoding="utf-8") as file:
            file.write(f"AI Fitness Agent eval suite: {suite_name}\n")
            file.write(f"timestamp_utc: {timestamp}\n")
            file.write(f"provider: {self.model_provider.settings.llm_provider}\n")
            file.write("\nresults:\n")
            for result in results:
                file.write(json.dumps(result, ensure_ascii=False) + "\n")
        return str(log_path)

    # Clean overrides for the methods above. They intentionally avoid literal
    # Chinese text so Windows console encodings cannot corrupt the parser.
    def run_evals(self, suite_name: str, persist_cases: bool) -> dict[str, Any]:
        FitnessKnowledgeService(self.db, self.model_provider).seed_builtin_knowledge()
        with (KNOWLEDGE_DIR / "eval_cases.json").open("r", encoding="utf-8") as file:
            cases = json.load(file)
        results = []
        passed = 0

        for case in cases:
            eval_user = self._prepare_eval_user()
            context_packet = ContextBuilder(self.db, self.model_provider).build_context_packet(
                eval_user.id,
                str(case["input"]),
            )
            knowledge_context = context_packet.get("knowledge_context") or {}
            debug = knowledge_context.get("debug", {})
            expected = case.get("expected") or {}
            checks = {
                "intent": self._eval_check_equal(context_packet.get("intent"), expected.get("intent")),
                "knowledge": self._eval_check_contains(
                    debug.get("matched_knowledge_ids", []),
                    expected.get("must_include_knowledge", []),
                ),
                "rules": self._eval_check_contains(
                    debug.get("matched_rule_ids", []),
                    expected.get("must_trigger_rule", []),
                ),
                "templates": self._eval_check_contains(
                    debug.get("matched_template_ids", []),
                    expected.get("must_include_template", []),
                ),
                "cases": self._eval_check_contains(
                    debug.get("matched_case_ids", []),
                    expected.get("must_include_case", []),
                ),
            }
            active_checks = [value for value in checks.values() if value is not None]
            score = sum(1 for value in active_checks if value) / max(len(active_checks), 1)
            ok = score >= 1.0
            details = {
                "input": case["input"],
                "intent": context_packet.get("intent"),
                "context_summary": context_packet.get("context_summary"),
                "knowledge_debug": debug,
                "checks": checks,
                "embedding_mode": self.model_provider.embedding_mode(),
            }

            passed += int(ok)
            result = {
                "name": case["name"],
                "category": case["category"],
                "passed": ok,
                "score": round(score, 2),
                "details": details,
            }
            results.append(result)

            if persist_cases:
                eval_case = self.db.scalar(
                    select(models.EvalCase).where(models.EvalCase.name == case["name"])
                )
                if not eval_case:
                    eval_case = models.EvalCase(
                        name=case["name"],
                        category=case["category"],
                        input_json={"input": case["input"]},
                        expected_json={"expected": expected},
                    )
                    self.db.add(eval_case)
                    self.db.flush()
                self.db.add(
                    models.EvalResult(
                        eval_case_id=eval_case.id,
                        score=score,
                        passed=ok,
                        details=details,
                    )
                )

        self.db.commit()
        log_path = self._write_eval_log(suite_name, results)
        return {
            "suite_name": suite_name,
            "total": len(results),
            "passed": passed,
            "score": round(passed / len(results), 2),
            "log_path": log_path,
            "results": results,
        }

    def _prepare_eval_user(self) -> models.User:
        eval_email = f"eval-{uuid.uuid4().hex[:8]}@test.local"
        from fast_api.app.core.security import hash_password
        user = models.User(
            id=uuid.uuid4(),
            email=eval_email,
            password_hash=hash_password("eval-pass"),
            display_name="Eval User",
        )
        self.db.add(user)
        self.db.flush()
        profile = models.UserProfile(user_id=user.id)
        profile.age = 21
        profile.sex = "male"
        profile.height_cm = 178
        profile.weight_kg = 80
        profile.goal = "fat_loss"
        profile.experience_level = "intermediate"
        profile.workout_frequency = 5
        profile.equipment_available = ["gym", "barbell", "dumbbell", "machines"]
        profile.dietary_preferences = ["takeout_friendly"]
        profile.injuries = []
        self._refresh_macro_targets(profile)
        self.db.flush()
        self._write_memory(
            user.id,
            "medical_context",
            "用户有甲亢背景，正在服用赛治，需要避免突然高强度冲刺和极限重量。",
            "eval_seed",
            0.95,
            {"condition": "hyperthyroidism"},
            0.95,
        )
        self._write_memory(
            user.id,
            "nutrition_habit",
            "用户平时不自己做饭，饮食建议需要外食、食堂、外卖友好。",
            "eval_seed",
            0.85,
            {"habit": "takeout"},
            0.9,
        )
        self._write_memory(
            user.id,
            "correction",
            "用户明确否认肩伤，后续不能声称用户右肩有伤。",
            "eval_seed",
            0.9,
            {"field": "injuries", "action": "remove", "value": "shoulder"},
            0.95,
        )
        recovery = models.RecoveryLog(
            user_id=user.id,
            log_date=date.today(),
            sleep_hours=5,
            fatigue_score=9,
            soreness_score=8,
            notes="eval high fatigue state",
        )
        self.db.add(recovery)
        session = models.WorkoutSession(
            user_id=user.id,
            session_date=date.today(),
            session_name="Eval bench session",
            completion_score=1.0,
            fatigue_score=7,
        )
        self.db.add(session)
        self.db.flush()
        self.db.add(
            models.ExerciseLog(
                user_id=user.id,
                session_id=session.id,
                exercise_name="bench_press",
                set_index=1,
                reps=8,
                weight=50,
                rpe=8,
                completed=True,
                pain_score=0,
            )
        )
        self.db.flush()
        return user

    def _eval_check_equal(self, actual: Any, expected: Any) -> bool | None:
        if expected is None:
            return None
        return actual == expected

    def _eval_check_contains(self, actual: list[Any], expected: list[Any] | None) -> bool | None:
        if not expected:
            return None
        actual_set = {str(item) for item in actual}
        return all(str(item) in actual_set for item in expected)

    async def profile_extractor_agent(
        self,
        profile: models.UserProfile,
        text: str,
    ) -> dict[str, Any]:
        extraction = self._rule_profile_extraction(text)
        if self._should_use_llm_extractor(text, extraction):
            llm_patch = await self._llm_profile_patch(profile, text, extraction)
            if llm_patch:
                self._merge_llm_profile_patch(extraction, llm_patch, text)
        return extraction

    def extract_profile_updates(self, text: str) -> dict[str, Any]:
        return self._rule_profile_extraction(text)["profile_patch"]

    def _rule_profile_extraction(self, text: str) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        corrections: list[dict[str, Any]] = []
        ignored_candidates: list[dict[str, Any]] = []

        age = self._first_number(
            text,
            [
                r"age\s*[:=]?\s*(\d{2})",
                r"(\d{2})\s*岁",
                r"年龄\s*[：:=\s\-]*\s*(\d{2})",
                r"\*\*年龄：\*\*\s*(\d{2})",
            ],
        )
        if age:
            updates["age"] = int(age)

        lowered = text.lower()
        if re.search(r"性别\s*[:：\-\*]*\s*男|\bmale\b|\bman\b|(^|[\s,，。；;])男($|[\s,，。；;])", lowered):
            updates["sex"] = "male"
        elif re.search(r"性别\s*[:：\-\*]*\s*女|\bfemale\b|\bwoman\b|(^|[\s,，。；;])女($|[\s,，。；;])", lowered):
            updates["sex"] = "female"

        height = self._first_number(
            text,
            [
                r"height\s*[:=]?\s*(\d{2,3})\s*cm",
                r"身高\s*[：:=\s\-]*\s*(\d{2,3})\s*cm",
                r"身高\s*[：:=\s\-]*\s*(\d{2,3})",
                r"(\d+(?:\.\d+)?)\s*m\b",          # "1.75m" (English meter)
                r"(\d{3})\s*cm",
            ],
        )

        # Chinese colloquial "1米75" / "一米七五" → two numbers around 米
        if height is None:
            m_cn = re.search(r"(\d)\s*米\s*(\d{1,2})", text)
            if m_cn:
                height = f"{m_cn.group(1)}.{m_cn.group(2)}"

        if height:
            value = float(height)
            # If the number looks like meters (e.g. "1.75" → 175 cm)
            if 0.5 < value < 3.5:
                value = round(value * 100)
            if 90 <= value <= 240:
                updates["height_cm"] = value

        weight, ignored_weight_candidates = self._extract_body_weight_kg(text)
        if weight:
            updates["weight_kg"] = float(weight)
        ignored_candidates.extend(ignored_weight_candidates)

        if any(keyword in lowered for keyword in ["muscle", "bulk", "\u589e\u808c"]):
            updates["goal"] = "muscle_gain"
        elif any(
            keyword in lowered
            for keyword in [
                "fat loss",
                "lose fat",
                "cut",
                "减脂",
                "降脂",
                "显现腹肌",
                "腹肌",
            ]
        ):
            updates["goal"] = "fat_loss"
        elif any(keyword in lowered for keyword in ["maintenance", "\u7ef4\u6301"]):
            updates["goal"] = "maintenance"

        frequency = self._first_number(
            text,
            [
                r"每周(?:可)?(?:训练|锻炼)?\s*(\d)\s*天",
                r"(\d)\s*days?\s*(?:per|/)\s*week",
            ],
        )
        if frequency:
            updates["workout_frequency"] = int(frequency)

        if any(keyword in lowered for keyword in ["beginner", "\u65b0\u624b", "\u96f6\u57fa\u7840"]):
            updates["experience_level"] = "beginner"
        elif (
            any(keyword in lowered for keyword in ["intermediate", "\u8fdb\u9636", "\u4e2d\u7ea7"])
            or re.search(r"(系统)?(?:训练|锻炼)(?:过)?\s*1\s*年", text)
            or re.search(r"(系统)?(?:训练|锻炼)(?:过)?\s*一\s*年", text)
            or re.search(r"练(?:了|过)?\s*1\s*年", text)
            or re.search(r"练(?:了|过)?\s*一\s*年", text)
        ):
            updates["experience_level"] = "intermediate"
        elif any(keyword in lowered for keyword in ["advanced", "\u9ad8\u7ea7"]):
            updates["experience_level"] = "advanced"

        equipment = self._extract_equipment(lowered)
        if equipment:
            updates["equipment_available"] = equipment

        dietary = self._extract_keywords(
            lowered,
            {
                "vegetarian": ["vegetarian", "\u7d20\u98df"],
                "high_protein": ["high protein", "\u9ad8\u86cb\u767d"],
                "low_carb": ["low carb", "\u4f4e\u78b3"],
                "no_spicy": ["no spicy", "\u4e0d\u5403\u8fa3"],
                "eat_out": ["\u4e0d\u81ea\u5df1\u505a\u996d", "\u5916\u98df", "\u98df\u5802", "takeout"],
                "flexible_diet": ["\u4ec0\u4e48\u90fd\u53ef\u4ee5\u5403", "no restriction"],
            },
        )
        if dietary:
            updates["dietary_preferences"] = dietary

        injury_result = self._extract_injury_state(text)
        if injury_result["injuries"]:
            updates["injuries"] = injury_result["injuries"]
        corrections.extend(injury_result["corrections"])
        ignored_candidates.extend(injury_result["ignored_candidates"])

        return {
            "profile_patch": updates,
            "corrections": corrections,
            "ignored_candidates": ignored_candidates,
            "model_patch": {},
            "model_used": False,
        }

    def _extract_equipment(self, lowered: str) -> list[str]:
        equipment = self._extract_keywords(
            lowered,
            {
                "dumbbells": ["dumbbell", "\u54d1\u94c3"],
                "barbell": ["barbell", "\u6760\u94c3"],
                "bodyweight": ["bodyweight", "\u81ea\u91cd"],
                "machines": ["machine", "\u56fa\u5b9a\u5668\u68b0", "\u5668\u68b0"],
                "pull_up_bar": ["pull-up", "\u5355\u6760"],
                "cables": ["cable", "\u9f99\u95e8\u67b6", "\u7ef3\u7d22"],
                "cardio": ["cardio", "\u6709\u6c27\u533a", "\u8dd1\u6b65\u673a"],
            },
        )
        if any(keyword in lowered for keyword in ["\u5065\u8eab\u623f", "\u5546\u4e1a\u5065\u8eab\u623f", "\u5668\u68b0\u9f50\u5168", "gym"]):
            equipment.extend(["gym", "barbell", "dumbbells", "machines", "cables", "cardio"])
        return sorted(set(equipment))

    def _extract_body_weight_kg(self, text: str) -> tuple[float | None, list[dict[str, Any]]]:
        ignored: list[dict[str, Any]] = []
        explicit_patterns = [
            r"(?:体重|当前体重|现在体重|身体体重)[^\d]{0,16}(\d{2,3}(?:\.\d+)?)\s*(?:kg|公斤)",
            r"\b(?:weight|body weight|weigh)\s*[:=]?\s*(\d{2,3}(?:\.\d+)?)\s*kg",
            r"(?:身高[^\d]{0,8})?\d{2,3}\s*cm[^\d]{0,16}(?:体重[^\d]{0,8})?(\d{2,3}(?:\.\d+)?)\s*(?:kg|公斤)",
        ]
        for pattern in explicit_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return float(match.group(1)), ignored

        for match in re.finditer(r"(\d{2,3}(?:\.\d+)?)\s*(?:kg|公斤)", text, flags=re.IGNORECASE):
            value = float(match.group(1))
            window = text[max(0, match.start() - 24) : min(len(text), match.end() + 24)]
            ignored.append(
                {
                    "field": "weight_kg",
                    "candidate": value,
                    "reason": (
                        "training_load_not_body_weight"
                        if self._is_training_load_context(window)
                        else "kg_without_body_weight_context"
                    ),
                    "evidence": window,
                }
            )
        return None, ignored

    def _source_text_supports_body_weight(self, source_text: str | None, value: Any) -> bool:
        if not source_text:
            return True
        number = self._coerce_float(value)
        if number is None:
            return False
        weight, _ignored = self._extract_body_weight_kg(source_text)
        if weight is not None and abs(weight - number) < 0.01:
            return True
        return False

    def _is_training_load_context(self, text: str) -> bool:
        lowered = text.lower()
        training_terms = [
            "卧推",
            "深蹲",
            "硬拉",
            "推肩",
            "划船",
            "弯举",
            "上胸",
            "下胸",
            "固定器械",
            "做组",
            "组",
            "次数",
            "rpe",
            "bench",
            "squat",
            "deadlift",
            "press",
            "row",
            "curl",
            "set",
            "sets",
            "reps",
        ]
        return any(term in lowered for term in training_terms)

    def _extract_injury_state(self, text: str) -> dict[str, Any]:
        lowered = text.lower()
        body_parts = {
            "shoulder": ["shoulder", "\u80a9", "\u80a9\u8180", "\u53f3\u80a9", "\u5de6\u80a9"],
            "knee": ["knee", "\u819d", "\u819d\u76d6"],
            "lower_back": ["lower back", "\u8170", "\u4e0b\u80cc"],
            "wrist": ["wrist", "\u624b\u8155"],
        }
        injury_terms = ["injury", "pain", "hurt", "rehab", "\u4f24", "\u75bc", "\u75db", "\u4e0d\u9002", "\u5eb7\u590d", "\u523a\u75db", "\u62c9\u4f24", "\u626d\u4f24"]
        negation_terms = ["\u6ca1\u6709", "\u6ca1\u8bf4\u8fc7", "\u65e0", "\u5426\u8ba4", "\u6ca1\u4f24", "\u4e0d\u662f", "no ", "not "]
        training_terms = ["\u63a8\u80a9", "shoulder press", "\u5750\u59ff\u63a8\u80a9", "\u4fa7\u5e73\u4e3e"]

        injuries: list[str] = []
        corrections: list[dict[str, Any]] = []
        ignored_candidates: list[dict[str, Any]] = []
        sentences = [item for item in re.split(r"[\n\u3002\uff1b;.!?]", text) if item.strip()]
        for canonical, terms in body_parts.items():
            has_body_part = any(term in lowered for term in terms)
            if not has_body_part:
                continue
            has_negation = any(term in lowered for term in negation_terms)
            if has_negation:
                corrections.append(
                    {
                        "field": "injuries",
                        "action": "remove",
                        "value": canonical,
                        "evidence": text[:240],
                    }
                )
                continue

            positive = False
            ignored_reason = "body_part_without_injury_context"
            for sentence in sentences:
                sentence_lower = sentence.lower()
                if not any(term in sentence_lower for term in terms):
                    continue
                if any(term in sentence_lower for term in training_terms) and not any(term in sentence_lower for term in injury_terms):
                    ignored_reason = "training_movement_not_injury"
                    continue
                if any(term in sentence_lower for term in injury_terms):
                    positive = True
                    break
            if positive:
                injuries.append(canonical)
            else:
                ignored_candidates.append(
                    {
                        "field": "injuries",
                        "candidate": canonical,
                        "reason": ignored_reason,
                        "evidence": text[:240],
                    }
                )

        if any(term in lowered for term in ["\u65e0\u4f24\u75c5", "\u6ca1\u6709\u4f24\u75c5", "no injuries"]):
            corrections.append(
                {
                    "field": "injuries",
                    "action": "clear",
                    "value": "*",
                    "evidence": text[:240],
                }
            )
            injuries = []
        return {
            "injuries": sorted(set(injuries)),
            "corrections": corrections,
            "ignored_candidates": ignored_candidates,
        }

    def _should_use_llm_extractor(self, text: str, extraction: dict[str, Any]) -> bool:
        if not self.model_provider.has_live_model():
            return False
        if len(text) < 180 and "#" not in text:
            return False
        useful_fields = {
            key
            for key, value in extraction.get("profile_patch", {}).items()
            if value not in (None, [], "")
        }
        return len(useful_fields) < 5

    async def _llm_profile_patch(
        self,
        profile: models.UserProfile,
        text: str,
        extraction: dict[str, Any],
    ) -> dict[str, Any] | None:
        system_prompt = registry.get("coach_profile_extractor")
        user_prompt = json.dumps(
            {
                "user_message": text,
                "current_profile": {
                    "age": profile.age,
                    "sex": profile.sex,
                    "height_cm": profile.height_cm,
                    "weight_kg": profile.weight_kg,
                    "goal": profile.goal,
                    "experience_level": profile.experience_level,
                    "equipment_available": profile.equipment_available,
                    "injuries": profile.injuries,
                },
                "rule_extraction": extraction,
                "required_shape": {
                    "profile_patch": {},
                    "corrections": [
                        {"field": "injuries", "action": "remove", "value": "shoulder"}
                    ],
                },
            },
            ensure_ascii=False,
        )
        tracker = track_llm_call(model=self.model_provider.settings.chat_model)
        try:
            reply = await self.model_provider.coach_reply(system_prompt, user_prompt)
            tracker.success()
        except Exception:
            tracker.failure()
            return None
        return self._extract_json_object(reply or "")

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _merge_llm_profile_patch(
        self,
        extraction: dict[str, Any],
        llm_patch: dict[str, Any],
        source_text: str,
    ) -> None:
        allowed_fields = {
            "age",
            "sex",
            "height_cm",
            "weight_kg",
            "goal",
            "experience_level",
            "workout_frequency",
            "workout_duration",
            "dietary_preferences",
            "equipment_available",
            "injuries",
        }
        profile_patch = llm_patch.get("profile_patch") or {}
        if isinstance(profile_patch, dict):
            for key, value in profile_patch.items():
                if key not in allowed_fields or value in (None, [], ""):
                    continue
                if key in extraction["profile_patch"]:
                    continue
                if key == "injuries":
                    rule_injuries = self._extract_injury_state(source_text)["injuries"]
                    value = [item for item in value if item in rule_injuries]
                    if not value:
                        continue
                value = self._normalize_profile_patch_value(key, value, source_text)
                if value in (None, [], ""):
                    continue
                extraction["profile_patch"][key] = value

        corrections = llm_patch.get("corrections") or []
        if isinstance(corrections, list):
            for correction in corrections:
                if not isinstance(correction, dict):
                    continue
                if correction.get("field") == "injuries" and correction.get("action") in {"remove", "clear"}:
                    extraction["corrections"].append(correction)

        extraction["model_patch"] = llm_patch
        extraction["model_used"] = True

    def write_memories_from_message(
        self,
        user_id: uuid.UUID,
        message: str,
        extraction: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        ) -> list[str]:
        written = []
        extraction = extraction or self._rule_profile_extraction(message)
        profile = (
            self._get_or_create_profile(user_id)
            if self.db is not None
            else SimpleNamespace(
                user_id=user_id,
                age=None,
                sex=None,
                height_cm=None,
                weight_kg=None,
                goal=None,
                experience_level=None,
                workout_frequency=None,
                equipment_available=[],
                injuries=[],
                dietary_preferences=[],
                allergies=[],
            )
        )
        verification = verification or self._verify_memory_tool(user_id, message, extraction, profile)
        corrections_to_write = verification.get("accepted_corrections") or extraction.get("corrections", [])
        candidates_to_write = verification.get("accepted_candidates")
        if candidates_to_write is None:
            candidates_to_write = self._memory_candidates_from_message(message, extraction)

        conflict_resolution = MemoryConflictResolver(self.db).apply_corrections(
            user_id,
            corrections_to_write,
            message,
        )
        for correction in corrections_to_write:
            written.append(
                self._write_memory(
                    user_id=user_id,
                    memory_type="correction",
                    content=(
                        f"用户纠正档案：{correction.get('field')} "
                        f"{correction.get('action')} {correction.get('value')}. 原文：{message}"
                    ),
                    source="chat_correction",
                    importance=0.92,
                    memory_metadata={**correction, "conflict_resolution": conflict_resolution},
                    confidence=0.95,
                )
            )

        for candidate in candidates_to_write:
            written.append(
                self._write_memory(
                    user_id=user_id,
                    memory_type=candidate["memory_type"],
                    content=candidate["content"],
                    source="chat",
                    importance=candidate["importance"],
                    memory_metadata=candidate["memory_metadata"],
                    confidence=candidate["confidence"],
                )
            )
        return [str(item) for item in written if item]

    def _verify_memory_tool(
        self,
        user_id: uuid.UUID,
        message: str,
        extraction: dict[str, Any],
        profile: models.UserProfile,
    ) -> dict[str, Any]:
        candidates = self._memory_candidates_from_message(message, extraction)
        corrections = extraction.get("corrections") or []
        result = MemoryVerifier().verify(
            candidates=candidates,
            corrections=corrections,
            profile_snapshot=self._profile_snapshot(profile),
            message=message,
        )
        return result.to_dict()

    def _profile_snapshot(self, profile: models.UserProfile) -> dict[str, Any]:
        return {
            "user_id": str(profile.user_id),
            "age": profile.age,
            "sex": profile.sex,
            "height_cm": profile.height_cm,
            "weight_kg": profile.weight_kg,
            "goal": profile.goal,
            "experience_level": profile.experience_level,
            "workout_frequency": profile.workout_frequency,
            "equipment_available": profile.equipment_available or [],
            "injuries": profile.injuries or [],
            "dietary_preferences": profile.dietary_preferences or [],
            "allergies": profile.allergies or [],
        }

    async def _live_onboarding_reply(
        self,
        profile: models.UserProfile,
        missing_slots: list[str],
        message: str,
    ) -> str:
        fallback = self._onboarding_reply(profile, missing_slots)
        if not self.model_provider.has_live_model():
            return fallback

        system_prompt = registry.get("coach_onboarding")
        user_prompt = json.dumps(
            {
                "user_message": message,
                "known_profile": {
                    "age": profile.age,
                    "height_cm": profile.height_cm,
                    "weight_kg": profile.weight_kg,
                    "goal": profile.goal,
                    "experience_level": profile.experience_level,
                    "equipment_available": profile.equipment_available,
                    "injuries": profile.injuries,
                    "dietary_preferences": profile.dietary_preferences,
                },
                "missing_slots": missing_slots,
                "fallback_question": fallback,
            },
            ensure_ascii=False,
        )
        tracker = track_llm_call(model=self.model_provider.settings.chat_model)
        try:
            live_reply = await self.model_provider.coach_reply(system_prompt, user_prompt)
            tracker.success()
        except Exception as exc:
            tracker.failure()
            return registry.get("error_model_call_onboarding").format(fallback=fallback)
        return live_reply or fallback

    async def _stream_static_text(self, text: str):
        for index in range(0, len(text), 3):
            yield text[index : index + 3]

    def _model_call_error_message(self, exc: Exception) -> str:
        provider = self.model_provider.settings.llm_provider
        model = self.model_provider.settings.chat_model
        return registry.get("error_model_call").format(provider=provider, model=model, error=exc)

    def _knowledge_debug_summary(self, payload: dict[str, Any]) -> str:
        knowledge_count = len(payload.get("matched_knowledge_ids") or [])
        case_count = len(payload.get("matched_case_ids") or [])
        return f"召回解释知识 {knowledge_count} 条，教练案例 {case_count} 条"

    def _summarize_tool_execution(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = payload.get("tool_name")
        return {
            "tool_name": tool_name,
            "status": payload.get("status"),
            "latency_ms": payload.get("latency_ms"),
            "attempts": payload.get("attempts", 1),
            "validation_errors": payload.get("validation_errors") or [],
            "repaired": payload.get("repaired", False),
            "repair_actions": payload.get("repair_actions") or [],
            "contract": {
                "contract_id": (payload.get("contract") or {}).get("contract_id"),
                "risk_level": (payload.get("contract") or {}).get("risk_level"),
                "permission_level": (payload.get("contract") or {}).get("permission_level"),
                "input_schema_version": (payload.get("contract") or {}).get("input_schema_version"),
                "output_schema_version": (payload.get("contract") or {}).get("output_schema_version"),
            },
            "idempotency_key": payload.get("idempotency_key"),
            "input_json": self._summarize_tool_payload(tool_name, payload.get("input_json") or {}),
            "output_json": self._summarize_tool_payload(tool_name, payload.get("output_json") or {}),
            "error": payload.get("error"),
        }

    def _summarize_tool_payload(self, tool_name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"value_type": type(payload).__name__}

        if tool_name == "profile.extract":
            return {
                "message_chars": payload.get("message_chars"),
                "profile_patch": payload.get("profile_patch") or {},
                "open_memories_count": len(payload.get("open_memories") or []),
                "corrections": payload.get("corrections") or [],
                "ignored_candidates": payload.get("ignored_candidates") or [],
                "model_used": payload.get("model_used", False),
            }
        if tool_name == "memory.write":
            extraction = payload.get("extraction") or {}
            return {
                "has_extraction": bool(extraction),
                "open_memories_count": len(extraction.get("open_memories") or []),
                "verification_present": bool(payload.get("verification")),
                "written": payload.get("written") or [],
                "written_count": len(payload.get("written") or []),
            }
        if tool_name == "memory.verify":
            return {
                "passed": payload.get("passed"),
                "accepted_count": payload.get("accepted_count", 0),
                "rejected_count": payload.get("rejected_count", 0),
                "issue_count": payload.get("issue_count", len(payload.get("issues") or [])),
                "repair_actions": payload.get("repair_actions") or [],
                "issues": [
                    {
                        "issue_id": issue.get("issue_id"),
                        "severity": issue.get("severity"),
                        "action": issue.get("action"),
                    }
                    for issue in (payload.get("issues") or [])[:6]
                    if isinstance(issue, dict)
                ],
            }
        if tool_name == "context.build":
            knowledge_context = payload.get("knowledge_context") or {}
            return {
                "message_chars": payload.get("message_chars"),
                "intent": payload.get("intent"),
                "current_request_policy": payload.get("current_request_policy") or {},
                "context_summary": payload.get("context_summary"),
                "relevant_memory_count": len(payload.get("relevant_memories") or []),
                "active_risk_count": len(payload.get("active_risk_notes") or []),
                "knowledge_debug": knowledge_context.get("debug", {}),
            }
        if tool_name == "plan.decide":
            context_packet = payload.get("context_packet") or {}
            return {
                "intent": payload.get("intent") or context_packet.get("intent"),
                "current_request_policy": payload.get("current_request_policy")
                or context_packet.get("current_request_policy")
                or {},
                "should_generate_plan": payload.get("should_generate_plan"),
                "reason": payload.get("reason"),
                "context_summary": context_packet.get("context_summary"),
            }
        if tool_name == "plan.generate":
            active_plan = payload.get("active_plan") or {}
            return {
                "reason": payload.get("reason"),
                "plan_id": payload.get("plan_id"),
                "status": payload.get("status") or active_plan.get("status"),
                "has_active_plan": bool(active_plan),
            }
        if tool_name in {"plan.verify", "response.verify"}:
            return {
                "passed": payload.get("passed"),
                "issue_count": payload.get("issue_count", len(payload.get("issues") or [])),
                "repair_actions": payload.get("repair_actions") or [],
                "issues": [
                    {
                        "issue_id": issue.get("issue_id"),
                        "severity": issue.get("severity"),
                        "message": issue.get("message"),
                    }
                    for issue in (payload.get("issues") or [])[:8]
                    if isinstance(issue, dict)
                ],
                "has_plan_payload": bool(payload.get("plan_payload")),
                "assistant_message_chars": len(payload.get("assistant_message") or ""),
            }
        if tool_name == "plan.repair":
            active_plan = payload.get("active_plan") or {}
            return {
                "plan_id": payload.get("plan_id") or active_plan.get("id"),
                "repaired": payload.get("repaired"),
                "repair_actions": payload.get("repair_actions") or [],
                "has_active_plan": bool(active_plan),
            }
        if tool_name == "response.repair":
            return {
                "repaired": payload.get("repaired"),
                "repair_actions": payload.get("repair_actions") or [],
                "repair_text_chars": len(payload.get("repair_text") or ""),
            }
        if tool_name == "guardrail.check":
            return {
                "assistant_message_chars": len(payload.get("assistant_message") or ""),
                "action": payload.get("action"),
                "flag_count": payload.get("flag_count", 0),
                "flags": payload.get("flags") or [],
            }
        return self._truncate_trace_payload(payload)

    def _truncate_trace_payload(self, value: Any, depth: int = 0) -> Any:
        if depth > 3:
            return "[truncated]"
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 12:
                    result["_truncated_keys"] = len(value) - index
                    break
                result[str(key)] = self._truncate_trace_payload(item, depth + 1)
            return result
        if isinstance(value, list):
            items = [self._truncate_trace_payload(item, depth + 1) for item in value[:8]]
            if len(value) > 8:
                items.append({"_truncated_items": len(value) - 8})
            return items
        if isinstance(value, str) and len(value) > 500:
            return value[:500] + "...[truncated]"
        if isinstance(value, (uuid.UUID, datetime, date)):
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
        return value

    def _public_trace_metadata(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "RequestReceived":
            return {
                "provider": payload.get("provider"),
                "chat_model": payload.get("chat_model"),
                "embedding_mode": payload.get("embedding_mode"),
            }
        if name == "LLMPlanner":
            raw = payload.get("raw_output") or {}
            return {
                "planner_mode": payload.get("planner_mode"),
                "planner_fallback": payload.get("planner_fallback", False),
                "intent": raw.get("intent") if isinstance(raw, dict) else None,
                "tool_order": raw.get("tool_order") if isinstance(raw, dict) else [],
                "reasoning_summary": raw.get("reasoning_summary") if isinstance(raw, dict) else None,
            }
        if name == "PlannerVerifier":
            plan = payload.get("verified_plan") or {}
            return {
                "intent": plan.get("intent"),
                "planner_mode": plan.get("planner_mode"),
                "repair_actions": payload.get("repair_actions") or [],
                "step_count": len(plan.get("steps") or []),
                "tool_order": [
                    step.get("tool_name")
                    for step in (plan.get("steps") or [])
                    if isinstance(step, dict) and step.get("tool_name")
                ],
            }
        if name == "PlannerFallback":
            return {
                "planner_fallback": payload.get("planner_fallback", False),
                "reason": payload.get("reason"),
            }
        if name == "AgentPlanner":
            return {
                "plan_id": payload.get("plan_id"),
                "strategy": payload.get("strategy"),
                "intent": payload.get("intent"),
                "step_count": len(payload.get("steps") or []),
                "steps": [
                    {
                        "key": step.get("key"),
                        "tool_name": step.get("tool_name"),
                        "stage": step.get("stage"),
                        "required": step.get("required"),
                    }
                    for step in (payload.get("steps") or [])
                    if isinstance(step, dict)
                ],
            }
        if name == "ToolExecutor":
            return {
                "tool_name": payload.get("tool_name"),
                "status": payload.get("status"),
                "latency_ms": payload.get("latency_ms"),
                "attempts": payload.get("attempts", 1),
                "validation_errors": payload.get("validation_errors") or [],
                "repaired": payload.get("repaired", False),
                "repair_actions": payload.get("repair_actions") or [],
            }
        if name == "ProfileExtractorAgent":
            return {
                "profile_patch": payload.get("profile_patch") or {},
                "corrections": payload.get("corrections") or [],
                "ignored_candidates": payload.get("ignored_candidates") or [],
                "model_used": payload.get("model_used", False),
            }
        if name == "MemoryAgent":
            return {"memory_ids": payload.get("written") or []}
        if name == "MemoryVerifier":
            return {
                "passed": payload.get("passed"),
                "accepted_count": payload.get("accepted_count", 0),
                "rejected_count": payload.get("rejected_count", 0),
                "issue_count": payload.get("issue_count", len(payload.get("issues") or [])),
                "repair_actions": payload.get("repair_actions") or [],
                "issues": payload.get("issues") or [],
            }
        if name == "IntentRouter":
            return {
                "onboarding_complete": payload.get("onboarding_complete"),
                "missing_slots": payload.get("missing_slots") or [],
            }
        if name == "ContextBuilder":
            memory_context = payload.get("memory_context") or {}
            knowledge_context = payload.get("knowledge_context") or {}
            return {
                "intent": payload.get("intent"),
                "context_summary": payload.get("context_summary"),
                "relevant_memory_count": len(memory_context.get("relevant_memories") or []),
                "active_risk_count": len(memory_context.get("active_risk_notes") or []),
                "knowledge_debug": knowledge_context.get("debug", {}),
            }
        if name == "KnowledgeRetrieval":
            return {
                "intent": payload.get("intent"),
                "matched_knowledge_ids": payload.get("matched_knowledge_ids") or [],
                "matched_case_ids": payload.get("matched_case_ids") or [],
                "rag_used_for": payload.get("rag_used_for") or [],
            }
        if name == "DecisionRules":
            return {
                "matched_rule_ids": payload.get("matched_rule_ids") or [],
                "rules": [
                    {
                        "rule_id": item.get("rule_id"),
                        "title": item.get("title"),
                        "action": item.get("action"),
                    }
                    for item in (payload.get("rules") or [])[:5]
                    if isinstance(item, dict)
                ],
            }
        if name == "TemplateSelector":
            return {
                "matched_template_ids": payload.get("matched_template_ids") or [],
                "templates": [
                    {
                        "template_id": item.get("template_id"),
                        "name": item.get("name"),
                        "goal": item.get("goal"),
                    }
                    for item in (payload.get("templates") or [])[:5]
                    if isinstance(item, dict)
                ],
            }
        if name == "CoachLLM":
            return {
                "mode": payload.get("mode") or "coaching",
                "live_model": payload.get("live_model"),
                "safety": payload.get("safety", False),
                "response_chars": payload.get("response_chars"),
            }
        if name == "ResponsePersisted":
            return {"response_chars": payload.get("response_chars")}
        if name == "GuardrailCheck":
            return {
                "action": payload.get("action"),
                "flag_count": payload.get("flag_count"),
                "flags": payload.get("flags") or [],
            }
        if name in {"PlanVerifier", "ResponseVerifier"}:
            return {
                "passed": payload.get("passed"),
                "issue_count": payload.get("issue_count"),
                "repair_actions": payload.get("repair_actions") or [],
                "issues": payload.get("issues") or [],
            }
        if name in {"PlanRepair", "ResponseRepair"}:
            return {
                "repaired": payload.get("repaired"),
                "repair_actions": payload.get("repair_actions") or [],
            }
        return self._truncate_trace_payload(payload)
    async def _live_onboarding_reply_stream(
        self,
        profile: models.UserProfile,
        missing_slots: list[str],
        message: str,
    ):
        fallback = self._onboarding_reply(profile, missing_slots)
        if not self.model_provider.has_live_model():
            async for chunk in self._stream_static_text(fallback):
                yield chunk
            return

        system_prompt = registry.get("coach_onboarding_stream")
        user_prompt = json.dumps(
            {
                "user_message": message,
                "known_profile": {
                    "age": profile.age,
                    "height_cm": profile.height_cm,
                    "weight_kg": profile.weight_kg,
                    "goal": profile.goal,
                    "experience_level": profile.experience_level,
                    "equipment_available": profile.equipment_available,
                    "injuries": profile.injuries,
                    "dietary_preferences": profile.dietary_preferences,
                },
                "missing_slots": missing_slots,
                "fallback_question": fallback,
            },
            ensure_ascii=False,
        )
        tracker = track_llm_call(model=self.model_provider.settings.chat_model)
        try:
            async for chunk in self.model_provider.stream_coach_reply(
                system_prompt, user_prompt
            ):
                yield chunk
            tracker.success()
        except Exception as exc:
            tracker.failure()
            async for chunk in self._stream_static_text(
                registry.get("error_model_call_onboarding").format(fallback=fallback)
            ):
                yield chunk

    async def _coaching_reply(
        self,
        user_id: uuid.UUID,
        message: str,
        context_packet: dict[str, Any] | None = None,
    ) -> str:
        profile = self._get_or_create_profile(user_id)
        plan = self.get_active_plan(user_id)
        context_packet = context_packet or ContextBuilder(self.db, self.model_provider).build_context_packet(user_id, message)
        today_plan = None
        allow_plan_content = self._allow_plan_content_for_context(context_packet)
        if allow_plan_content and plan and isinstance(plan.plan_json, dict):
            training_days = plan.plan_json.get("training_days") or []
            if training_days:
                today_plan = training_days[0]
        _, feedback_debug = get_adaptive_system_prompt(
            self.db, user_id, "coach_coaching_reply", registry
        )
        system_prompt = registry.get("coach_coaching_reply")
        user_prompt = json.dumps(
            {
                "user_message": message,
                "canonical_profile": self._profile_payload(profile),
                "today_plan": today_plan,
                "context_packet": context_packet,
                "current_request_policy": context_packet.get("current_request_policy", {}),
                "memory_policy": (
                    "Use the context_packet as the only retrieved memory context. "
                    "Do not request or assume full history. Prioritize active_risk_notes. "
                    "Treat previous user commands as completed or historical unless the current user_message repeats them."
                ),
                "response_policy": (
                    "Answer only the current user_message. Do not append, continue, or regenerate a training plan "
                    "unless current_request_policy.should_generate_plan or current_request_policy.allow_plan_content is true."
                ),
            },
            ensure_ascii=False,
        )
        # Check semantic cache first
        cached = self.cache.get(system_prompt, user_prompt)
        if cached is not None:
            return cached

        tracker = track_llm_call(model=self.model_provider.settings.chat_model)
        try:
            live_reply = await self.model_provider.coach_reply(system_prompt, user_prompt)
            tracker.success()
        except Exception as exc:
            tracker.failure()
            if self.model_provider.has_live_model():
                return self._local_coaching_fallback(profile, plan, context_packet)
            live_reply = None
        if live_reply:
            self.cache.set(system_prompt, user_prompt, live_reply, model_name=self.model_provider.settings.chat_model)
            return live_reply

        return self._local_coaching_fallback(profile, plan, context_packet)

    async def _coaching_reply_stream(
        self,
        user_id: uuid.UUID,
        message: str,
        context_packet: dict[str, Any] | None = None,
    ):
        profile = self._get_or_create_profile(user_id)
        plan = self.get_active_plan(user_id)
        context_packet = context_packet or ContextBuilder(self.db, self.model_provider).build_context_packet(user_id, message)
        today_plan = None
        allow_plan_content = self._allow_plan_content_for_context(context_packet)
        if allow_plan_content and plan and isinstance(plan.plan_json, dict):
            training_days = plan.plan_json.get("training_days") or []
            if training_days:
                today_plan = training_days[0]
        _, feedback_debug = get_adaptive_system_prompt(
            self.db, user_id, "coach_coaching_reply_stream", registry
        )
        system_prompt = registry.get("coach_coaching_reply_stream")
        user_prompt = json.dumps(
            {
                "user_message": message,
                "canonical_profile": self._profile_payload(profile),
                "today_plan": today_plan,
                "context_packet": context_packet,
                "current_request_policy": context_packet.get("current_request_policy", {}),
                "memory_policy": (
                    "Use the context_packet as the only retrieved memory context. "
                    "Do not request or assume full history. Prioritize active_risk_notes. "
                    "Treat previous user commands as completed or historical unless the current user_message repeats them."
                ),
                "response_policy": (
                    "Answer only the current user_message. Do not append, continue, or regenerate a training plan "
                    "unless current_request_policy.should_generate_plan or current_request_policy.allow_plan_content is true."
                ),
            },
            ensure_ascii=False,
        )
        if not self.model_provider.has_live_model():
            async for chunk in self._stream_static_text(
                self._local_coaching_fallback(profile, plan, context_packet)
            ):
                yield chunk
            return
        # Check semantic cache first
        cached = self.cache.get(system_prompt, user_prompt)
        if cached is not None:
            async for chunk in self._stream_static_text(cached):
                yield chunk
            return

        tracker = track_llm_call(model=self.model_provider.settings.chat_model)
        try:
            chunks: list[str] = []
            async for chunk in self.model_provider.stream_coach_reply(system_prompt, user_prompt):
                chunks.append(str(chunk))
                yield chunk
            full_reply = "".join(chunks)
            self.cache.set(system_prompt, user_prompt, full_reply, model_name=self.model_provider.settings.chat_model)
            tracker.success()
        except Exception:
            tracker.failure()
            async for chunk in self._stream_static_text(
                self._local_coaching_fallback(profile, plan, context_packet)
            ):
                yield chunk

    def _local_coaching_fallback(
        self,
        profile: models.UserProfile,
        plan: models.TrainingPlan | None,
        context_packet: dict[str, Any] | None = None,
    ) -> str:
        if not self._allow_plan_content_for_context(context_packet):
            return (
                "我会把前面关于训练计划的内容当作历史背景，不会在这轮继续执行旧指令。"
                "你当前这条消息更像普通问答或澄清问题，我会优先围绕当前问题回答；"
                "如果你确实想重新生成或调整训练计划，请在当前消息里明确说“帮我生成计划”或“调整计划”。"
            )

        if not plan or not isinstance(plan.plan_json, dict):
            missing = self.missing_onboarding_slots(profile)
            if missing:
                return self._onboarding_reply(profile, missing)
            return "你的档案已经建好。告诉我今天的睡眠、疲劳、酸痛和训练时间，我会给你安排当天训练。"

        training_days = plan.plan_json.get("training_days") or []
        today_plan = training_days[0] if training_days else {}
        exercises = today_plan.get("exercises") or []
        exercise_lines = []
        for item in exercises[:5]:
            name = item.get("name", "主训练动作")
            sets = item.get("sets", 3)
            reps = item.get("reps", "8-12")
            rest = item.get("rest_seconds", 90)
            exercise_lines.append(f"- {name}: {sets}组 x {reps}次，休息约{rest}秒")

        calories = profile.target_calories or 2100
        protein = profile.target_protein_g or round((profile.weight_kg or 80) * 2)
        carb = profile.target_carbs_g or 200
        fat = profile.target_fat_g or 60
        equipment = "、".join(profile.equipment_available or ["健身房器械"])
        medical_note = ""
        memories = (context_packet or {}).get("memory_context") or {}
        active_risk_notes = memories.get("active_risk_notes") or []
        if active_risk_notes:
            medical_note = "\n\n健康边界：你有需要被训练强度照顾的健康背景，今天不做冲刺、极限重量或长时间高心率训练；如有胸闷、头晕、心悸、刺痛等症状，停止训练并按医生建议处理。"

        return (
            "今天先按“中等强度力量 + 稳定减脂饮食”执行。\n\n"
            f"训练目标：{profile.goal or '维持训练节奏'}；器械环境：{equipment}；强度控制在RPE 7左右，保留1-3次余力。\n\n"
            "今日训练：\n"
            "1. 热身：跑步机或椭圆机8-10分钟，再做肩胛、髋、踝动态活动。\n"
            "2. 主训练：\n"
            f"{chr(10).join(exercise_lines) if exercise_lines else '- 深蹲模式、推、拉、髋铰链、核心各3组，保持动作标准。'}\n"
            "3. 收尾：低强度有氧15-20分钟，心率保持能说完整句子的程度。\n\n"
            "饮食执行：\n"
            f"- 今日热量先控制在约{calories} kcal。\n"
            f"- 蛋白质约{protein}g，碳水约{carb}g，脂肪约{fat}g。\n"
            "- 如果外食，优先选一份瘦肉/鱼/蛋/豆制品 + 一份主食 + 两份蔬菜，少油少糖饮料。\n\n"
            "训练后告诉我：完成度、每个主动作重量/次数、RPE、酸痛、睡眠和今天饮食执行，我会据此调整下一次训练。"
            f"{medical_note}"
        )

    def _memory_payload(self, memories: list[models.LongTermMemory]) -> list[dict[str, Any]]:
        return [
            {
                "type": memory.memory_type,
                "content": memory.content,
                "importance": memory.importance,
                "confidence": memory.confidence,
                "source": memory.source,
                "metadata": memory.memory_metadata or {},
            }
            for memory in memories
        ]

    def _profile_payload(self, profile: models.UserProfile) -> dict[str, Any]:
        return {
            "age": profile.age,
            "sex": profile.sex,
            "height_cm": profile.height_cm,
            "weight_kg": profile.weight_kg,
            "activity_level": profile.activity_level,
            "goal": profile.goal,
            "experience_level": profile.experience_level,
            "workout_frequency": profile.workout_frequency,
            "workout_duration": profile.workout_duration,
            "dietary_preferences": profile.dietary_preferences or [],
            "allergies": profile.allergies or [],
            "equipment_available": profile.equipment_available or [],
            "injuries": profile.injuries or [],
            "target_calories": profile.target_calories,
            "target_protein_g": profile.target_protein_g,
            "target_carbs_g": profile.target_carbs_g,
            "target_fat_g": profile.target_fat_g,
        }

    def _onboarding_reply(
        self, profile: models.UserProfile, missing_slots: list[str]
    ) -> str:
        slot_prompts = {
            "age": "年龄",
            "height_cm": "身高",
            "weight_kg": "体重",
            "goal": "主要目标",
            "experience_level": "训练经验",
            "equipment_available": "可用器械/训练场地",
        }
        needed = ", ".join(slot_prompts[slot] for slot in missing_slots[:3])
        known = []
        if profile.target_calories:
            known.append(f"估算目标热量 {profile.target_calories} kcal")
        known_text = f" ({'; '.join(known)})" if known else ""
        return registry.get("fallback_onboarding").format(known_text=known_text, needed=needed)

    def _safety_reply(self) -> str:
        return registry.get("fallback_safety_reply")

    def _build_plan_reflection_prompt(
        self,
        plan_output: dict[str, Any],
        context_packet: dict[str, Any],
        profile: Any,
    ) -> str | None:
        plan = plan_output.get("active_plan") or {}
        if not plan:
            return None
        profile_text = json.dumps(
            self._profile_payload(profile), ensure_ascii=False, default=str
        )
        plan_text = json.dumps(plan, ensure_ascii=False, default=str)[:3000]
        return (
            "Before finalizing this plan, role-play as the user with this profile:\n"
            + profile_text
            + "\n\nReview the generated plan and identify:\n"
            "1. Any exercise that could aggravate their listed injuries\n"
            "2. Volume or intensity mismatched to their experience level\n"
            "3. Missing warmup/cooldown or safety notes\n"
            "4. Equipment requirements they don't have\n\n"
            "If you find issues, revise the plan. If the plan looks safe, "
            "confirm it's ready. Keep your response concise — list issues "
            "or say 'Plan verified: no issues found.'\n\n"
            "Generated plan:\n" + plan_text
        )

    def _contains_medical_risk(self, message: str) -> bool:
        lowered = message.lower()
        return any(
            token in lowered
            for token in [
                "sharp pain",
                "pain",
                "injury",
                "numb",
                "medication",
                "medicine",
                "thyroid",
                "hyperthyroidism",
                "chest pain",
                "palpitation",
                "\u523a\u75db",
                "\u75bc\u75db",
                "\u53d7\u4f24",
                "\u819d\u76d6\u75db",
                "\u8170\u75db",
                "\u7532\u4ea2",
                "\u7532\u72b6\u817a",
                "\u5403\u836f",
                "\u670d\u836f",
                "\u7528\u836f",
                "\u80f8\u95f7",
                "\u5934\u6655",
                "\u5fc3\u6095",
            ]
        )

    def _requires_immediate_safety_reply(self, message: str) -> bool:
        lowered = message.lower()
        acute_terms = [
            "sharp pain",
            "chest pain",
            "palpitation",
            "dizzy",
            "faint",
            "numb",
            "\u523a\u75db",
            "\u80f8\u95f7",
            "\u5934\u6655",
            "\u5fc3\u6095",
            "\u9ebb\u6728",
            "\u660f\u53a5",
        ]
        medical_question_terms = [
            "\u8981\u4e0d\u8981\u7ee7\u7eed\u7ec3",
            "\u80fd\u4e0d\u80fd\u7ee7\u7eed",
            "\u836f\u600e\u4e48\u5403",
            "\u600e\u4e48\u7528\u836f",
        ]

        for term in medical_question_terms:
            if term in lowered:
                return True

        return False

    def _build_adaptive_prompt_wrapper(self, original_builder, preferences):
        """Wrap the system prompt builder to inject learned user preferences."""
        def wrapper():
            prompt = original_builder()
            if preferences.get("behavioral_guidance"):
                parts = []
                for g in preferences["behavioral_guidance"]:
                    parts.append("- " + g)
                guidance = "\n".join(parts)
                conf_pct = str(int(preferences.get("confidence", 0) * 100))
                evidence = str(preferences.get("evidence_summary", ""))
                prompt = (
                    prompt
                    + "\n\n## Learned User Preferences (from feedback history)\n"
                    + guidance
                    + "\n\nConfidence: " + conf_pct + "%. Evidence: " + evidence
                )
            return prompt
        return wrapper

    async def _handle_chat_llm_agent(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: str,
    ) -> dict[str, Any]:
        """LLM-driven agent: LLM selects tools iteratively, host executes them."""
        started_at = datetime.utcnow()

        session = self.db.get(models.ConversationSession, session_id)
        if not session:
            raise ValueError("Conversation session not found")
        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)

        self._save_message(session.id, user.id, "user", message)

        tool_registry = self._build_chat_tool_registry(user.id, session.id, profile, message)

        agent = LLMAgent(
            db=self.db,
            model_provider=self.model_provider,
            tool_registry=tool_registry,
            user_id=user.id,
            session_id=session.id,
            profile=profile,
            message=message,
        )

        # ---- Inject learned user preferences into system prompt ----
        from fast_api.app.services.feedback_learner import (
            FeedbackCollector, PreferenceLearner, PromptEnhancer,
        )
        try:
            collector = FeedbackCollector(self.db)
            learner = PreferenceLearner(collector)
            enhancer = PromptEnhancer(learner)
            preferences = learner.learn(user.id)
            if preferences.get("behavioral_guidance"):
                agent._build_system_prompt = self._build_adaptive_prompt_wrapper(
                    agent._build_system_prompt, preferences
                )
            self.nodes_extra = getattr(agent, 'nodes', [])
        except Exception:
            pass  # Feedback enhancement is best-effort

        result = await agent.run()

        if result.error:
            assistant_message = f"I encountered an issue: {result.error}. Please try again."
        else:
            assistant_message = result.final_response

        # Apply guardrail
        guardrail_result = run_guardrails(assistant_message, user_message=message, profile=profile)
        if guardrail_result.action == GuardrailSeverity.BLOCK:
            assistant_message = guardrail_result.blocked_replacement or assistant_message

        assistant_msg = self._save_message(session.id, user.id, "assistant", assistant_message)
        if runtime_route is not None:
            result.nodes.append({"node": "RuntimeRouter", "output": runtime_route.to_dict()})

        # Persist run
        run = models.AgentRun(
            user_id=user.id,
            session_id=session.id,
            run_type="chat_llm_agent",
            status="completed",
            nodes=result.nodes,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            summary=assistant_message[:500],
        )
        self.db.add(run)
        self.db.flush()
        state_updates = {"agent_mode": "llm_driven", "iterations": result.iterations}
        if runtime_route is not None:
            state_updates["runtime_route"] = runtime_route.to_dict()
        task_service = AgentTaskStateService(self.db)
        long_term_tasks = task_service.update_from_chat_turn(
            user_id=user.id,
            message=message,
            profile=profile,
            context_packet={},
            state_updates=state_updates,
            agent_run_id=run.id,
        )
        state_updates["long_term_tasks"] = long_term_tasks
        result.nodes.append({"node": "LongTermTaskState", "output": {"tasks": long_term_tasks}})
        run.nodes = result.nodes
        task_service.record_replay_snapshot(
            agent_run=run,
            request_json={
                "session_id": str(session.id),
                "message": message,
                "message_chars": len(message),
                "agent_mode": "llm_driven",
                "runtime_route": runtime_route.to_dict() if runtime_route else None,
            },
            state_snapshot={
                "profile": self._profile_snapshot(profile),
                "state_updates": state_updates,
                "active_tasks": long_term_tasks,
            },
            tool_plan_json={
                "tool_contracts": tool_registry.list_specs(),
                "contract_issues": tool_registry.validate_contracts(),
                "llm_iterations": result.iterations,
            },
            response_snapshot={
                "assistant_message": assistant_message,
                "guardrail": result.guardrail,
                "tool_calls": result.tool_calls,
            },
            config_snapshot={
                "llm_provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
                "embedding_mode": self.model_provider.embedding_mode(),
                "agent_runtime_mode": get_settings().agent_runtime_mode,
            },
        )

        for call in result.tool_calls:
            self.db.add(models.ToolCall(
                agent_run_id=run.id,
                tool_name=call["tool_name"],
                input_json=call.get("input", {}),
                output_json=call.get("output", {}),
                latency_ms=call.get("latency_ms", 0),
                status=call.get("status", "success"),
            ))

        self.db.commit()
        self.db.refresh(run)

        return {
            "session_id": session.id,
            "user_id": user.id,
            "assistant_message": assistant_message,
            "agent_run_id": run.id,
            "feedback_message_id": assistant_msg.id,
            "onboarding_complete": not self.missing_onboarding_slots(profile),
            "missing_slots": self.missing_onboarding_slots(profile),
            "memories_written": [],
            "tool_calls": result.tool_calls,
            "state_updates": state_updates,
            "guardrail": result.guardrail,
        }
