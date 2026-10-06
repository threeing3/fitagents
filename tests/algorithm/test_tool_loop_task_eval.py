import asyncio
from types import SimpleNamespace

import pytest

from algorithm.evaluation.tool_loop_task_eval import (
    CASES,
    ConfiguredModel,
    ScriptedModel,
    evaluate_task,
    run,
    score_task,
)


@pytest.mark.parametrize("case_id", CASES)
def test_scripted_tasks_use_actual_loop_and_grounded_read(case_id):
    result = asyncio.run(
        evaluate_task(
            case_id, ScriptedModel(case_id), evidence_mode="scripted_model_synthetic_tools"
        )
    )
    assert result["passed"]
    assert result["live_calls"] == 0
    assert result["tool_environment"] == "synthetic_read_only_no_database"


def test_verbally_correct_answer_without_tool_execution_is_not_success():
    result = SimpleNamespace(
        final_response='{"workouts":37}', tool_calls=[], error=None, iterations=1
    )
    score = score_task("normal_read", result, 0, [{"messages": []}])
    assert not score["passed"]
    assert not score["checks"]["grounded_read_executed"]


def test_evidence_mode_and_failed_run_preservation(tmp_path):
    output = tmp_path / "new_run"
    summary = asyncio.run(run(output))
    assert summary["evidence_mode"] == "scripted_model_synthetic_tools"
    assert summary["passed"] == len(CASES)
    assert (output / "run.log").is_file()
    with pytest.raises(FileExistsError):
        asyncio.run(run(output))


def test_live_wrapper_preserves_per_call_quota_checks():
    class Provider:
        calls = 0

        def chat_model(self, **_kwargs):
            self.calls += 1
            return ScriptedModel("normal_read") if self.calls == 1 else None

    provider = Provider()
    model = ConfiguredModel(provider)
    asyncio.run(model.ainvoke([]))
    with pytest.raises(RuntimeError, match="quota unavailable"):
        asyncio.run(model.ainvoke([]))
    assert provider.calls == 2


def test_dependency_chain_passes_observed_reference_to_second_tool():
    row = asyncio.run(
        evaluate_task(
            "dependent_read_chain",
            ScriptedModel("dependent_read_chain"),
            evidence_mode="scripted_model_synthetic_tools",
        )
    )
    assert row["passed"]
    context, read = row["handler_trace"]
    assert context["output"]["session_ref"] == read["input"]["session_ref"]
    assert len(row["model_calls"]) == 3


def test_fabricated_read_trace_without_context_is_not_chain_success():
    result = SimpleNamespace(
        final_response='{"workouts":37}',
        tool_calls=[
            {"tool_name": "training.log.read", "status": "success", "output": {"workouts": 37}}
        ],
        error=None,
        iterations=1,
    )
    score = score_task("dependent_read_chain", result, 1, [{"messages": []}])
    assert not score["passed"]
    assert not score["checks"]["context_executed"]


@pytest.mark.parametrize("inputs", [{}, {"session_ref": "invented-session"}])
def test_skipped_dependency_cannot_pass_with_correct_verbal_answer(inputs):
    import json

    class SkippingModel:
        def __init__(self):
            self.replies = iter(
                [
                    "<tool_call>"
                    + json.dumps({"name": "training.log.read", "input": inputs})
                    + "</tool_call>",
                    '{"workouts":37}',
                ]
            )

        async def ainvoke(self, _messages):
            return SimpleNamespace(content=next(self.replies))

    row = asyncio.run(
        evaluate_task(
            "dependent_read_chain",
            SkippingModel(),
            evidence_mode="scripted_model_synthetic_tools",
        )
    )
    assert not row["passed"]
    assert all(entry["status"] == "error" for entry in row["handler_trace"])
    assert not row["checks"]["context_executed"]
    assert not row["checks"]["grounded_read_executed"]
