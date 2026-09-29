"""Replay synthetic intent cases through the actual offline chat entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.intent_inference_client import IntentInferenceResult
from fast_api.app.services.model_provider import ModelProvider

DATASET = (
    Path(__file__).resolve().parents[1]
    / "datasets"
    / "fixtures"
    / "intent_full_chat_replay_v1.json"
)
REPORT_ROOT = Path(__file__).resolve().parent / "reports"


async def _chat_with_wait_trace(service, session_id, user_id, message, log_dir):
    """Record suspended coroutine locations without exposing frame locals or secrets."""
    task = asyncio.create_task(service.handle_chat_message(session_id, user_id, message))
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=5)
        if done:
            break
        current = task.get_coro()
        frames = []
        while current is not None:
            code = getattr(current, "cr_code", None)
            frame = getattr(current, "cr_frame", None)
            if code:
                frames.append(f"{code.co_filename}:{frame.f_lineno if frame else 0} {code.co_name}")
            current = getattr(current, "cr_await", None)
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "async_wait.log").open("a", encoding="utf-8") as handle:
            handle.write(datetime.now().isoformat() + "\n" + "\n".join(frames) + "\n\n")
    return await task


def load_cases(path: Path = DATASET) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "intent-full-chat-replay/v1":
        raise ValueError("Unsupported replay dataset schema")
    if payload.get("source") != "synthetic_self_authored" or payload.get("training_eligible"):
        raise ValueError("Replay requires non-training synthetic fixtures")
    cases = payload.get("cases", [])
    ids = [case.get("case_id") for case in cases]
    if not cases or any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("Replay case IDs must be nonempty and unique")
    return cases


def _count_state(engine, user_id: uuid.UUID) -> dict[str, int]:
    tables = {
        "workout_log": models.WorkoutLog,
        "plan": models.TrainingPlan,
        "user_risk_note": models.RiskNote,
        "memory": models.LongTermMemory,
    }
    with Session(engine) as reader:
        return {
            name: reader.scalar(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            )
            or 0
            for name, model in tables.items()
        }


def _profile_state(engine, user_id: uuid.UUID) -> dict:
    with Session(engine) as reader:
        profile = reader.scalar(
            select(models.UserProfile).where(models.UserProfile.user_id == user_id)
        )
        if profile is None:
            raise RuntimeError("Replay profile disappeared")
        return {
            "age": profile.age,
            "height_cm": profile.height_cm,
            "weight_kg": profile.weight_kg,
            "goal": profile.goal,
            "experience_level": profile.experience_level,
            "injuries": profile.injuries,
        }


def replay_case(case: dict, log_dir: Path, *, recorded_model_payload: dict | None = None) -> dict:
    """Use one new in-memory database and a fresh reader for committed outcomes."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="offline",
        EMBEDDING_PROVIDER="offline",
        ADAPTER_INFERENCE_URL=None,
        USE_PGVECTOR=False,
        AGENT_RUNTIME_MODE="code_driven",
        CODE_DRIVEN_PLANNER="rule",
        AGENT_LOG_DIR=str(log_dir),
        JWT_SECRET_KEY="synthetic-replay-only",
    )
    if (
        settings.has_live_model_key
        or settings.has_live_embedding_key
        or settings.adapter_inference_url
    ):
        raise RuntimeError("Replay refuses configuration with live model access")
    user_id = uuid.uuid4()
    try:
        with Session(engine) as db:
            db.add(models.User(id=user_id, email=f"{user_id}@example.test", password_hash="none"))
            db.add(
                models.UserProfile(
                    user_id=user_id,
                    age=25,
                    sex="male",
                    height_cm=175,
                    weight_kg=70,
                    goal="maintenance",
                    experience_level="beginner",
                    workout_frequency=3,
                    equipment_available=["dumbbells"],
                    injuries=[],
                )
            )
            db.commit()
            service = CoachAgentService(db, ModelProvider(settings))
            session_id = service.create_session(user_id, "Synthetic", case["case_id"]).id
            before = _count_state(engine, user_id)
            profile_before = _profile_state(engine, user_id)
            recorded_call = None
            with ExitStack() as patches:
                patches.enter_context(
                    patch("fast_api.app.services.coach_agent.get_settings", return_value=settings)
                )
                patches.enter_context(
                    patch(
                        "fast_api.app.services.agent_observability.get_settings",
                        return_value=settings,
                    )
                )
                if recorded_model_payload is not None:
                    valid = service.intent_decision_engine.inference_client._valid_decision(
                        recorded_model_payload
                    )
                    if not valid:
                        raise ValueError("Recorded model payload does not satisfy intent contract")
                    recorded_call = AsyncMock(
                        return_value=IntentInferenceResult(
                            attempted=True,
                            succeeded=True,
                            status="recorded_prediction",
                            payload=recorded_model_payload,
                            model_version="recorded_prediction",
                        )
                    )
                    patches.enter_context(
                        patch.object(
                            service.intent_decision_engine.inference_client,
                            "classify",
                            recorded_call,
                        )
                    )
                result = asyncio.run(
                    _chat_with_wait_trace(service, session_id, user_id, case["message"], log_dir)
                )

        after = _count_state(engine, user_id)
        profile_after = _profile_state(engine, user_id)
        run_id = result["agent_run_id"]
        with Session(engine) as reader:
            run = reader.get(models.AgentRun, run_id)
            replay = reader.scalar(
                select(models.AgentRunReplay).where(models.AgentRunReplay.agent_run_id == run_id)
            )
            calls = reader.scalars(
                select(models.ToolCall).where(models.ToolCall.agent_run_id == run_id)
            ).all()
            messages = reader.scalars(
                select(models.ChatMessage).where(models.ChatMessage.session_id == session_id)
            ).all()
            task_events = reader.scalars(
                select(models.AgentTaskEvent).where(models.AgentTaskEvent.agent_run_id == run_id)
            ).all()
            persisted = {
                "run_status": run.status if run else None,
                "agent_log_path": run.log_path if run else None,
                "replay_recorded": replay is not None,
                "runtime_errors": [
                    node
                    for node in (run.nodes if run else [])
                    if node.get("node") == "RuntimeError"
                ],
                "tool_calls": [
                    {
                        "name": call.tool_name,
                        "status": call.status,
                        "input": call.input_json,
                        "output": call.output_json,
                    }
                    for call in calls
                ],
                "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
                "task_events": [
                    {"type": item.event_type, "summary": item.summary} for item in task_events
                ],
            }

        deltas = {name: after[name] - before[name] for name in before}
        checks = {
            "run_completed": persisted["run_status"] == "completed",
            "no_runtime_error": not persisted["runtime_errors"],
            "replay_recorded": persisted["replay_recorded"],
            "reply_persisted": any(
                item["role"] == "assistant" and item["content"] == result["assistant_message"]
                for item in persisted["messages"]
            ),
            "tool_trace_persisted": len(calls) == len(result["tool_calls"]),
        }
        checks.update(
            {
                name: deltas[name.removesuffix("_delta")] == target
                for name, target in case["expected"].items()
                if name not in {"profile_unchanged", "reply_contains", "reply_excludes"}
            }
        )
        if "profile_unchanged" in case["expected"]:
            checks["profile_unchanged"] = (profile_before == profile_after) is case["expected"][
                "profile_unchanged"
            ]
        if "reply_contains" in case["expected"]:
            checks["reply_contains"] = all(
                phrase in result["assistant_message"]
                for phrase in case["expected"]["reply_contains"]
            )
        if "reply_excludes" in case["expected"]:
            checks["reply_excludes"] = all(
                phrase not in result["assistant_message"]
                for phrase in case["expected"]["reply_excludes"]
            )
        counts = Counter(call["name"] for call in persisted["tool_calls"])
        decision = result.get("runtime_route", {}).get("intent_decision", {})
        return {
            "case_id": case["case_id"],
            "source": "synthetic_self_authored",
            "message": case["message"],
            "expected": case["expected"],
            "runtime_route": result.get("runtime_route"),
            "intent_summary": {
                "primary": decision.get("primary_intent"),
                "secondary": decision.get("secondary_intents", []),
                "candidate_tools": decision.get("candidate_tools", []),
            },
            "assistant_message": result["assistant_message"],
            "response_review_status": "not_reviewed",
            "recorded_prediction_supplied": recorded_model_payload is not None,
            "recorded_prediction_consumed": bool(recorded_call and recorded_call.await_count),
            "tool_call_counts": dict(counts),
            "state_before": before,
            "state_after": after,
            "state_delta": deltas,
            "profile_before": profile_before,
            "profile_after": profile_after,
            "checks": checks,
            "passed": all(checks.values()),
            "persisted": persisted,
        }
    finally:
        engine.dispose()


def run_replay(output_dir: Path, dataset: Path = DATASET) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite replay output: {output_dir}")
    output_dir.mkdir(parents=True)
    cases = load_cases(dataset)
    results = []
    for case in cases:
        case_dir = output_dir / "agent_logs" / case["case_id"]
        try:
            outcome = replay_case(case, case_dir)
        except Exception as exc:
            outcome = {"case_id": case["case_id"], "passed": False, "error": repr(exc)}
        results.append(outcome)
        (output_dir / f"{case['case_id']}.json").write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    summary = {
        "schema_version": "intent-full-chat-replay-result/v1",
        "evidence_scope": "synthetic_offline_isolated_sqlite_full_handle_chat_message",
        "case_count": len(results),
        "passed_count": sum(item["passed"] for item in results),
        "failed_case_ids": [item["case_id"] for item in results if not item["passed"]],
        "dataset": str(dataset),
        "output_dir": str(output_dir),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or REPORT_ROOT / (
        "intent_full_chat_replay_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    print(json.dumps(run_replay(output_dir, args.dataset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
