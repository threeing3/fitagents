"""Final-state diagnostics: real runtime, synthetic tools, isolated SQLite state."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from fast_api.app.services.agent_runtime import (
    AgentExecutor,
    AgentTaskTimeline,
    ToolRegistry,
    ToolSpec,
)
from fast_api.app.services.agent_task_state import AgentTaskStateService


def test_training_issue_message_creates_experiment_signal():
    service = AgentTaskStateService(db=None)

    assert service._looks_like_training_experiment(
        "今天练胸，卧推55kg后后续动作没有力量，质量不好",
        "training_log",
    )


def test_goal_next_actions_include_weekly_review_for_fat_loss():
    service = AgentTaskStateService(db=None)
    profile = SimpleNamespace(goal="fat_loss")

    actions = service._next_actions_for_goal(profile)

    assert any(action["action"] == "weekly_review" for action in actions)


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE training_plan (id INTEGER PRIMARY KEY, minutes INTEGER, constraint_note TEXT);
        INSERT INTO training_plan VALUES (1, 45, 'avoid jumping');
        CREATE TABLE training_log (id INTEGER PRIMARY KEY, load_kg REAL);
        INSERT INTO training_log VALUES (1, 55);
        CREATE TABLE write_events (id INTEGER PRIMARY KEY, request_id TEXT);
        """
    )
    yield connection
    connection.close()


def run_tool(registry, name, payload, database, record_property):
    timeline = AgentTaskTimeline("synthetic final-state diagnostic", request_id="state-01")
    step = timeline.add_step("Execute diagnostic tool", name)
    result = asyncio.run(AgentExecutor().execute(registry, timeline, step, payload))
    snapshot = {
        "plan": database.execute("SELECT * FROM training_plan").fetchall(),
        "training_log": database.execute("SELECT * FROM training_log").fetchall(),
        "writes": database.execute("SELECT * FROM write_events").fetchall(),
        "trace": result.result.to_trace(),
        "timeline": timeline.to_dict(),
    }
    record_property("final_state", json.dumps(snapshot, ensure_ascii=False))
    return result, snapshot


def write_spec(**overrides):
    fields = {
        "name": "diagnostic.write",
        "description": "Synthetic write with controlled failures",
        "permission_level": "write",
        "side_effects": True,
        "retry_count": 2,
        "idempotency_key_fields": ["request_id"],
        "input_schema": {
            "type": "object",
            "required": ["request_id"],
            "properties": {"request_id": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
    }
    return ToolSpec(**(fields | overrides))


@pytest.mark.parametrize("failure", ["lost_response", "invalid_output", "before_write"])
@pytest.mark.parametrize("retries", [0, 2])
def test_write_failure_does_not_repeat_or_claim_completion(
    database, record_property, failure, retries
):
    registry = ToolRegistry()
    calls = []

    def handler(payload):
        calls.append(payload)
        if failure == "before_write":
            raise ConnectionError("injected failure before commit")
        database.execute(
            "INSERT INTO write_events(request_id) VALUES (?)", (payload["request_id"],)
        )
        database.commit()
        if failure == "lost_response":
            raise TimeoutError("commit succeeded but response lost")
        return {"malformed": True}

    registry.register(write_spec(retry_count=retries), handler)
    execution, snapshot = run_tool(
        registry, "diagnostic.write", {"request_id": "r1"}, database, record_property
    )

    assert len(calls) == 1
    assert len(snapshot["writes"]) == (0 if failure == "before_write" else 1)
    assert execution.result.attempts == 1
    assert execution.result.status == "outcome_unknown"
    assert execution.completed_event["status"] == "failed"
    assert snapshot["plan"] == [(1, 45, "avoid jumping")]


def test_async_write_lost_response_is_not_replayed(database, record_property):
    registry = ToolRegistry()

    async def handler(payload):
        database.execute(
            "INSERT INTO write_events(request_id) VALUES (?)", (payload["request_id"],)
        )
        database.commit()
        await asyncio.sleep(0)
        raise TimeoutError("asynchronous response lost after commit")

    registry.register(write_spec(), handler)
    execution, snapshot = run_tool(
        registry, "diagnostic.write", {"request_id": "async-1"}, database, record_property
    )
    assert execution.result.status == "outcome_unknown"
    assert execution.result.attempts == 1
    assert execution.completed_event["status"] == "failed"
    assert snapshot["writes"] == [(1, "async-1")]


@pytest.mark.parametrize(
    "retries,expected_status,expected_calls", [(0, "error", 1), (1, "success", 2)]
)
def test_read_only_retry_can_recover(
    database, record_property, retries, expected_status, expected_calls
):
    registry = ToolRegistry()
    calls = []

    def handler(_payload):
        calls.append(True)
        if len(calls) == 1:
            raise ConnectionError("injected transient read failure")
        return {"minutes": database.execute("SELECT minutes FROM training_plan").fetchone()[0]}

    registry.register(
        ToolSpec(name="diagnostic.read", description="Read plan", retry_count=retries), handler
    )
    execution, snapshot = run_tool(registry, "diagnostic.read", {}, database, record_property)
    assert execution.result.status == expected_status
    assert execution.result.attempts == expected_calls == len(calls)
    assert snapshot["writes"] == []
    assert snapshot["plan"] == [(1, 45, "avoid jumping")]
    if expected_status == "success":
        assert execution.result.output_json["minutes"] == 45


def test_missing_input_never_calls_writer(database, record_property):
    registry = ToolRegistry()
    calls = []
    registry.register(write_spec(), lambda payload: calls.append(payload))
    execution, snapshot = run_tool(registry, "diagnostic.write", {}, database, record_property)
    assert execution.result.status == "schema_error"
    assert execution.result.attempts == 0
    assert calls == []
    assert snapshot["writes"] == []


def test_plan_adjustment_preserves_constraint_and_other_records(database, record_property):
    registry = ToolRegistry()

    def adjust(_payload):
        database.execute("UPDATE training_plan SET minutes = 20 WHERE id = 1")
        database.commit()
        return {"ok": True}

    registry.register(write_spec(), adjust)
    execution, snapshot = run_tool(
        registry, "diagnostic.write", {"request_id": "adjust-1"}, database, record_property
    )
    assert execution.result.status == "success"
    assert execution.completed_event["status"] == "completed"
    assert snapshot["plan"] == [(1, 20, "avoid jumping")]
    assert snapshot["training_log"] == [(1, 55.0)]


def test_record_correction_updates_existing_row(database, record_property):
    registry = ToolRegistry()

    def correct(_payload):
        database.execute("UPDATE training_log SET load_kg = 50 WHERE id = 1")
        database.commit()
        return {"ok": True}

    registry.register(write_spec(), correct)
    execution, snapshot = run_tool(
        registry, "diagnostic.write", {"request_id": "correct-1"}, database, record_property
    )
    assert execution.result.status == "success"
    assert snapshot["training_log"] == [(1, 50.0)]
    assert snapshot["plan"] == [(1, 45, "avoid jumping")]


def test_output_repair_does_not_reinvoke_writer(database, record_property):
    registry = ToolRegistry()

    def handler(payload):
        database.execute(
            "INSERT INTO write_events(request_id) VALUES (?)", (payload["request_id"],)
        )
        database.commit()
        return {"committed": True}

    def repair(payload):
        return {"output_json": {"ok": payload["output_json"]["committed"]}}

    registry.register(write_spec(), handler, repair_handler=repair)
    execution, snapshot = run_tool(
        registry, "diagnostic.write", {"request_id": "repair-1"}, database, record_property
    )
    assert execution.result.status == "success"
    assert execution.result.repaired
    assert execution.result.attempts == 1
    assert snapshot["writes"] == [(1, "repair-1")]


def test_separate_requests_are_not_deduplicated_by_trace_key(database, record_property):
    """Known boundary, not a claim of application-level idempotency."""
    registry = ToolRegistry()

    def handler(payload):
        database.execute(
            "INSERT INTO write_events(request_id) VALUES (?)", (payload["request_id"],)
        )
        database.commit()
        return {"ok": True}

    registry.register(write_spec(retry_count=0), handler)
    first, _ = run_tool(
        registry, "diagnostic.write", {"request_id": "same-request"}, database, record_property
    )
    second, snapshot = run_tool(
        registry, "diagnostic.write", {"request_id": "same-request"}, database, record_property
    )
    assert first.result.idempotency_key == second.result.idempotency_key
    assert len(snapshot["writes"]) == 2
    record_property("known_gap", "separate execute calls are not deduplicated")
