import asyncio

from fast_api.app.services.agent_runtime import (
    AgentExecutor,
    AgentPlanner,
    AgentTaskTimeline,
    ToolRegistry,
    ToolSpec,
)


def test_tool_registry_executes_and_records_metadata():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="profile.read",
            description="Read profile",
            permission_level="read",
            side_effects=False,
        ),
        lambda payload: {"user_id": payload["user_id"], "goal": "fat_loss"},
    )

    result = asyncio.run(registry.execute("profile.read", {"user_id": "u1"}))

    assert result.status == "success"
    assert result.output_json["goal"] == "fat_loss"
    assert result.latency_ms >= 0
    assert registry.list_specs()[0]["name"] == "profile.read"


def test_tool_registry_captures_tool_errors():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="bad.tool", description="Bad tool"), lambda _: 1 / 0)

    result = asyncio.run(registry.execute("bad.tool", {}))

    assert result.status == "error"
    assert "division" in (result.error or "")


def test_agent_task_timeline_tracks_step_lifecycle():
    timeline = AgentTaskTimeline("answer current user question", request_id="req-1")
    step = timeline.add_step("Build context", "context.build", "Need current intent context")

    timeline.start(step)
    timeline.complete(step, {"intent": "general_chat"}, latency_ms=12)

    payload = timeline.to_dict()
    assert payload["request_id"] == "req-1"
    assert payload["steps"][0]["status"] == "completed"
    assert payload["steps"][0]["tool_name"] == "context.build"
    assert payload["steps"][0]["output_summary"]["intent"] == "general_chat"


def test_agent_planner_builds_current_message_first_plan():
    registry = ToolRegistry()
    for name in [
        "profile.extract",
        "memory.verify",
        "memory.write",
        "context.build",
        "plan.decide",
        "response.verify",
        "guardrail.check",
        "response.persist",
    ]:
        registry.register(ToolSpec(name=name, description=name), lambda _: {})

    plan = AgentPlanner().plan_chat_turn("Do I need creatine?", registry.list_specs())

    assert plan.strategy == "current_message_first"
    assert plan.steps[0].key == "profile_extract"
    keys = [step.key for step in plan.steps]
    assert keys.index("memory_verify") < keys.index("memory_write")
    assert any(step.key == "response_verify" for step in plan.steps)
    assert "current user message" in plan.assumptions[0]


def test_agent_planner_adds_plan_tools_only_for_plan_intent():
    registry = ToolRegistry()
    for name in [
        "profile.extract",
        "memory.verify",
        "memory.write",
        "context.build",
        "plan.decide",
        "plan.generate",
        "plan.verify",
        "plan.repair",
        "response.verify",
        "response.repair",
        "guardrail.check",
        "response.persist",
    ]:
        registry.register(ToolSpec(name=name, description=name), lambda _: {})

    plan = AgentPlanner().plan_chat_turn("今天应该练什么？帮我生成训练计划", registry.list_specs())
    keys = [step.key for step in plan.steps]

    assert plan.intent == "training_plan"
    assert "plan_generate" in keys
    assert "plan_verify" in keys
    assert "plan_repair" in keys
    assert "response_repair" in keys

    non_plan = AgentPlanner().plan_chat_turn("今天卧推55kg做了3组，有点累", registry.list_specs())
    non_plan_keys = [step.key for step in non_plan.steps]
    assert non_plan.intent == "training_log"
    assert "plan_generate" not in non_plan_keys
    assert "plan_verify" not in non_plan_keys


def test_tool_registry_rejects_invalid_input_schema():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="strict.tool",
            description="Strict",
            input_schema={
                "type": "object",
                "required": ["message_chars"],
                "properties": {"message_chars": {"type": "integer"}},
            },
        ),
        lambda payload: {"ok": True},
    )

    result = asyncio.run(registry.execute("strict.tool", {"message_chars": "bad"}))

    assert result.status == "schema_error"
    assert result.attempts == 0
    assert result.validation_errors


def test_tool_registry_retries_transient_tool_error():
    registry = ToolRegistry()
    calls = {"count": 0}

    def flaky(_payload):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return {"ok": True}

    registry.register(ToolSpec(name="flaky.tool", description="Flaky", retry_count=1), flaky)

    result = asyncio.run(registry.execute("flaky.tool", {}))

    assert result.status == "success"
    assert result.attempts == 2
    assert calls["count"] == 2


def test_tool_registry_repairs_invalid_output_schema():
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="repairable.tool",
            description="Repairable",
            output_schema={
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
            },
        ),
        lambda _payload: {"message": "missing ok"},
        repair_handler=lambda payload: {"output_json": {**payload.get("output_json", {}), "ok": True}},
    )

    result = asyncio.run(registry.execute("repairable.tool", {}))

    assert result.status == "success"
    assert result.output_json["ok"] is True
    assert result.repaired is True
    assert "repair_output_schema" in result.repair_actions


def test_agent_executor_runs_tool_and_updates_timeline():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="context.build", description="Build context"), lambda _: {"intent": "training_plan"})
    timeline = AgentTaskTimeline("build context", request_id="req-executor")
    step = timeline.add_step("Build context", "context.build", "Need intent context")

    execution = asyncio.run(AgentExecutor().execute(registry, timeline, step, {"message_chars": 12}))

    assert execution.result.status == "success"
    assert execution.result.output_json["intent"] == "training_plan"
    assert execution.started_event["status"] == "running"
    assert execution.completed_event["status"] == "completed"
    assert timeline.to_dict()["steps"][0]["output_summary"]["intent"] == "training_plan"
