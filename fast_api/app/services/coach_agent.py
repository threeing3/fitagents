import json
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
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
from fast_api.app.services.decision_logger import DecisionLogger
from fast_api.app.core.guardrails import run_guardrails, Severity as GuardrailSeverity
from fast_api.app.core.prompts import registry
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService, KNOWLEDGE_DIR
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.core.metrics import track_llm_call
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.semantic_cache import SemanticCacheService


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
        started_at = datetime.utcnow()
        nodes: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        memories_written: list[str] = []
        state_updates: dict[str, Any] = {}

        session = self.db.get(models.ConversationSession, session_id)
        if not session:
            raise ValueError("Conversation session not found")
        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)
        run_logger = AgentRunLogger("chat", user.id, session.id)
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

        if self._requires_immediate_safety_reply(message):
            assistant_message = self._safety_reply()
            nodes.append(run_logger.node("CoachLLM", time.perf_counter(), {"safety": True, "mode": "static_safety"}))
        elif not onboarding_complete:
            node_start = time.perf_counter()
            assistant_message = await self._live_onboarding_reply(profile, missing_slots, message)
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
            active_plan = self.get_active_plan(user.id)
            if active_plan is None:
                plan_result = self.generate_plan(PlanGenerateRequest(user_id=user.id))
                state_updates["generated_plan_id"] = str(plan_result.id)
                tool_calls.append(
                    {
                        "tool_name": "generate_training_plan",
                        "status": "success",
                        "output": {"plan_id": str(plan_result.id)},
                    }
                )
            node_start = time.perf_counter()
            context_packet = ContextBuilder(self.db, self.model_provider).build_context_packet(user.id, message)
            nodes.append(run_logger.node("ContextBuilder", node_start, context_packet))
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
            node_start = time.perf_counter()
            assistant_message = await self._coaching_reply(user.id, message, context_packet)
            nodes.append(
                run_logger.node(
                    "CoachLLM",
                    node_start,
                    {
                        "safety": False,
                        "live_model": self.model_provider.has_live_model(),
                        "response_chars": len(assistant_message),
                    },
                )
            )

        # ---- Safety guardrail check ----
        guardrail_result = run_guardrails(assistant_message, user_message=message, profile=profile)
        nodes.append(run_logger.event("GuardrailCheck", {
            "action": guardrail_result.action.value,
            "flag_count": len(guardrail_result.flags),
            "flags": [{"rule_id": f.rule_id, "severity": f.severity.value, "category": f.category} for f in guardrail_result.flags],
        }))
        if guardrail_result.action == GuardrailSeverity.BLOCK:
            assistant_message = guardrail_result.blocked_replacement or assistant_message

        assistant_msg = self._save_message(session.id, user.id, "assistant", assistant_message)
        nodes.append(run_logger.event("ResponsePersisted", {"response_chars": len(assistant_message)}))

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

        for call in tool_calls:
            self.db.add(
                models.ToolCall(
                    agent_run_id=run.id,
                    tool_name=call["tool_name"],
                    output_json=call.get("output", {}),
                    status=call.get("status", "success"),
                )
            )

        self.db.commit()
        self.db.refresh(run)
        log_path = run_logger.write_run_log(run.id, "completed", assistant_message[:500])
        run.log_path = log_path
        self.db.commit()
        state_updates["agent_log_path"] = log_path

        return {
            "session_id": session.id,
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
                "action": guardrail_result.action.value,
                "passed": guardrail_result.passed,
                "flags": [{"rule_id": f.rule_id, "severity": f.severity.value, "category": f.category, "message": f.message} for f in guardrail_result.flags],
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
                active_plan = self.get_active_plan(user.id)
                if active_plan is None:
                    plan_result = self.generate_plan(PlanGenerateRequest(user_id=user.id))
                    state_updates["generated_plan_id"] = str(plan_result.id)
                    tool_calls.append(
                        {
                            "tool_name": "generate_training_plan",
                            "status": "success",
                            "output": {"plan_id": str(plan_result.id)},
                        }
                    )
                node_start = time.perf_counter()
                context_packet = ContextBuilder(self.db, self.model_provider).build_context_packet(user.id, message)
                nodes.append(run_logger.node("ContextBuilder", node_start, context_packet))
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
        for call in tool_calls:
            self.db.add(
                models.ToolCall(
                    agent_run_id=run.id,
                    tool_name=call["tool_name"],
                    output_json=call.get("output", {}),
                    status=call.get("status", "success"),
                )
            )
        self.db.commit()
        run.log_path = run_logger.write_run_log(run.id, "completed", assistant_message[:500])
        self.db.commit()

    async def stream_chat_events(
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

        def event(event_type: str, **payload: Any) -> str:
            return json.dumps({"type": event_type, **payload}, ensure_ascii=False, default=str) + "\n"

        def step_summary(name: str, payload: dict[str, Any]) -> str:
            if name == "ProfileExtractorAgent":
                patch = payload.get("profile_patch") or {}
                corrections = payload.get("corrections") or []
                if patch or corrections:
                    return f"档案更新 {len(patch)} 项，纠错 {len(corrections)} 项"
                return "未发现新的档案字段"
            if name == "MemoryAgent":
                return f"写入长期记忆 {len(payload.get('written') or [])} 条"
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

        session = self.db.get(models.ConversationSession, session_id)
        if not session:
            yield event("error", message="Conversation session not found")
            return

        user = self.ensure_user(user_id)
        profile = self._get_or_create_profile(user.id)
        run_logger = AgentRunLogger("chat_stream", user.id, session.id)
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

        self._save_message(session.id, user.id, "user", message)

        node_start = time.perf_counter()
        yield event("status", text="正在抽取档案字段和纠错信息")
        extraction = await self.profile_extractor_agent(profile, message)
        if extraction["profile_patch"] or extraction["corrections"]:
            self._apply_profile_extraction(profile, extraction)
            self._refresh_macro_targets(profile)
            state_updates["profile_updates"] = extraction["profile_patch"]
            state_updates["corrections"] = extraction["corrections"]
        node = run_logger.node("ProfileExtractorAgent", node_start, extraction, {"message": message[:240]})
        nodes.append(node)
        yield step_event("ProfileExtractorAgent", node, extraction)

        node_start = time.perf_counter()
        yield event("status", text="正在写入长期记忆")
        memories_written = self.write_memories_from_message(user.id, message, extraction)
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

        try:
            if self._requires_immediate_safety_reply(message):
                node_start = time.perf_counter()
                yield event("status", text="正在触发安全边界回复")
                async for chunk in self._stream_static_text(self._safety_reply()):
                    chunks.append(chunk)
                    yield event("answer_delta", text=chunk)
                coach_payload = {"safety": True, "mode": "static_safety"}
                node = run_logger.node("CoachLLM", node_start, coach_payload)
                nodes.append(node)
                yield step_event("CoachLLM", node, coach_payload)
            elif not onboarding_complete:
                node_start = time.perf_counter()
                yield event("status", text="正在生成建档追问")
                async for chunk in self._live_onboarding_reply_stream(profile, missing_slots, message):
                    chunks.append(chunk)
                    yield event("answer_delta", text=chunk)
                coach_payload = {
                    "mode": "onboarding",
                    "live_model": self.model_provider.has_live_model(),
                    "missing_slots": missing_slots,
                }
                node = run_logger.node("CoachLLM", node_start, coach_payload)
                nodes.append(node)
                yield step_event("CoachLLM", node, coach_payload)
            else:
                active_plan = self.get_active_plan(user.id)
                if active_plan is None:
                    yield event("status", text="没有活跃计划，正在生成第一版训练计划")
                    plan_result = self.generate_plan(PlanGenerateRequest(user_id=user.id))
                    state_updates["generated_plan_id"] = str(plan_result.id)
                    tool_call = {
                        "tool_name": "generate_training_plan",
                        "status": "success",
                        "output": {"plan_id": str(plan_result.id)},
                    }
                    tool_calls.append(tool_call)
                    yield event(
                        "tool_call",
                        name=tool_call["tool_name"],
                        status="success",
                        summary=f"生成训练计划 {plan_result.id}",
                        metadata=tool_call["output"],
                    )

                node_start = time.perf_counter()
                yield event("status", text="正在构建上下文包：档案、长期记忆、知识和规则")
                context_packet = ContextBuilder(self.db, self.model_provider).build_context_packet(user.id, message)
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
                node_start = time.perf_counter()
                yield event("status", text="正在生成最终教练回复")
                async for chunk in self._coaching_reply_stream(user.id, message, context_packet):
                    chunks.append(chunk)
                    yield event("answer_delta", text=chunk)
                coach_payload = {
                    "safety": False,
                    "live_model": self.model_provider.has_live_model(),
                    "response_chars": len("".join(chunks)),
                }
                node = run_logger.node("CoachLLM", node_start, coach_payload)
                nodes.append(node)
                yield step_event("CoachLLM", node, coach_payload)
        except Exception as exc:
            error_text = f"\n\n{self._model_call_error_message(exc)} 请稍后重试。"
            chunks.append(error_text)
            nodes.append(run_logger.event("RuntimeError", {"error": str(exc)}))
            yield event("error", message=str(exc), summary="Agent 运行时发生错误")
            yield event("answer_delta", text=error_text)

        assistant_message = "".join(chunks).strip()
        if not assistant_message:
            assistant_message = registry.get("error_coach_stream_empty")
            yield event("answer_delta", text=assistant_message)

        # ---- Safety guardrail check ----
        guardrail_result = run_guardrails(assistant_message, user_message=message, profile=profile)
        guardrail_payload = {
            "action": guardrail_result.action.value,
            "flag_count": len(guardrail_result.flags),
            "flags": [{"rule_id": f.rule_id, "severity": f.severity.value, "category": f.category} for f in guardrail_result.flags],
        }
        guardrail_node = run_logger.event("GuardrailCheck", guardrail_payload)
        nodes.append(guardrail_node)
        yield step_event("GuardrailCheck", guardrail_node, guardrail_payload)
        if guardrail_result.action == GuardrailSeverity.BLOCK:
            assistant_message = guardrail_result.blocked_replacement or assistant_message
            yield event("guardrail_block", replacement=assistant_message[:200])

        self._save_message(session.id, user.id, "assistant", assistant_message)
        response_payload = {"response_chars": len(assistant_message)}
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
                    output_json=call.get("output", {}),
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

        self.db.commit()
        return {
            "status": "recorded",
            "checkin_id": str(checkin.id),
            "auto_adjusted": auto_adjusted,
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
            "tool_calls": [self._model_dict(call) for call in calls],
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }

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
        msg = models.ChatMessage(
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
        )
        self.db.add(msg)
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

        weight = self._first_number(
            text,
            [
                r"weight\s*[:=]?\s*(\d{2,3}(?:\.\d+)?)\s*kg",
                r"体重\s*[：:=\s\-]*\s*(\d{2,3}(?:\.\d+)?)\s*kg",
                r"(\d{2,3}(?:\.\d+)?)\s*(?:kg|公斤)",
            ],
        )
        if weight:
            updates["weight_kg"] = float(weight)

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
    ) -> list[str]:
        written = []
        extraction = extraction or self._rule_profile_extraction(message)
        for correction in extraction.get("corrections", []):
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
                    memory_metadata=correction,
                    confidence=0.95,
                )
            )

        for candidate in self._memory_candidates_from_message(message, extraction):
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

    def _public_trace_metadata(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "RequestReceived":
            return {
                "provider": payload.get("provider"),
                "chat_model": payload.get("chat_model"),
                "embedding_mode": payload.get("embedding_mode"),
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
        return {}

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
        if plan and isinstance(plan.plan_json, dict):
            training_days = plan.plan_json.get("training_days") or []
            if training_days:
                today_plan = training_days[0]
        system_prompt = registry.get("coach_coaching_reply")
        user_prompt = json.dumps(
            {
                "user_message": message,
                "canonical_profile": self._profile_payload(profile),
                "today_plan": today_plan,
                "context_packet": context_packet,
                "memory_policy": (
                    "Use the context_packet as the only retrieved memory context. "
                    "Do not request or assume full history. Prioritize active_risk_notes."
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
        if plan and isinstance(plan.plan_json, dict):
            training_days = plan.plan_json.get("training_days") or []
            if training_days:
                today_plan = training_days[0]
        system_prompt = registry.get("coach_coaching_reply_stream")
        user_prompt = json.dumps(
            {
                "user_message": message,
                "canonical_profile": self._profile_payload(profile),
                "today_plan": today_plan,
                "context_packet": context_packet,
                "memory_policy": (
                    "Use the context_packet as the only retrieved memory context. "
                    "Do not request or assume full history. Prioritize active_risk_notes."
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
