"""Task checks for the actual tool loop; synthetic tools are not business integration."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.llm_agent import LLMAgentService

CASES = (
    "normal_read",
    "invalid_request_recovery",
    "transient_read_recovery",
    "dependent_read_chain",
)
REQUEST = '<tool_call>{"name":"training.log.read","input":{}}</tool_call>'


class RecordedModel:
    def __init__(self, model: Any, case_id: str):
        self.model = model
        self.case_id = case_id
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages):
        started = time.perf_counter()
        entry = {
            "messages": [
                {"role": type(message).__name__, "content": str(message.content)}
                for message in messages
            ],
            "injected": self.case_id == "invalid_request_recovery" and not self.calls,
        }
        self.calls.append(entry)
        try:
            if entry["injected"]:
                response = SimpleNamespace(content="<tool_call>[]</tool_call>")
            else:
                response = await self.model.ainvoke(messages)
            entry["response"] = str(response.content)
            entry["usage"] = getattr(response, "usage_metadata", None)
            metadata = getattr(response, "response_metadata", {})
            entry["response_model"] = metadata.get("model_name")
            entry["response_metadata"] = metadata
            return response
        except Exception as exc:
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            entry["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)


class ScriptedModel:
    """Checks evaluator plumbing; never a model-quality measurement."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.chain_step = 0
        replies = [REQUEST, '{"workouts":37}']
        if case_id == "transient_read_recovery":
            replies.insert(0, REQUEST)
        self.replies = iter(replies)

    async def ainvoke(self, _messages):
        if self.case_id == "dependent_read_chain":
            self.chain_step += 1
            if self.chain_step == 1:
                return SimpleNamespace(
                    content='<tool_call>{"name":"context.build","input":{}}</tool_call>'
                )
            if self.chain_step == 2:
                observation = str(_messages[-1].content).split("\n")[1]
                session_ref = json.loads(observation)["session_ref"]
                return SimpleNamespace(
                    content="<tool_call>"
                    + json.dumps(
                        {"name": "training.log.read", "input": {"session_ref": session_ref}}
                    )
                    + "</tool_call>"
                )
            return SimpleNamespace(content='{"workouts":37}')
        return SimpleNamespace(content=next(self.replies))


class ConfiguredModel:
    """Reserve the configured quota on every call, not only at construction."""

    def __init__(self, provider):
        self.provider = provider

    async def ainvoke(self, messages):
        model = self.provider.chat_model(temperature=0.0)
        if model is None:
            raise RuntimeError("Live model or call quota unavailable")
        return await model.ainvoke(messages)


def score_task(
    case_id: str,
    result,
    handler_calls: int,
    model_calls: list[dict],
    handler_trace: list[dict] | None = None,
) -> dict:
    try:
        answer = json.loads(result.final_response)
    except (ValueError, TypeError):
        answer = None
    successful_reads = [
        call
        for call in result.tool_calls
        if call["tool_name"] == "training.log.read"
        and call["status"] == "success"
        and call["output"] == {"workouts": 37}
    ]
    statuses = [call["status"] for call in result.tool_calls]
    checks = {
        "no_runtime_error": result.error is None,
        "grounded_read_executed": bool(successful_reads) and handler_calls > 0,
        "answer_contract": answer == {"workouts": 37},
        "call_count_consistent": result.iterations == len(model_calls),
    }
    if case_id == "invalid_request_recovery":
        checks["invalid_request_observed"] = "invalid" in statuses
        checks["feedback_delivered"] = any(
            "invalid_tool_call" in message["content"]
            for call in model_calls[1:]
            for message in call["messages"]
        )
    if case_id == "transient_read_recovery":
        checks["failed_read_observed"] = "error" in statuses
        checks["retried_handler"] = handler_calls >= 2
    if case_id == "dependent_read_chain":
        trace = handler_trace or []
        contexts = [entry for entry in trace if entry["tool_name"] == "context.build"]
        reads = [entry for entry in trace if entry["tool_name"] == "training.log.read"]
        checks["context_executed"] = bool(contexts)
        checks["dependent_input_observed"] = bool(contexts and reads) and any(
            context["sequence"] < read["sequence"]
            and read["input"].get("session_ref") == context["output"]["session_ref"]
            and read["status"] == "success"
            for context in contexts
            for read in reads
        )
        checks["no_dependency_bypass"] = bool(reads) and all(
            read["status"] == "success" for read in reads
        )
    return {"passed": all(checks.values()), "checks": checks}


async def evaluate_task(
    case_id: str,
    model: Any,
    *,
    evidence_mode: str,
    agent_class: type[LLMAgentService] = LLMAgentService,
) -> dict:
    if case_id not in CASES:
        raise ValueError("Unknown task")
    registry = ToolRegistry()
    handler_calls = 0
    handler_trace: list[dict] = []
    session_ref = "synthetic-" + uuid.uuid4().hex[:12]
    context_ready = False

    def context(_inputs):
        nonlocal context_ready
        context_ready = True
        output = {"session_ref": session_ref}
        handler_trace.append(
            {
                "sequence": len(handler_trace),
                "tool_name": "context.build",
                "input": dict(_inputs),
                "output": output,
                "status": "success",
            }
        )
        return output

    def read(_inputs):
        nonlocal handler_calls
        handler_calls += 1
        entry = {
            "sequence": len(handler_trace),
            "tool_name": "training.log.read",
            "input": dict(_inputs),
            "status": "error",
        }
        handler_trace.append(entry)
        if case_id == "dependent_read_chain" and (
            not context_ready or _inputs.get("session_ref") != session_ref
        ):
            raise ValueError("Read requires session_ref from a preceding context.build result.")
        if case_id == "transient_read_recovery" and handler_calls == 1:
            raise RuntimeError("Synthetic temporary read failure; retry the read tool.")
        entry["status"] = "success"
        return {"workouts": 37}

    if case_id == "dependent_read_chain":
        registry.register(
            ToolSpec(
                name="context.build",
                description="Read the session_ref required by training.log.read.",
                input_schema={"type": "object", "properties": {}},
            ),
            context,
        )

    registry.register(
        ToolSpec(
            name="training.log.read",
            description="Read workout count. "
            + (
                "Requires session_ref returned by context.build; never invent it."
                if case_id == "dependent_read_chain"
                else "For the past seven days."
            ),
            input_schema={
                "type": "object",
                "properties": {"session_ref": {"type": "string"}}
                if case_id == "dependent_read_chain"
                else {},
                "required": ["session_ref"] if case_id == "dependent_read_chain" else [],
                "additionalProperties": False,
            },
        ),
        read,
    )
    recorded = RecordedModel(model, case_id)
    provider = SimpleNamespace(
        settings=SimpleNamespace(chat_model="task-evaluation", llm_provider=evidence_mode),
        chat_model=lambda **_kwargs: recorded,
    )
    agent = agent_class(
        None,
        provider,
        registry,
        uuid.uuid4(),
        uuid.uuid4(),
        SimpleNamespace(injuries=[], goal="maintenance"),
        '请通过工具查询我过去七天的训练次数，只输出JSON对象，例如 {"workouts":整数}。',
    )
    started = time.perf_counter()
    result = await agent.run()
    return {
        "case_id": case_id,
        "evidence_mode": evidence_mode,
        "tool_environment": "synthetic_read_only_no_database",
        "intervention": "first_response_invalid" if case_id == CASES[1] else case_id,
        **score_task(case_id, result, handler_calls, recorded.calls, handler_trace),
        "handler_calls": handler_calls,
        "handler_trace": handler_trace,
        "model_calls": recorded.calls,
        "live_calls": sum(not call["injected"] for call in recorded.calls)
        if evidence_mode in {"live_model_synthetic_tools", "local_model_synthetic_tools"}
        else 0,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "final_response": result.final_response,
        "tool_calls": result.tool_calls,
        "error": result.error,
    }


async def run(output: Path, *, live: bool = False, local: bool = False) -> dict:
    if live and local:
        raise ValueError("Choose one explicit model transport")
    # Never overwrite a prior successful or failed run.
    output.mkdir(parents=True, exist_ok=False)
    mode = "live_model_synthetic_tools" if live else "scripted_model_synthetic_tools"
    if local:
        mode = "local_model_synthetic_tools"
    log_path = output / "run.log"

    def log(message):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(datetime.now(timezone.utc).isoformat() + " " + message + "\n")

    log("START " + mode + "; no production database; tasks=" + str(len(CASES)))
    model = None
    if local:
        from algorithm.evaluation.local_qwen_model import LocalQwenModel

        model = LocalQwenModel()
    if live:
        from fast_api.app.services.model_provider import ModelProvider

        provider = ModelProvider()
        if not provider.has_live_model():
            log("STOP live model unavailable; no offline substitution")
            raise RuntimeError("Live model unavailable")
        model = ConfiguredModel(provider)
    rows = []
    for case_id in CASES:
        log("TASK_START " + case_id)
        try:
            row = await evaluate_task(case_id, model or ScriptedModel(case_id), evidence_mode=mode)
        except Exception as exc:
            log("TASK_ERROR " + case_id + " " + type(exc).__name__)
            raise
        (output / (case_id + ".json")).write_text(
            json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        rows.append(row)
        log("TASK_END " + case_id + " passed=" + str(row["passed"]))
    summary = {
        "schema_version": "tool-loop-task-eval/v2",
        "evidence_mode": mode,
        "cases": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "limitations": [
            "Four authored diagnostics, not production-distribution accuracy.",
            "Synthetic read tools; no profile persistence, business writes or database integration.",
            "Invalid first response is injected, not spontaneous model behavior.",
            "Exact JSON answer contract, not a semantic medical or coaching assessment.",
        ],
        "results": [{"case_id": row["case_id"], "checks": row["checks"]} for row in rows],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("END passed=" + str(summary["passed"]))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true", help="Explicitly use configured paid model")
    modes.add_argument("--local", action="store_true", help="Use local Qwen on 127.0.0.1:8079 only")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(args.output, live=args.live, local=args.local)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
