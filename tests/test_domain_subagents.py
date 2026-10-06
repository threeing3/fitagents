"""Scripted protocol tests, not live model or clinical evidence."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from fast_api.app.services.domain_subagents import DomainSubagents, project_read, select_domains


def final(ids=None):
    return {
        "action": "final",
        "summary": "候选建议",
        "recommendations": ["先核对时间限制"],
        "uncertainties": ["记录不足"],
        "evidence_ids": ids or [],
    }


class Provider:
    settings = SimpleNamespace(chat_model="deepseek-chat")

    def __init__(self, decisions=(), live=True):
        self.decisions = iter(decisions)
        self.live = live
        self.calls = 0
        self.messages = []

    def has_live_model(self):
        return self.live

    def chat_model(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages.append(list(messages))
        return SimpleNamespace(content=json.dumps(next(self.decisions), ensure_ascii=False))


def reader(domain, tool):
    return project_read(
        {
            "relevant_memories": [
                {"id": "active", "status": "active", "content": "40分钟"},
                {"id": "old", "status": "superseded"},
            ],
            "recent_training": [{"duration": 40}],
            "core_profile": {"goal": "maintenance"},
        },
        domain,
        tool,
    )


async def collect(manager, packet=None):
    return [entry async for entry in manager.run(packet or {"intent": "training_plan"}, "复盘建议")]


def result(entries):
    return entries[-1]["results"]


def test_domain_selection_keeps_simple_writes_and_safety_on_host():
    assert select_domains({"intent": "training_log"}) == []
    assert (
        select_domains({"intent": "injury_or_risk", "secondary_intents": ["training_plan"]}) == []
    )
    assert select_domains({"intent": "weekly_review"}) == ["training", "nutrition", "recovery"]
    assert select_domains(
        {"intent": "training_plan", "secondary_intents": ["nutrition_advice"]}
    ) == ["training", "nutrition"]


def test_child_reads_then_finishes_and_never_receives_raw_history():
    provider = Provider([{"action": "read", "tool": "records.read"}, final(["active"])])
    entries = asyncio.run(collect(DomainSubagents(provider, reader)))
    assert result(entries)[0]["status"] == "completed"
    assert result(entries)[0]["model_calls"] == 2
    assert any(entry.get("name") == "subagent.tool" for entry in entries)
    assert "old" not in provider.messages[0][-1].content
    assert "tool_result" in provider.messages[1][-1].content
    packet = {"recent_conversation": ["private"], "relevant_memories": []}
    assert "private" not in str(project_read(packet, "training", "constraints.read"))


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "read", "tool": "memory.write"},
        {"action": "read", "tool": "memory.recall", "user_id": "other"},
        final(["old"]),
        final(["invented"]),
    ],
)
def test_invalid_tool_owner_or_reference_cannot_be_accepted(decision):
    entries = asyncio.run(collect(DomainSubagents(Provider([decision]), reader)))
    assert result(entries)[0]["status"] == "failed"


def test_memory_correction_during_child_invalidates_result():
    reads = 0

    def changing_reader(domain, tool):
        nonlocal reads
        data = reader(domain, tool)
        if tool == "memory.recall":
            reads += 1
            if reads > 1:
                data = {"memories": []}
        return data

    entries = asyncio.run(collect(DomainSubagents(Provider([final(["active"])]), changing_reader)))
    assert result(entries)[0]["failure_reason"] == "evidence_changed"


def test_offline_is_skipped_not_fake_model_success():
    provider = Provider(live=False)
    entries = asyncio.run(collect(DomainSubagents(provider, reader), {"intent": "weekly_review"}))
    assert provider.calls == 0
    assert all(row["status"] == "skipped" and not row["model_called"] for row in result(entries))


def test_expired_shared_deadline_makes_no_model_call():
    provider = Provider([final()])
    manager = DomainSubagents(provider, reader)
    manager.deadline = 0
    entries = asyncio.run(collect(manager))
    assert provider.calls == 0
    assert result(entries)[0]["failure_reason"] == "shared_budget_exhausted"


def test_retrieval_rank_changes_are_not_mistaken_for_fact_corrections():
    packet = {
        "relevant_memories": [
            {
                "id": "one",
                "content": "当前事实",
                "final_score": 0.8,
                "retrieval_debug": {"temporal_score": 0.9},
            }
        ]
    }
    before = project_read(packet, "training", "memory.recall")
    packet["relevant_memories"][0]["final_score"] = 0.7
    packet["relevant_memories"][0]["retrieval_debug"]["temporal_score"] = 0.8
    assert project_read(packet, "training", "memory.recall") == before


def test_changed_records_after_read_reject_stale_result():
    reads = 0

    def changing(domain, tool):
        nonlocal reads
        data = reader(domain, tool)
        if tool == "records.read":
            reads += 1
            if reads > 1:
                return {"recent_training": []}
        return data

    provider = Provider([{"action": "read", "tool": "records.read"}, final()])
    entries = asyncio.run(collect(DomainSubagents(provider, changing)))
    assert result(entries)[0]["failure_reason"] == "evidence_changed"


def test_shared_budget_and_child_iteration_limit():
    provider = Provider([{"action": "read", "tool": "records.read"}] * 9)
    entries = asyncio.run(collect(DomainSubagents(provider, reader), {"intent": "weekly_review"}))
    assert provider.calls == 9
    assert all(row["failure_reason"] == "iteration_limit" for row in result(entries))


def test_parent_cancellation_is_not_converted_to_a_result():
    provider = Provider()

    async def cancelled(_messages):
        raise asyncio.CancelledError

    provider.ainvoke = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect(DomainSubagents(provider, reader)))


def test_recovery_failure_does_not_hide_other_successes_or_leak_error():
    provider = Provider([final(), {"action": "read", "tool": "forbidden"}, final()])
    entries = asyncio.run(collect(DomainSubagents(provider, reader), {"intent": "weekly_review"}))
    assert [row["status"] for row in result(entries)] == ["completed", "failed", "completed"]
    assert entries[-2]["details"]["completed"] == 2
    public = [entry for entry in entries if entry["type"] == "execution_event"]
    assert "40分钟" not in str(public)
