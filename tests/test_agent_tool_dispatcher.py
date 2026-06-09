from types import SimpleNamespace

from fast_api.app.services.agent_tool_dispatcher import (
    ReplayRunner,
    ToolInputBuilder,
    ToolOutputReducer,
    ToolRuntimeState,
)


def test_tool_input_builder_builds_memory_write_from_verified_state():
    state = ToolRuntimeState(message="记住我不自己做饭", message_chars=8)
    state.extraction = {"open_memories": [{"type": "nutrition_habit"}]}
    state.memory_verification = {"passed": True, "accepted_candidates": [{"type": "nutrition_habit"}]}

    payload = ToolInputBuilder(state).build("memory.write")

    assert payload.skip_reason is None
    assert payload.payload["extraction"] == state.extraction
    assert payload.payload["verification"] == state.memory_verification


def test_tool_input_builder_skips_context_when_onboarding_incomplete():
    state = ToolRuntimeState(message="今天练什么", message_chars=5, onboarding_complete=False)

    payload = ToolInputBuilder(state).build("context.build")

    assert payload.payload == {}
    assert payload.skip_reason == "onboarding_or_static_safety"


def test_tool_output_reducer_updates_context_and_plan_state():
    state = ToolRuntimeState(message="帮我制定计划", message_chars=6, onboarding_complete=True)
    reducer = ToolOutputReducer(state)

    reducer.reduce("context.build", {"intent": "training_plan", "active_plan": None})
    reducer.reduce("plan.decide", {"should_generate_plan": True, "reason": "current request asks for plan"})
    reducer.reduce("plan.generate", {"plan_id": "p1", "active_plan": {"title": "Week 1"}})

    assert state.context_packet["intent"] == "training_plan"
    assert state.plan_decision["should_generate_plan"] is True
    assert state.plan_output["plan_id"] == "p1"
    assert state.context_packet["active_plan"]["title"] == "Week 1"
    assert state.executed_tools == ["context.build", "plan.decide", "plan.generate"]


def test_replay_runner_adds_dispatcher_state_to_tool_plan_json():
    state = ToolRuntimeState(message="你好", message_chars=2)
    state.executed_tools = ["profile.extract", "coach.reply", "response.persist"]
    state.skipped_tools = [{"tool_name": "context.build", "reason": "onboarding_or_static_safety"}]
    state.persisted = True

    execution_plan = SimpleNamespace(
        steps=[
            SimpleNamespace(tool_name="profile.extract"),
            SimpleNamespace(tool_name="coach.reply"),
            SimpleNamespace(tool_name="response.persist"),
        ],
        to_dict=lambda: {"plan_id": "plan-1", "steps": []},
    )
    timeline = SimpleNamespace(to_dict=lambda: {"timeline_id": "tl-1"})

    replay = ReplayRunner.tool_plan_json(
        execution_plan=execution_plan,
        timeline=timeline,
        tool_contracts=[],
        contract_issues=[],
        planner_debug={"planner_repair_actions": ["canonicalize_host_tool_order"]},
        dispatcher_state=state,
    )

    assert replay["verified_tool_order"] == ["profile.extract", "coach.reply", "response.persist"]
    assert replay["dispatcher"]["executed_tools"] == state.executed_tools
    assert replay["dispatcher"]["skipped_tools"] == state.skipped_tools
    assert replay["dispatcher"]["persisted"] is True
