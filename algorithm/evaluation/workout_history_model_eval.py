"""Actual history reader and model loop over an isolated synthetic SQLite database."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from algorithm.evaluation.tool_loop_task_eval import RecordedModel
from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.agent_runtime import ToolRegistry, ToolSpec
from fast_api.app.services.llm_agent import LLMAgentService
from fast_api.app.services.workout_history_query import history_query, read_workout_history

USER_ID = UUID("a0000000-0000-0000-0000-000000000001")
OTHER_ID = UUID("a0000000-0000-0000-0000-000000000002")
CASES = (
    {"case_id": "latest_self", "message": "查一下我上次训练多久", "status": "found", "minutes": 18},
    {
        "case_id": "activity_filter",
        "message": "查一下我上次跑步多久",
        "status": "found",
        "minutes": 23,
    },
    {
        "case_id": "no_record",
        "message": "查一下我上次游泳多久",
        "status": "not_found",
        "minutes": None,
    },
    {
        "case_id": "missing_duration",
        "message": "查一下我上次力量训练多久",
        "status": "found",
        "minutes": None,
    },
)


def seed_database(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                models.User(
                    id=USER_ID, email="self@synthetic.test", password_hash="not-a-credential"
                ),
                models.User(
                    id=OTHER_ID, email="other@synthetic.test", password_hash="not-a-credential"
                ),
            ]
        )
        for index, (uid, name, minutes, day) in enumerate(
            [
                (USER_ID, "跑步", 23, 1),
                (USER_ID, "力量训练", None, 2),
                (USER_ID, "骑行", 18, 3),
                (OTHER_ID, "跑步", 99, 4),
            ]
        ):
            db.add(
                models.WorkoutLog(
                    id=UUID(int=(10 << 124) + 100 + index),
                    user_id=uid,
                    workout_name=name,
                    duration_minutes=minutes,
                    performed_at=datetime(2026, 6, day, 12),
                )
            )
        db.commit()


def snapshot(db):
    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "name": r.workout_name,
            "minutes": r.duration_minutes,
            "performed_at": r.performed_at.isoformat(),
        }
        for r in db.scalars(select(models.WorkoutLog).order_by(models.WorkoutLog.id)).all()
    ]


class ScriptedReader:
    """Only validates evaluator plumbing, never model quality."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content='<tool_call>{"name":"training.log.read","input":{}}</tool_call>'
            )
        observation = json.loads(messages[-1].content.split("\n")[1])
        return SimpleNamespace(
            content=json.dumps(
                {
                    "status": observation["status"],
                    "duration_minutes": observation.get("duration_minutes"),
                }
            )
        )


async def evaluate_case(engine, case, model, *, mode):
    with Session(engine) as db:
        before = snapshot(db)
        query = history_query(case["message"])
        if query is None:
            raise ValueError("Frozen self-history query was not recognized")
        trace = []

        def read(inputs):
            output = read_workout_history(db, USER_ID, query)
            trace.append({"input": dict(inputs), "output": output})
            return output

        registry = ToolRegistry()
        # Match the current production contract: host-owned user/query; empty open inputs.
        registry.register(
            ToolSpec(
                name="training.log.read",
                description="Read this user's prior workout before current-turn writes.",
                input_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "required": ["status", "reply"],
                    "properties": {"status": {"type": "string"}, "reply": {"type": "string"}},
                },
            ),
            read,
        )
        recorded = RecordedModel(model, case["case_id"])
        provider = SimpleNamespace(
            settings=SimpleNamespace(chat_model="unknown", llm_provider=mode),
            chat_model=lambda **_: recorded,
        )
        agent = LLMAgentService(
            db,
            provider,
            registry,
            USER_ID,
            uuid4(),
            SimpleNamespace(injuries=[], goal="maintenance"),
            case["message"] + "。必须通过工具读取，不猜测。最后仅输出JSON，"
            "字段为status、duration_minutes；时长未知则为null。",
        )
        result = await agent.run()
        try:
            answer = json.loads(result.final_response)
        except (ValueError, TypeError):
            answer = None
        expected = {"status": case["status"], "duration_minutes": case["minutes"]}
        outputs = [entry["output"] for entry in trace]
        checks = {
            "tool_executed": bool(trace),
            "no_runtime_error": result.error is None,
            "query_result_correct": bool(outputs)
            and all(
                {"status": out["status"], "duration_minutes": out.get("duration_minutes")}
                == expected
                for out in outputs
            ),
            "answer_correct": answer == expected,
            "no_other_user_record": all(
                out.get("record_id") != str(UUID(int=(10 << 124) + 103)) for out in outputs
            ),
            "database_unchanged": before == snapshot(db),
        }
        return {
            "case_id": case["case_id"],
            "mode": mode,
            "query": query,
            "expected": expected,
            "passed": all(checks.values()),
            "checks": checks,
            "handler_trace": trace,
            "tool_calls": result.tool_calls,
            "model_calls": recorded.calls,
            "final_response": result.final_response,
            "error": result.error,
        }


async def run(output, *, local=False):
    output.mkdir(parents=True, exist_ok=False)
    engine = create_engine("sqlite:///" + (output / "synthetic.sqlite").resolve().as_posix())
    mode = "local_model_actual_reader_sqlite" if local else "scripted_model_actual_reader_sqlite"
    with (output / "run.log").open("w", encoding="utf-8") as handle:

        def log(message):
            line = datetime.now(timezone.utc).isoformat() + " " + message
            handle.write(line + "\n")
            handle.flush()
            print(line, flush=True)

        try:
            log("START " + mode + "; 4 fixed cases; no production data")
            seed_database(engine)
            with Session(engine) as db:
                (output / "fixture.json").write_text(
                    json.dumps(snapshot(db), ensure_ascii=False, indent=2), encoding="utf-8"
                )
            model = None
            if local:
                from algorithm.evaluation.local_qwen_model import LocalQwenModel

                model = LocalQwenModel()
            rows = []
            for case in CASES:
                log("CASE_START " + case["case_id"])
                row = await evaluate_case(engine, case, model or ScriptedReader(), mode=mode)
                rows.append(row)
                (output / (case["case_id"] + ".json")).write_text(
                    json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                )
                log("CASE_END " + case["case_id"] + " passed=" + str(row["passed"]))
            report = {
                "mode": mode,
                "cases": len(rows),
                "passed": sum(r["passed"] for r in rows),
                "checks": [{"case_id": r["case_id"], "checks": r["checks"]} for r in rows],
                "limitations": "Four synthetic scenarios; actual reader and model loop, not full chat/API or PostgreSQL deployment.",
            }
            (output / "summary.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log("END passed=" + str(report["passed"]))
            return report
        except Exception as exc:
            log("ERROR " + type(exc).__name__ + "; incomplete, no complete score")
            raise
        finally:
            engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.output, local=args.local))
