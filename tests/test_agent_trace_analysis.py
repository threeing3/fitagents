from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from fast_api.app.services.agent_trace_analysis import analyze_agent_run


def test_agent_trace_analysis_is_sanitized_and_classifies_failures():
    run = SimpleNamespace(
        id=uuid4(),
        run_type="chat_stream",
        status="completed",
        started_at=datetime(2026, 8, 17, 8, 0, 0),
        completed_at=datetime(2026, 8, 17, 8, 0, 1),
        summary="safe reply",
        nodes=[
            {
                "node": "PlannerVerifier",
                "status": "completed",
                "latency_ms": 2,
                "input_summary": {"message": "private user message"},
                "output": {
                    "intent": "training_plan",
                    "planner_mode": "rule",
                    "tool_order": ["context.build", "plan.generate"],
                },
            },
            {
                "node": "ContextBuilder",
                "status": "completed",
                "latency_ms": 4,
                "output": {
                    "relevant_memory_count": 3,
                    "active_risk_count": 1,
                    "raw_context": "must-not-leak",
                    "knowledge_debug": {"vector_status": "vector unavailable"},
                },
            },
            {
                "node": "ToolExecutor",
                "status": "failed",
                "latency_ms": 5,
                "output": {"tool_name": "plan.generate", "status": "failed", "secret": "no"},
            },
            {
                "node": "GuardrailCheck",
                "status": "completed",
                "latency_ms": 1,
                "output": {"action": "warn", "flag_count": 1},
            },
        ],
    )
    calls = [SimpleNamespace(tool_name="plan.generate")]

    result = analyze_agent_run(run, calls)

    assert result["decision"]["intent"] == "training_plan"
    assert result["decision"]["memory_count"] == 3
    assert {item["code"] for item in result["findings"]} >= {
        "retrieval_degraded",
        "tool_failure",
        "runtime_error",
        "guardrail_intervention",
    }
    serialized = str(result)
    assert "private user message" not in serialized
    assert "must-not-leak" not in serialized
    assert '"secret"' not in serialized


def test_agent_trace_analysis_reports_healthy_empty_run():
    run = SimpleNamespace(
        id=uuid4(),
        run_type="chat",
        status="completed",
        started_at=datetime(2026, 8, 17, 8, 0, 0),
        completed_at=None,
        summary=None,
        nodes=[],
    )

    result = analyze_agent_run(run, [], include_timeline=False)

    assert result["timeline"] == []
    assert result["findings"][0]["code"] == "healthy_run"
    assert result["privacy"] == "sanitized_projection_no_raw_user_context"
