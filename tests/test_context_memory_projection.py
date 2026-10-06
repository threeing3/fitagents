"""Keep all memory views aligned with the actual budget-selected candidates."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.context_window_manager import build_context_packet_with_budget


def test_budgeted_packet_cannot_reintroduce_dropped_memory_through_views():
    kept = {
        "id": "keep",
        "memory_network": "world",
        "summary": "current constraint",
        "importance": 0.9,
    }
    dropped = {
        "id": "drop",
        "memory_network": "experience",
        "fact_kind": "failed_strategy",
        "summary": "obsolete payload " * 2000,
        "importance": 0.1,
    }
    packet = {
        "relevant_memories": [kept, dropped],
        "world_memories": [kept],
        "experience_memories": [dropped],
        "observation_memories": [],
        "opinion_memories": [],
        "strategy_memory_guidance": {
            "failed_strategies": [dict(dropped, usage="avoid repeating")],
            "successful_strategies": [],
            "policy": "risks override experience",
        },
        "active_risk_notes": [{"risk_type": "protected"}],
    }
    original = deepcopy(packet)
    compacted, stats = build_context_packet_with_budget(packet, model_name="unknown")
    assert stats["budgets"]["memory"]["truncated"]
    assert [item["id"] for item in compacted["relevant_memories"]] == ["keep"]
    assert compacted["experience_memories"] == []
    assert compacted["strategy_memory_guidance"]["failed_strategies"] == []
    assert compacted["strategy_memory_guidance"]["policy"] == "risks override experience"
    assert compacted["active_risk_notes"] == packet["active_risk_notes"]
    assert packet == original


def test_truncated_text_is_shared_by_all_views_without_mutating_source():
    memory = {
        "id": "large",
        "memory_network": "experience",
        "fact_kind": "failed_strategy",
        "summary": "large summary " * 3000,
        "importance": 0.7,
    }
    packet = {
        "relevant_memories": [memory],
        "experience_memories": [memory],
        "strategy_memory_guidance": {
            "failed_strategies": [dict(memory, usage="avoid")],
            "successful_strategies": [],
        },
    }
    original = deepcopy(packet)
    compacted, _ = build_context_packet_with_budget(packet)
    selected = compacted["relevant_memories"][0]
    assert selected["summary"] != memory["summary"]
    assert compacted["experience_memories"][0]["summary"] == selected["summary"]
    assert (
        compacted["strategy_memory_guidance"]["failed_strategies"][0]["summary"]
        == selected["summary"]
    )
    assert compacted["strategy_memory_guidance"]["failed_strategies"][0]["usage"] == "avoid"
    assert packet == original


def test_packet_without_memory_truncation_is_unchanged():
    packet = {"relevant_memories": [{"id": "small", "summary": "short"}]}
    compacted, stats = build_context_packet_with_budget(packet)
    assert not stats["budgets"]["memory"]["truncated"]
    assert compacted == packet


@pytest.mark.parametrize("prompt_id", ["coach_coaching_reply", "coach_coaching_reply_stream"])
def test_real_business_budget_entry_applies_projection(prompt_id):
    service = CoachAgentService.__new__(CoachAgentService)
    service.model_provider = SimpleNamespace(settings=SimpleNamespace(chat_model="unknown"))
    memory = {
        "id": "large",
        "summary": "long text " * 4000,
        "memory_network": "world",
    }
    packet = {"relevant_memories": [memory], "world_memories": [memory]}
    compacted = service._budget_context_packet(packet, prompt_id)
    assert compacted["_memory_truncated"]
    assert compacted["world_memories"] == compacted["relevant_memories"]
    assert compacted["relevant_memories"][0]["summary"] != memory["summary"]
    assert compacted["context_management"]["scope"] == "single_agent_run_input"
