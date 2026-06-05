"""
LLM-driven Agent Service — Claude Code architecture pattern.

Instead of a code-driven fixed pipeline, this agent gives the LLM a system prompt
listing available tools, and the LLM iteratively picks tools and observes results
until it decides to produce a final response.

Architecture:
  1. Build system prompt with tool definitions (name, description, schema, usage hints)
  2. Send [system_prompt, user_message] to LLM
  3. Parse LLM response: look for <tool_call> JSON blocks
  4. If tool_call found: execute tool, inject <tool_result> back into conversation, go to 2
  5. If no tool_call: return text as final coaching response
  6. Safety: max 10 iterations, guardrail check on final output
  7. Context compaction: when conversation approaches token limit, compact early messages
"""

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from fast_api.app.db import models
from fast_api.app.core.guardrails import run_guardrails, Severity as GuardrailSeverity
from fast_api.app.core.metrics import llm_requests_total, llm_request_latency_seconds
from fast_api.app.services.agent_runtime import (
    AgentExecutor,
    AgentTaskTimeline,
    ToolRegistry,
    ToolSpec,
    TaskStep,
)
from fast_api.app.services.agent_observability import AgentRunLogger
from fast_api.app.services.context_window_manager import ContextWindowManager, estimate_tokens
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


@dataclass
class LLMAgentResult:
    final_response: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0
    guardrail: dict[str, Any] = field(default_factory=dict)
    timeline: AgentTaskTimeline | None = None
    error: str | None = None


class LLMAgentService:

    def __init__(
        self,
        db,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        profile: Any,
        message: str,
    ):
        self.db = db
        self.model_provider = model_provider
        self.tool_registry = tool_registry
        self.user_id = user_id
        self.session_id = session_id
        self.profile = profile
        self.message = message

        self.run_logger = AgentRunLogger("chat_llm_agent", user_id, session_id)
        self.timeline = AgentTaskTimeline(message, request_id=self.run_logger.request_id)
        self.executor = AgentExecutor()

        self.nodes: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.total_tokens = 0
        self.total_latency_ms = 0
        self.ctx_manager = ContextWindowManager(model_name=model_provider.settings.chat_model)

    # ----------------------------------------------------------------
    # Main entry point
    # ----------------------------------------------------------------

    async def run(self) -> LLMAgentResult:
        started_at = time.perf_counter()

        system_prompt = self._build_system_prompt()
        self.ctx_manager.set_system_prompt(system_prompt)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=self.message),
        ]

        self.nodes.append(self.run_logger.event(
            "RequestReceived",
            {
                "message_chars": len(self.message),
                "provider": self.model_provider.settings.llm_provider,
                "chat_model": self.model_provider.settings.chat_model,
            },
        ))
        self.nodes.append(self.run_logger.event(
            "ToolRegistry",
            {"tools": self.tool_registry.list_specs()},
        ))
        self.nodes.append(self.run_logger.event(
            "LLMAgentStart",
            {"architecture": "llm_driven_tool_use", "max_iterations": MAX_ITERATIONS},
        ))

        final_response = ""
        for iteration in range(1, MAX_ITERATIONS + 1):
            call_start = time.perf_counter()
            model = self.model_provider.chat_model(temperature=0.4)
            if model is None:
                return LLMAgentResult(
                    final_response="",
                    error="No live model available for LLM-driven agent",
                )

            try:
                response = await model.ainvoke(messages)
            except Exception as exc:
                logger.error("LLM call failed at iteration %d: %s", iteration, exc)
                return LLMAgentResult(
                    final_response="",
                    error="LLM call failed: " + str(exc),
                    nodes=self.nodes,
                    tool_calls=self.tool_calls,
                    iterations=iteration,
                )

            latency = round((time.perf_counter() - call_start) * 1000)
            self.total_latency_ms += latency
            response_text = str(response.content)
            self.total_tokens += len(response_text) // 3

            self.nodes.append(self.run_logger.event(
                "LLMIteration" + str(iteration),
                {
                    "iteration": iteration,
                    "response_preview": response_text[:200],
                    "has_tool_call": "<tool_call>" in response_text,
                    "latency_ms": latency,
                },
            ))

            tool_call_matches = TOOL_CALL_RE.findall(response_text)

            if not tool_call_matches:
                final_response = response_text.strip()
                self.run_logger.event(
                    "LLMAgentDecided",
                    {"decision": "final_response", "iteration": iteration},
                )
                break

            for match in tool_call_matches:
                tool_call_data = self._parse_tool_call_json(match)
                if tool_call_data is None:
                    continue

                tool_name = tool_call_data.get("name", "")
                tool_input = tool_call_data.get("input", {})

                if not tool_name:
                    continue

                specs_set = {s["name"] for s in self.tool_registry.list_specs()}
                if tool_name not in specs_set:
                    tool_result_text = json.dumps({
                        "error": "Unknown tool: " + tool_name,
                        "available_tools": sorted(specs_set),
                    })
                else:
                    step = self.timeline.add_step(
                        "llm_agent_" + tool_name, tool_name,
                        "LLM requested " + tool_name + " at iteration " + str(iteration),
                    )
                    execution = await self.executor.execute(self.tool_registry, self.timeline, step, tool_input)
                    result = execution.result

                    tool_call_record = {
                        "tool_name": tool_name,
                        "status": result.status,
                        "input": tool_input,
                        "output": result.output_json if result.status == "success" else {},
                        "latency_ms": result.latency_ms,
                        "attempts": result.attempts,
                        "iteration": iteration,
                    }
                    self.tool_calls.append(tool_call_record)
                    self.nodes.append(self.run_logger.event("ToolExecutor", tool_call_record))

                    if result.status == "success":
                        tool_result_text = json.dumps(result.output_json, ensure_ascii=False, default=str)
                    else:
                        tool_result_text = json.dumps({
                            "error": result.error or "Tool execution failed",
                            "tool_name": tool_name,
                        })

                # ---- Context compaction check ----
                total_chars = sum(len(str(m.content)) for m in messages)
                est_tokens_now = estimate_tokens(
                    json.dumps([{"role": type(m).__name__, "c": str(m.content)[:200]} for m in messages])
                )
                if est_tokens_now > self.ctx_manager.total_tokens * 0.75:
                    self.ctx_manager.compaction_count += 1
                    compacted = [messages[0], messages[1]]
                    recent = messages[2:]
                    if len(recent) > 8:
                        summary = (
                            "[Earlier context: " + str(len(recent) - 8) + " messages compacted. "
                            "The user is working with a fitness coach. Continue naturally.]"
                        )
                        compacted.append(HumanMessage(content=summary))
                        compacted.extend(recent[-8:])
                    else:
                        compacted.extend(recent)
                    messages = compacted
                    self.nodes.append(self.run_logger.event(
                        "ContextCompaction",
                        {"reason": "approaching_token_limit", "est_tokens_before": est_tokens_now},
                    ))

                # Inject tool result
                result_message = (
                    '<tool_result tool="' + tool_name + '">\n'
                    + tool_result_text + '\n'
                    + '</tool_result>\n\n'
                    + 'Continue. Call more tools if needed, or produce the final reply. '
                    + 'Available tools: ' + str(sorted(specs_set))
                )
                messages.append(HumanMessage(content=result_message))

        # ---- Final response ----
        if not final_response:
            messages.append(HumanMessage(content="Please produce the final coaching reply now. Do not call any more tools."))
            try:
                model = self.model_provider.chat_model(temperature=0.4)
                final = await model.ainvoke(messages) if model else None
                final_response = str(final.content) if final else ""
            except Exception as exc:
                logger.error("Final response generation failed: %s", exc)
                final_response = ""

        if not final_response:
            final_response = "I've reviewed your information. How can I help you with your fitness goals today?"
            self.run_logger.event("LLMAgentFallback", {"reason": "empty_final_response"})

        # ---- Guardrail ----
        guardrail_result = run_guardrails(final_response, user_message=self.message, profile=self.profile)
        guardrail = {
            "action": guardrail_result.action.value,
            "passed": guardrail_result.passed,
            "flags": [
                {"rule_id": f.rule_id, "severity": f.severity.value, "category": f.category, "message": f.message}
                for f in guardrail_result.flags
            ],
        }
        if guardrail_result.action == GuardrailSeverity.BLOCK:
            final_response = guardrail_result.blocked_replacement or final_response

        self.nodes.append(self.run_logger.event("GuardrailCheck", guardrail))

        total_ms = round((time.perf_counter() - started_at) * 1000)
        iter_count = len([n for n in self.nodes if "LLMIteration" in str(n.get("event_type", ""))])

        self.run_logger.event("LLMAgentComplete", {
            "iterations": iter_count,
            "tool_calls": len(self.tool_calls),
            "total_latency_ms": total_ms,
            "total_tokens_est": self.total_tokens,
        })

        return LLMAgentResult(
            final_response=final_response,
            tool_calls=self.tool_calls,
            nodes=self.nodes,
            iterations=len([tc for tc in self.tool_calls]),
            total_tokens=self.total_tokens,
            total_latency_ms=total_ms,
            guardrail=guardrail,
            timeline=self.timeline,
        )

    # ----------------------------------------------------------------
    # System prompt builder
    # ----------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        tools_text = self._format_tools_for_prompt()

        return (
            "You are an AI fitness coach. Your goal is to provide safe, personalized, "
            "and evidence-based fitness advice.\n\n"
            "You have access to tools that let you look up the user's profile, "
            "memories, training plan, and knowledge base. Use these tools "
            "intelligently — only call the tools you actually need for the "
            "current request.\n\n"
            "## How to use tools\n\n"
            "When you need information, output a tool call in this exact format:\n\n"
            "<tool_call>\n"
            '{"name": "tool.name", "input": {"key": "value"}}\n'
            "</tool_call>\n\n"
            "After each tool call, you will receive a <tool_result> with the "
            "tool's output. You can then call more tools or produce your final "
            "coaching reply.\n\n"
            + tools_text + "\n"
            "## Safety rules\n\n"
            "- NEVER give medical diagnoses or medication advice. If the user "
            "asks, recommend they consult a doctor.\n"
            "- NEVER suggest extreme calorie restriction (< 1200 kcal/day).\n"
            "- ALWAYS include appropriate disclaimers about consulting a "
            "healthcare professional before starting a new exercise program.\n"
            "- If the user mentions pain, injury, dizziness, or chest discomfort, "
            "prioritize safety and recommend medical evaluation.\n\n"
            "## Response guidelines\n\n"
            "- Be concise and direct. Under 300 words unless a training plan "
            "is being explained.\n"
            "- Use the user's language (Chinese or English based on their profile).\n"
            "- **IMPORTANT**: When you use tool results, naturally cite specific data "
            "in your response. For example: 'Based on your current weight of 75kg...' "
            "or 'Your active plan includes bench press at 60kg...'. This shows the user "
            "you actually looked up their information.\n"
            "- Don't make up information — only reference data you obtained from tools.\n\n"
            "Think step by step before calling tools. Do you need to know the "
            "user's profile? Their active plan? Recent workouts? Only call the "
            "tools that are necessary for this specific request.\n\n"
            "Now, respond to the user's message."
        )

    def _format_tools_for_prompt(self) -> str:
        specs = self.tool_registry.list_specs()
        lines = ["## Available tools", ""]

        usage_hints = {
            "profile.extract": "Use for: extracting or updating profile fields from user messages (age, weight, goals, injuries, equipment, etc.)",
            "memory.verify": "Use for: verifying memory candidates before writing them. Prevents incorrect information from being stored.",
            "memory.write": "Use for: persisting verified memories. Only call after memory.verify has passed.",
            "context.build": "Use for: getting the user's full context — profile, active plan, risk notes, knowledge base, relevant memories. Almost always needed before giving advice.",
            "plan.decide": "Use for: checking whether the current request should trigger plan generation. Returns intent classification and decision.",
            "plan.generate": "Use for: creating a new training plan. Only call when the user explicitly asks for a plan AND no active plan exists.",
            "plan.verify": "Use for: checking a generated plan's structure, safety, and alignment with user profile. **Always call after plan.generate before using the plan.**",
            "plan.repair": "Use for: fixing issues found by plan.verify. Applies deterministic repairs.",
            "guardrail.check": "Use for: running safety guardrails on your draft response BEFORE sending it. Must be called before the final reply.",
            "response.persist": "Use for: saving the final response, agent run, tool calls, and log. Call as the LAST step.",
        }

        for spec in specs:
            name = spec["name"]
            desc = spec.get("description", "No description available")
            hint = usage_hints.get(name, "")
            schema = spec.get("input_schema", {})
            schema_text = self._simplify_schema(schema)

            lines.append("### " + name)
            lines.append(desc)
            if hint:
                lines.append(hint)
            if schema_text:
                lines.append("Input: " + schema_text)
            lines.append("")

        return "\n".join(lines)

    def _simplify_schema(self, schema: dict[str, Any]) -> str:
        if not schema:
            return ""
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not required and not properties:
            return ""

        parts = []
        for key in required:
            prop = properties.get(key, {})
            ptype = prop.get("type", "any")
            parts.append(key + ": " + str(ptype) + " (required)")
        for key in properties:
            if key not in required:
                prop = properties[key]
                ptype = prop.get("type", "any")
                parts.append(key + ": " + str(ptype) + " (optional)")

        return "{" + ", ".join(parts) + "}"

    # ----------------------------------------------------------------
    # Tool call parser
    # ----------------------------------------------------------------

    def _parse_tool_call_json(self, text: str) -> dict[str, Any] | None:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        logger.warning("Failed to parse tool call: %s", text[:200])
        return None
