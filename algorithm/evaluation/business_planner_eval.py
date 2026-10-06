"""Actual chat service, host-controlled writes and planner over synthetic SQLite data."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from algorithm.evaluation.local_qwen_model import LocalQwenModel
from algorithm.evaluation.tool_loop_task_eval import RecordedModel
from algorithm.evaluation.workout_history_model_eval import USER_ID, seed_database, snapshot
from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.intent_cascade import IntentCascadePolicy
from fast_api.app.services.model_provider import ModelProvider

CASES = (
    {"id": "record", "messages": ["我刚完成跑步二十三分钟，帮我记录。"], "new_minutes": [23]},
    {
        "id": "hypothetical",
        "messages": ["如果我跑步二十三分钟，能不能记为训练？"],
        "new_minutes": [],
    },
    {"id": "history", "messages": ["请查一下我上一次骑行用了多久"], "new_minutes": []},
    {
        "id": "clarify",
        "messages": ["我刚完成哑铃训练，帮我记录", "30分钟"],
        "new_minutes": [30],
    },
    {
        "id": "cancel",
        "messages": ["我刚完成哑铃训练，帮我记录", "取消，不要记录了", "30分钟"],
        "new_minutes": [],
    },
    {"id": "plan", "messages": ["请安排明天慢跑。"], "new_minutes": []},
    {
        "id": "risk",
        "messages": ["我胸口疼，帮我安排今天高强度训练"],
        "new_minutes": [],
    },
)


class PlannerProvider(ModelProvider):
    """Only chat_model uses local transport; generation/embedding remain offline."""

    def __init__(self, settings, model):
        super().__init__(settings)
        self.planner_model = model

    def chat_model(self, temperature=0.0):
        return self.planner_model


async def evaluate_case(case, directory: Path, *, mode: str, model=None):
    directory.mkdir(parents=True, exist_ok=False)
    engine = create_engine("sqlite:///" + str(directory / "synthetic.sqlite"))
    seed_database(engine)
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="offline",
        EMBEDDING_PROVIDER="offline",
        ADAPTER_INFERENCE_URL=None,
        USE_PGVECTOR=False,
        AGENT_RUNTIME_MODE="code_driven",
        CODE_DRIVEN_PLANNER=mode,
        AGENT_LOG_DIR=str(directory / "agent_logs"),
    )
    recorded = RecordedModel(model, case["id"]) if mode == "llm" else None
    provider = PlannerProvider(settings, recorded) if recorded else ModelProvider(settings)
    try:
        with Session(engine) as db:
            db.add(
                models.UserProfile(
                    user_id=USER_ID,
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
            before = snapshot(db)
            session_id = (
                CoachAgentService(db, provider).create_session(USER_ID, "Synthetic", case["id"]).id
            )
        turns = []
        for index, message in enumerate(case["messages"]):
            # The service resolves routing settings independently of its provider.
            # Bind both resolvers inside this isolated evaluator, not global config.
            with (
                Session(engine) as db,
                patch("fast_api.app.services.coach_agent.get_settings", return_value=settings),
                patch(
                    "fast_api.app.services.agent_observability.get_settings", return_value=settings
                ),
            ):
                service = CoachAgentService(db, provider)
                # This experiment compares the legacy rule/LLM planners, not
                # the default lightweight route which intentionally reuses dispatch.
                service.intent_decision_engine.cascade_policy = IntentCascadePolicy()
                service.intent_decision_engine.semantic_assistance = True
                response = await service.handle_chat_message(
                    session_id, USER_ID, message, idempotency_key=f"{case['id']}-{index}"
                )
                turns.append(response)
            with Session(engine) as db:
                turns[-1]["database_workouts"] = snapshot(db)
        with Session(engine) as db:
            after = snapshot(db)
            plans = [
                {
                    "id": str(p.id),
                    "user_id": str(p.user_id),
                    "status": p.status,
                    "plan": p.plan_json,
                }
                for p in db.scalars(select(models.TrainingPlan)).all()
            ]
        old_ids = {row["id"] for row in before}
        additions = [row for row in after if row["id"] not in old_ids]
        checks = {
            "expected_workout_writes": sorted(row["minutes"] for row in additions)
            == sorted(case["new_minutes"]),
            "original_records_unchanged": [row for row in after if row["id"] in old_ids] == before,
            "no_foreign_writes": all(row["user_id"] == str(USER_ID) for row in additions)
            and all(plan["user_id"] == str(USER_ID) for plan in plans),
            "expected_plan_count": len(plans) == (1 if case["id"] == "plan" else 0),
            "planner_executed": mode == "rule" or bool(recorded and recorded.calls),
        }
        if case["id"] in {"clarify", "cancel"}:
            checks["no_premature_write"] = turns[0]["database_workouts"] == before
        if case["id"] == "history":
            reads = [
                call
                for turn in turns
                for call in turn["tool_calls"]
                if call["tool_name"] == "training.log.read" and call["status"] == "success"
            ]
            checks["history_read_grounded"] = bool(reads) and "18" in str(
                turns[-1]["assistant_message"]
            )
        if case["id"] == "plan":
            checks["requested_plan_constraints"] = bool(plans) and plans[0]["plan"].get(
                "request_constraints"
            ) == {
                "target_date": (datetime.now().date() + timedelta(days=1)).isoformat(),
                "exercise_type": "easy_jog",
            }
        planner_states = [turn["state_updates"].get("planner", {}) for turn in turns]
        checks["declared_planner_mode"] = all(
            p.get("mode") == "rule" if mode == "rule" else p.get("mode") in {"llm", "rule_fallback"}
            for p in planner_states
        )
        return {
            "case_id": case["id"],
            "mode": mode,
            "passed": all(checks.values()),
            "checks": checks,
            "before": before,
            "after": after,
            "plans": plans,
            "turns": turns,
            "planner_states": planner_states,
            "fallback_turns": sum(bool(p.get("fallback")) for p in planner_states),
            "model_calls": recorded.calls if recorded else [],
        }
    finally:
        engine.dispose()


async def run(output: Path, model):
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        with (output / "run.log").open("a", encoding="utf-8") as handle:
            handle.write(datetime.now(timezone.utc).isoformat() + " " + message + "\n")

    protocol = {
        "revision": "business-planner/v2-config-isolation",
        "cases": CASES,
        "scope": "actual non-streaming chat service and synthetic SQLite; local model planner only",
        "date_anchor": datetime.now().date().isoformat(),
        "limits": "local transport max40 calls, temperature0 seed42 output512; no paid API",
        "limitations": [
            "Seven authored acceptance cases, not independent statistical accuracy.",
            "Reply generation, intent routing and embeddings stay offline.",
            "Writes and plan-order repairs are host controlled, not autonomous model permission.",
            "No HTTP, streaming, PostgreSQL or production-user validation.",
        ],
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = []
    log("START actual business planner acceptance")
    for index, case in enumerate(CASES):
        for mode in ("rule", "llm") if index % 2 == 0 else ("llm", "rule"):
            log("CASE_START " + case["id"] + " " + mode)
            try:
                row = await evaluate_case(
                    case, output / (case["id"] + "_" + mode), mode=mode, model=model
                )
            except Exception as exc:
                log("ERROR " + case["id"] + " " + mode + " " + repr(exc))
                raise
            (output / (case["id"] + "_" + mode + ".json")).write_text(
                json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            rows.append(row)
            log("CASE_END " + case["id"] + " " + mode + " passed=" + str(row["passed"]))
    summary = {
        "revision": protocol["revision"],
        "passed": {
            mode: sum(row["passed"] for row in rows if row["mode"] == mode)
            for mode in ("rule", "llm")
        },
        "cases_per_mode": len(CASES),
        "planner_calls": sum(len(row["model_calls"]) for row in rows),
        "fallback_turns": sum(row["fallback_turns"] for row in rows),
        "fallback_turns_by_mode": {
            mode: sum(row["fallback_turns"] for row in rows if row["mode"] == mode)
            for mode in ("rule", "llm")
        },
        "results": [
            {k: row[k] for k in ("case_id", "mode", "passed", "checks", "fallback_turns")}
            for row in rows
        ],
        "limitations": protocol["limitations"],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("END " + json.dumps(summary["passed"]))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output, LocalQwenModel())), ensure_ascii=False, indent=2))
