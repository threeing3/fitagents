import inspect
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]
ToolRepairHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class ToolSchemaValidationError(ValueError):
    """Raised when a tool input or output does not match its declared schema."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    input_schema_version: str = "v1"
    output_schema_version: str = "v1"
    permission_level: str = "read"
    side_effects: bool = False
    retry_count: int = 0
    retry_backoff_ms: int = 0
    risk_level: str = "low"
    idempotency_key_fields: list[str] = field(default_factory=list)
    timeout_ms: int | None = None
    owner: str = "coach_agent"
    tags: list[str] = field(default_factory=list)

    @property
    def contract_id(self) -> str:
        payload = {
            "name": self.name,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "input_schema_version": self.input_schema_version,
            "output_schema_version": self.output_schema_version,
            "permission_level": self.permission_level,
            "side_effects": self.side_effects,
            "risk_level": self.risk_level,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "contract_id": self.contract_id,
            "input_schema_version": self.input_schema_version,
            "output_schema_version": self.output_schema_version,
            "permission_level": self.permission_level,
            "side_effects": self.side_effects,
            "retry_count": self.retry_count,
            "retry_backoff_ms": self.retry_backoff_ms,
            "risk_level": self.risk_level,
            "idempotency_key_fields": self.idempotency_key_fields,
            "timeout_ms": self.timeout_ms,
            "owner": self.owner,
            "tags": self.tags,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


@dataclass
class ToolExecutionResult:
    tool_name: str
    status: str
    latency_ms: int
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    error: str | None = None
    attempts: int = 1
    validation_errors: list[str] = field(default_factory=list)
    repaired: bool = False
    repair_actions: list[str] = field(default_factory=list)
    contract: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def to_trace(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "input_json": self.input_json,
            "output_json": self.output_json,
            "error": self.error,
            "attempts": self.attempts,
            "validation_errors": self.validation_errors,
            "repaired": self.repaired,
            "repair_actions": self.repair_actions,
            "contract": self.contract,
            "idempotency_key": self.idempotency_key,
        }


class ToolRegistry:
    """Small runtime registry for agent tools.

    This gives the coach agent a Claude Code-like tool layer without forcing a
    large framework migration. Tools are explicit, schema-described, timed, and
    logged before their outputs affect the next agent step.
    """

    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._repair_handlers: dict[str, ToolRepairHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler, repair_handler: ToolRepairHandler | None = None) -> None:
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        if repair_handler is not None:
            self._repair_handlers[spec.name] = repair_handler

    def list_specs(self) -> list[dict[str, Any]]:
        return [
            {**spec.to_contract(), "has_repair_handler": spec.name in self._repair_handlers}
            for spec in self._specs.values()
        ]

    def validate_contracts(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for spec in self._specs.values():
            if not spec.description.strip():
                issues.append({"tool_name": spec.name, "severity": "error", "issue": "missing_description"})
            if spec.side_effects and not spec.idempotency_key_fields:
                issues.append({"tool_name": spec.name, "severity": "warn", "issue": "side_effect_without_idempotency_key"})
            if spec.permission_level not in {"read", "write_candidate", "write", "admin"}:
                issues.append({"tool_name": spec.name, "severity": "error", "issue": "invalid_permission_level"})
            if spec.risk_level not in {"low", "medium", "high", "critical"}:
                issues.append({"tool_name": spec.name, "severity": "error", "issue": "invalid_risk_level"})
            if spec.retry_count > 0 and spec.side_effects:
                issues.append({"tool_name": spec.name, "severity": "warn", "issue": "side_effect_tool_should_not_retry"})
            if spec.input_schema and spec.input_schema.get("type") != "object":
                issues.append({"tool_name": spec.name, "severity": "error", "issue": "input_schema_must_be_object"})
            if spec.output_schema and spec.output_schema.get("type") != "object":
                issues.append({"tool_name": spec.name, "severity": "error", "issue": "output_schema_must_be_object"})
        return issues

    async def execute_awaiting_approval(
        self,
        name: str,
        input_json: dict[str, Any] | None = None,
        approval_manager: "ApprovalManager | None" = None,
    ) -> tuple[ToolExecutionResult, bool]:
        """Execute with approval gate. Returns (result, was_approved).

        When approval_manager is provided and the tool has side_effects:
        1. Creates a pending approval
        2. The caller must poll for approval status
        3. Only executes when approved
        """
        if name not in self._handlers:
            raise ValueError(f"Tool not registered: {name}")
        spec = self._specs[name]

        # Check if approval is needed
        needs_approval = (
            approval_manager is not None
            and approval_manager.requires_approval(name, spec.permission_level, spec.side_effects)
        )
        if needs_approval:
            from fast_api.app.services.approval_manager import summarize_tool_for_approval
            approval = approval_manager.create_approval(
                user_id=approval_manager._last_user_id,
                session_id=approval_manager._last_session_id,
                tool_name=name,
                tool_description=spec.description,
                permission_level=spec.permission_level,
                input_summary=summarize_tool_for_approval(name, input_json or {}),
            )
            return ToolExecutionResult(
                tool_name=name,
                status="awaiting_approval",
                latency_ms=0,
                input_json=input_json or {},
                output_json={"approval_id": approval.approval_id},
                error=None,
                contract=spec.to_contract(),
                idempotency_key=self._idempotency_key(spec, input_json or {}),
            ), False

        # Execute normally
        result = await self.execute(name, input_json)
        return result, True

    async def execute(self, name: str, input_json: dict[str, Any] | None = None) -> ToolExecutionResult:
        if name not in self._handlers:
            raise ValueError(f"Tool not registered: {name}")
        spec = self._specs[name]
        payload = input_json or {}
        start = time.perf_counter()
        attempts = 0
        validation_errors: list[str] = []
        repair_actions: list[str] = []
        repaired = False
        max_attempts = max(1, 1 + max(0, spec.retry_count))
        contract = spec.to_contract()
        idempotency_key = self._idempotency_key(spec, payload)

        input_errors = self._validate_schema(payload, spec.input_schema, "input")
        if input_errors:
            validation_errors.extend(input_errors)
            repaired_payload = await self._repair(
                name,
                {
                    "phase": "input_validation",
                    "tool_name": name,
                    "input_json": payload,
                    "errors": input_errors,
                },
            )
            if isinstance(repaired_payload, dict) and isinstance(repaired_payload.get("input_json"), dict):
                payload = repaired_payload["input_json"]
                repaired = True
                repair_actions.append("repair_input_schema")
                input_errors = self._validate_schema(payload, spec.input_schema, "input")
            if input_errors:
                return ToolExecutionResult(
                    tool_name=name,
                    status="schema_error",
                    latency_ms=round((time.perf_counter() - start) * 1000),
                    input_json=payload,
                    output_json={},
                    error="input schema validation failed",
                    attempts=0,
                    validation_errors=input_errors,
                    repaired=repaired,
                    repair_actions=repair_actions,
                    contract=contract,
                    idempotency_key=idempotency_key,
                )

        last_error: str | None = None
        try:
            while attempts < max_attempts:
                attempts += 1
                try:
                    result = self._handlers[name](payload)
                    if inspect.isawaitable(result):
                        result = await result
                    output_json = result if isinstance(result, dict) else {"result": result}
                    output_errors = self._validate_schema(output_json, spec.output_schema, "output")
                    if output_errors:
                        validation_errors.extend(output_errors)
                        repaired_output = await self._repair(
                            name,
                            {
                                "phase": "output_validation",
                                "tool_name": name,
                                "input_json": payload,
                                "output_json": output_json,
                                "errors": output_errors,
                            },
                        )
                        if isinstance(repaired_output, dict) and isinstance(repaired_output.get("output_json"), dict):
                            output_json = repaired_output["output_json"]
                            repaired = True
                            repair_actions.append("repair_output_schema")
                            output_errors = self._validate_schema(output_json, spec.output_schema, "output")
                    if output_errors:
                        last_error = "output schema validation failed"
                        if attempts >= max_attempts:
                            return ToolExecutionResult(
                                tool_name=name,
                                status="schema_error",
                                latency_ms=round((time.perf_counter() - start) * 1000),
                                input_json=payload,
                                output_json=output_json,
                                error=last_error,
                                attempts=attempts,
                                validation_errors=output_errors,
                                repaired=repaired,
                                repair_actions=repair_actions,
                                contract=contract,
                                idempotency_key=idempotency_key,
                            )
                        await self._sleep_backoff(spec)
                        continue
                    return ToolExecutionResult(
                        tool_name=name,
                        status="success",
                        latency_ms=round((time.perf_counter() - start) * 1000),
                        input_json=payload,
                        output_json=output_json,
                        attempts=attempts,
                        validation_errors=validation_errors,
                        repaired=repaired,
                        repair_actions=repair_actions,
                        contract=contract,
                        idempotency_key=idempotency_key,
                    )
                except Exception as exc:
                    last_error = str(exc)
                    if attempts >= max_attempts:
                        break
                    await self._sleep_backoff(spec)
            return ToolExecutionResult(
                tool_name=name,
                status="error",
                latency_ms=round((time.perf_counter() - start) * 1000),
                input_json=payload,
                output_json={},
                error=last_error or "tool execution failed",
                attempts=attempts,
                validation_errors=validation_errors,
                repaired=repaired,
                repair_actions=repair_actions,
                contract=contract,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=name,
                status="error",
                latency_ms=round((time.perf_counter() - start) * 1000),
                input_json=payload,
                output_json={},
                error=str(exc),
                attempts=max(1, attempts),
                validation_errors=validation_errors,
                repaired=repaired,
                repair_actions=repair_actions,
                contract=contract,
                idempotency_key=idempotency_key,
            )

    async def _repair(self, name: str, payload: dict[str, Any]) -> Any:
        handler = self._repair_handlers.get(name)
        if handler is None:
            return None
        result = handler(payload)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _sleep_backoff(self, spec: ToolSpec) -> None:
        if spec.retry_backoff_ms <= 0:
            return
        import asyncio

        await asyncio.sleep(spec.retry_backoff_ms / 1000)

    def _validate_schema(self, payload: dict[str, Any], schema: dict[str, Any], label: str) -> list[str]:
        if not schema:
            return []
        errors: list[str] = []
        if schema.get("type") == "object" and not isinstance(payload, dict):
            return [f"{label}: expected object"]
        required = schema.get("required") or []
        for key in required:
            if key not in payload or payload.get(key) is None:
                errors.append(f"{label}.{key}: required")
        properties = schema.get("properties") or {}
        for key, rules in properties.items():
            if key not in payload or payload.get(key) is None:
                continue
            errors.extend(self._validate_value(payload.get(key), rules or {}, f"{label}.{key}"))
        return errors

    def _validate_value(self, value: Any, rules: dict[str, Any], path: str) -> list[str]:
        expected_type = rules.get("type")
        errors: list[str] = []
        if expected_type and not self._matches_type(value, expected_type):
            errors.append(f"{path}: expected {expected_type}")
            return errors
        if "enum" in rules and value not in rules["enum"]:
            errors.append(f"{path}: expected one of {rules['enum']}")
        if isinstance(value, (int, float)):
            if "minimum" in rules and value < rules["minimum"]:
                errors.append(f"{path}: below minimum {rules['minimum']}")
            if "maximum" in rules and value > rules["maximum"]:
                errors.append(f"{path}: above maximum {rules['maximum']}")
        if isinstance(value, list) and "items" in rules:
            for index, item in enumerate(value):
                errors.extend(self._validate_value(item, rules["items"], f"{path}[{index}]"))
        if isinstance(value, dict) and rules.get("properties"):
            nested_schema = {
                "type": "object",
                "required": rules.get("required", []),
                "properties": rules.get("properties", {}),
            }
            errors.extend(self._validate_schema(value, nested_schema, path))
        return errors

    def _matches_type(self, value: Any, expected_type: str | list[str]) -> bool:
        if isinstance(expected_type, list):
            return any(self._matches_type(value, item) for item in expected_type)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "null":
            return value is None
        return True

    def _idempotency_key(self, spec: ToolSpec, payload: dict[str, Any]) -> str | None:
        if not spec.idempotency_key_fields:
            return None
        material = {
            field: payload.get(field)
            for field in spec.idempotency_key_fields
            if field in payload
        }
        if not material:
            return None
        raw = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(f"{spec.name}:{raw}".encode("utf-8")).hexdigest()[:24]


@dataclass
class TaskStep:
    step_id: str
    name: str
    status: str = "pending"
    tool_name: str | None = None
    reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    latency_ms: int = 0
    output_summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class PlannedStep:
    key: str
    name: str
    tool_name: str | None = None
    reason: str | None = None
    required: bool = True
    stage: str = "execute"
    condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "required": self.required,
            "stage": self.stage,
            "condition": self.condition,
        }


@dataclass
class AgentExecutionPlan:
    objective: str
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy: str = "current_message_first"
    intent: str = "general_chat"
    steps: list[PlannedStep] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "strategy": self.strategy,
            "intent": self.intent,
            "steps": [step.to_dict() for step in self.steps],
            "assumptions": self.assumptions,
        }


class AgentPlanner:
    """Build an explicit execution plan for the current user turn."""

    def plan_chat_turn(self, message: str, available_tools: list[dict[str, Any]]) -> AgentExecutionPlan:
        tool_names = {tool.get("name") for tool in available_tools}
        intent = self.classify_intent(message)

        def include(tool_name: str) -> bool:
            return tool_name in tool_names

        steps = [
            PlannedStep(
                "profile_extract",
                "Extract profile patch and corrections",
                "profile.extract",
                "Keep canonical profile aligned with the current user message.",
                stage="planner",
            ),
            PlannedStep(
                "memory_verify",
                "Verify long-term memory candidates",
                "memory.verify",
                "Prevent memory pollution before candidates become durable.",
                stage="verifier",
            ),
            PlannedStep(
                "memory_write",
                "Write verified long-term memories",
                "memory.write",
                "Persist only useful stable facts and recent state after verification.",
                stage="executor",
            ),
            PlannedStep(
                "context_build",
                "Build intent-specific context packet",
                "context.build",
                "Use current intent to prevent old command carry-over.",
                stage="executor",
            ),
            PlannedStep(
                "plan_decision",
                "Decide whether plan generation is allowed",
                "plan.decide",
                "Generate a plan only if the current message explicitly asks for one.",
                stage="planner",
            ),
        ]
        if intent == "training_plan":
            steps.extend(
                [
                    PlannedStep(
                        "plan_generate",
                        "Generate first active plan when allowed",
                        "plan.generate",
                        "The current message is a plan request and no active plan may exist.",
                        required=False,
                        stage="executor",
                        condition="active_plan_missing_and_current_message_requests_plan",
                    ),
                    PlannedStep(
                        "plan_verify",
                        "Verify generated plan constraints",
                        "plan.verify",
                        "Generated plans must pass schema and safety checks before use.",
                        required=False,
                        stage="verifier",
                        condition="plan_generated",
                    ),
                    PlannedStep(
                        "plan_repair",
                        "Repair generated plan constraints",
                        "plan.repair",
                        "Apply deterministic repairs for fixable plan verifier findings.",
                        required=False,
                        stage="repair",
                        condition="plan_verifier_has_repair_actions",
                    ),
                ]
            )
        steps.extend(
            [
            PlannedStep(
                "coach_reply",
                "Generate coach response",
                "coach.reply",
                "Answer the current user message with retrieved context.",
                stage="executor",
            ),
            PlannedStep(
                "response_verify",
                "Verify coach response constraints",
                "response.verify",
                "Check whether the final response follows current-message policy and safety context.",
                stage="verifier",
            ),
            PlannedStep(
                "response_repair",
                "Repair coach response constraints",
                "response.repair",
                "Append deterministic repair text when verifier finds fixable issues.",
                required=False,
                stage="repair",
                condition="response_verifier_has_repair_actions",
            ),
            PlannedStep(
                "guardrail",
                "Run safety guardrail",
                "guardrail.check",
                "Check medical, injury, and unsafe dieting boundaries.",
                stage="verifier",
            ),
            PlannedStep(
                "persist",
                "Persist response and trace",
                "response.persist",
                "Save assistant message, agent run, tool calls, and readable logs.",
                stage="executor",
            ),
            ]
        )
        steps = [step for step in steps if not step.tool_name or include(step.tool_name) or step.tool_name == "coach.reply"]
        return AgentExecutionPlan(
            objective=message,
            intent=intent,
            steps=steps,
            assumptions=[
                "The current user message is the only active instruction.",
                "Conversation history and memory are background context, not commands to continue automatically.",
                "Verifier and repair steps run after execution outputs are available.",
            ],
        )

    def classify_intent(self, message: str) -> str:
        lowered = message.lower()
        if any(term in lowered for term in ["胸闷", "头晕", "呼吸困难", "刺痛", "甲亢", "甲状腺", "受伤", "pain", "injury", "dizzy"]):
            return "injury_or_risk"
        if any(term in lowered for term in ["今天练什么", "今天应该练什么", "训练计划", "健身计划", "生成计划", "制定计划", "workout plan", "training plan"]):
            return "training_plan"
        if any(term in lowered for term in ["kg", "公斤", "组", "次数", "rpe", "卧推", "深蹲", "硬拉", "练了", "做完", "bench", "squat", "deadlift"]):
            return "training_log"
        if any(term in lowered for term in ["吃", "热量", "蛋白", "碳水", "脂肪", "外卖", "外食", "calorie", "protein"]):
            return "nutrition_advice"
        if any(term in lowered for term in ["睡", "疲劳", "酸痛", "恢复", "压力", "心率", "recovery", "sleep", "tired"]):
            return "recovery_check"
        if any(term in lowered for term in ["你记得", "我的档案", "记忆", "memory", "profile"]):
            return "memory_query"
        return "general_chat"


@dataclass
class AgentExecutorResult:
    result: ToolExecutionResult
    started_event: dict[str, Any]
    completed_event: dict[str, Any]


class AgentExecutor:
    """Execute registered tools while updating the task timeline."""

    async def execute(
        self,
        registry: ToolRegistry,
        timeline: "AgentTaskTimeline",
        step: TaskStep,
        input_json: dict[str, Any] | None = None,
    ) -> AgentExecutorResult:
        timeline.start(step)
        started_event = timeline.step_event(step)
        result = await registry.execute(step.tool_name or "", input_json or {})
        if result.status == "success":
            output_summary = result.output_json if isinstance(result.output_json, dict) else {"result": result.output_json}
            timeline.complete(step, output_summary, result.latency_ms)
        else:
            timeline.fail(step, result.error or "tool execution failed", result.latency_ms)
        completed_event = timeline.step_event(step)
        return AgentExecutorResult(
            result=result,
            started_event=started_event,
            completed_event=completed_event,
        )


class AgentTaskTimeline:
    """Per-run task timeline that mirrors how strong coding agents show work."""

    def __init__(self, goal: str, request_id: str | None = None):
        self.timeline_id = str(uuid.uuid4())
        self.request_id = request_id or str(uuid.uuid4())
        self.goal = goal
        self.created_at = datetime.utcnow().isoformat()
        self.steps: list[TaskStep] = []

    def add_step(self, name: str, tool_name: str | None = None, reason: str | None = None) -> TaskStep:
        step = TaskStep(step_id=str(uuid.uuid4()), name=name, tool_name=tool_name, reason=reason)
        self.steps.append(step)
        return step

    def start(self, step: TaskStep) -> None:
        step.status = "running"
        step.started_at = datetime.utcnow().isoformat()

    def complete(self, step: TaskStep, output_summary: dict[str, Any] | None = None, latency_ms: int | None = None) -> None:
        step.status = "completed"
        step.completed_at = datetime.utcnow().isoformat()
        if output_summary:
            step.output_summary = output_summary
        if latency_ms is not None:
            step.latency_ms = latency_ms

    def fail(self, step: TaskStep, error: str, latency_ms: int | None = None) -> None:
        step.status = "failed"
        step.completed_at = datetime.utcnow().isoformat()
        step.error = error
        if latency_ms is not None:
            step.latency_ms = latency_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeline_id": self.timeline_id,
            "request_id": self.request_id,
            "goal": self.goal,
            "created_at": self.created_at,
            "steps": [step.__dict__ for step in self.steps],
        }

    def step_event(self, step: TaskStep) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "timeline_id": self.timeline_id,
            "name": step.name,
            "status": step.status,
            "tool_name": step.tool_name,
            "reason": step.reason,
            "latency_ms": step.latency_ms,
            "output_summary": step.output_summary,
            "error": step.error,
        }
