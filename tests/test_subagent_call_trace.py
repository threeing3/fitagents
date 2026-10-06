"""Real child loop and callback protocol; deterministic local model, no network."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from fast_api.app.services.domain_subagents import DomainSubagents, project_read
from fast_api.app.services.durable_stream_journal import DurableStreamJournal, read_stream_journal
from fast_api.app.services.model_call_records import ModelCallRecorder, model_recording
from fast_api.app.services.review_collaboration import ReviewCollaboration


def final():
    return {
        "action": "final",
        "summary": "合成证据复盘",
        "recommendations": ["保留原约束"],
        "uncertainties": ["缺少更多记录"],
        "evidence_ids": [],
    }


class Provider:
    settings = SimpleNamespace(chat_model="fake")

    def __init__(self, decisions, live=True):
        self.live = live
        self.model = FakeListChatModel(
            responses=[json.dumps(row, ensure_ascii=False) for row in decisions],
            callbacks=[ModelCallRecorder("fake", "test", "chat")],
        )

    def has_live_model(self):
        return self.live

    def chat_model(self, **kwargs):
        return self.model


def reader(domain, tool):
    return project_read(
        {
            "relevant_memories": [],
            "recent_training": [{"duration": 30}],
            "core_profile": {"goal": "maintenance"},
        },
        domain,
        tool,
    )


def collect(tmp_path, provider, packet, read=reader, *, collaboration=False):
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)
    manager = (
        ReviewCollaboration(provider, read) if collaboration else DomainSubagents(provider, read)
    )
    runtime = manager.worker.runtime if collaboration else manager.runtime
    runtime.bind(str(identity[1]), str(identity[2]))

    async def run():
        with model_recording(journal):
            events = [event async for event in manager.run(packet, "合成请求")]
            # Parent resumes after the child loop; this must have no child origin.
            await provider.model.ainvoke([HumanMessage(content="parent-only input")])
            return events

    try:
        events = asyncio.run(run())
    finally:
        journal.close()
    return manager, events, read_stream_journal(*identity, tmp_path)["entries"]


def test_three_domains_link_models_and_actual_reads_to_their_own_children(tmp_path):
    manager, events, entries = collect(tmp_path, Provider([final()]), {"intent": "weekly_review"})
    results = events[-1]["results"]
    assert len(results) == 3 and all(row["status"] == "completed" for row in results)
    for result in results:
        child = [
            entry
            for entry in entries
            if entry.get("details", {}).get("child_id") == result["child_id"]
        ]
        starts = [entry for entry in child if entry["name"] == "model.start"]
        ends = [entry for entry in child if entry["name"] == "model.end"]
        assert len(starts) == len(ends) == 1
        assert starts[0]["details"]["model_call_id"] == ends[0]["details"]["model_call_id"]
        assert starts[0]["details"]["parent_id"] == manager.runtime.parent_id
        assert starts[0]["details"]["model_iteration"] == 1
        reads = [entry for entry in child if entry["name"] == "subagent.read.end"]
        assert len(reads) == 4 and all(entry["status"] == "completed" for entry in reads)
        assert any(entry["details"]["read_phase"] == "acceptance_recheck" for entry in reads)
        assert all("observation" in entry["details"] for entry in reads)
    parent = [
        entry
        for entry in entries
        if entry.get("name") == "model.start" and not entry["details"].get("child_id")
    ]
    assert len(parent) == 1 and "parent-only input" in str(parent[0])


def test_read_then_final_keeps_two_model_iterations_and_tool_step_pairing(tmp_path):
    _, events, entries = collect(
        tmp_path,
        Provider([{"action": "read", "tool": "records.read"}, final()]),
        {"intent": "training_plan"},
    )
    result = events[-1]["results"][0]
    assert result["model_calls"] == 2 and result["status"] == "completed"
    starts = [
        entry
        for entry in entries
        if entry["name"] == "model.start" and entry["details"].get("child_id")
    ]
    assert [entry["details"]["model_iteration"] for entry in starts] == [1, 2]
    reads = [entry for entry in entries if entry["name"].startswith("subagent.read.")]
    for start in (entry for entry in reads if entry["name"].endswith("start")):
        end = [
            entry
            for entry in reads
            if entry["name"].endswith("end")
            and entry["details"]["step_id"] == start["details"]["step_id"]
        ]
        assert len(end) == 1
        assert end[0]["details"]["tool_name"] == start["details"]["tool_name"]


def test_read_error_is_visible_but_sensitive_error_text_is_not(tmp_path):
    def failing(domain, tool):
        raise RuntimeError("private evidence and credential")

    _, events, entries = collect(
        tmp_path, Provider([final()]), {"intent": "training_plan"}, failing
    )
    assert events[-1]["results"][0]["status"] == "failed"
    failures = [entry for entry in entries if entry["name"] == "subagent.read.end"]
    assert len(failures) == 1 and failures[0]["status"] == "failed"
    assert failures[0]["details"]["error_type"] == "RuntimeError"
    assert "private evidence and credential" not in str(entries)


def test_analysis_domains_and_planning_handoffs_have_explicit_origin(tmp_path):
    manager, events, entries = collect(
        tmp_path, Provider([final()]), {"intent": "weekly_review"}, collaboration=True
    )
    results = events[-1]["results"]
    assert [row["domain"] for row in results] == [
        "evidence_analysis",
        "training",
        "nutrition",
        "recovery",
        "plan_planning",
    ]
    assert all(row["status"] == "completed" for row in results)
    starts = [
        entry
        for entry in entries
        if entry["name"] == "model.start" and entry["details"].get("child_id")
    ]
    assert len(starts) == 5
    assert {entry["details"]["child_id"] for entry in starts} == {
        row["child_id"] for row in results
    }
    refreshes = [
        entry
        for entry in entries
        if entry["name"] == "subagent.read.end"
        and entry["details"]["read_phase"] == "handoff_recheck"
    ]
    assert refreshes
    assert all(
        entry["details"]["parent_id"] == manager.worker.runtime.parent_id for entry in refreshes
    )
    assert {entry["details"]["domain"] for entry in refreshes} == {row["domain"] for row in results}


@pytest.mark.parametrize("live", [False, True])
def test_offline_or_disallowed_write_never_creates_a_write_receipt(tmp_path, live):
    _, events, entries = collect(
        tmp_path,
        Provider([{"action": "read", "tool": "memory.write"}], live),
        {"intent": "training_plan"},
    )
    result = events[-1]["results"][0]
    assert result["status"] == ("failed" if live else "skipped")
    assert not any(entry.get("details", {}).get("tool_name") == "memory.write" for entry in entries)
    if not live:
        child_entries = [entry for entry in entries if entry.get("details", {}).get("child_id")]
        assert [entry["status"] for entry in child_entries] == ["pending", "skipped"]
        assert all(entry["name"] == "subagent.lifecycle" for entry in child_entries)


def test_history_projection_preserves_actual_child_model_and_read_origin(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from fast_api.app.db import models
    from fast_api.app.db.database import Base
    from fast_api.app.services import durable_stream_journal as journals
    from fast_api.app.services.execution_trace import recorded_execution_trace

    manager, events, _ = collect(tmp_path, Provider([final()]), {"intent": "training_plan"})
    path = next((tmp_path / "streams").glob("*.jsonl"))
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(
            id=uuid.UUID(header["owner_id"]),
            email="child-history@example.test",
            password_hash="synthetic",
        )
        db.add(user)
        db.flush()
        session = models.ConversationSession(
            id=uuid.UUID(header["session_id"]), user_id=user.id, title="children"
        )
        db.add(session)
        db.flush()
        catalog = models.AgentRun(
            id=uuid.UUID(manager.runtime.parent_id),
            user_id=user.id,
            session_id=session.id,
            run_type="subagent_catalog",
            nodes=[{"children": list(manager.runtime.children.values())}],
        )
        run = models.AgentRun(
            user_id=user.id,
            session_id=session.id,
            run_type="chat",
            nodes=[{"type": "DurableStreamJournal", "journal_id": header["journal_id"]}],
        )
        db.add_all([run, catalog])
        db.commit()
        trace = recorded_execution_trace(db, run.id, user.id)
        child_id = events[-1]["results"][0]["child_id"]
        model = next(
            entry
            for entry in trace["events"]
            if entry["name"] == "model.start" and entry["child_id"] == child_id
        )
        assert model["parent_id"] == manager.runtime.parent_id
        assert model["step_id"]
        assert model["input"]["message_batches"]
        reads = [entry for entry in trace["events"] if entry["name"] == "subagent.read.end"]
        assert len(reads) == 4 and all(entry["child_id"] == child_id for entry in reads)
        assert any(entry["name"] == "SubagentCheckpoint" for entry in trace["events"])
        assert not db.new and not db.dirty
    engine.dispose()
