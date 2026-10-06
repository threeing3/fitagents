"""Capture actual ordinary/streaming reply payloads using a scripted provider."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.coaching_prompt_payload import serialize_coaching_prompt


def decode_views(packet):
    """Independent test decoder for checking exact field reconstruction."""
    restored = deepcopy(packet)
    restored.pop("_memory_reference_policy", None)
    sources = {item["id"]: item for item in restored["relevant_memories"]}

    def expand(item):
        if not isinstance(item, dict) or "_memory_ref" not in item:
            return item
        source = sources[item["_memory_ref"]]
        fields = item.get("_memory_fields", list(source))
        result = {key: deepcopy(source[key]) for key in fields}
        result.update(
            {
                key: value
                for key, value in item.items()
                if key not in {"_memory_ref", "_memory_fields"}
            }
        )
        return result

    for key in (
        "world_memories",
        "experience_memories",
        "observation_memories",
        "opinion_memories",
    ):
        if key in restored:
            restored[key] = [expand(item) for item in restored[key]]
    guidance = restored.get("strategy_memory_guidance") or {}
    for key in ("successful_strategies", "failed_strategies"):
        if key in guidance:
            guidance[key] = [expand(item) for item in guidance[key]]
    return restored


def test_partial_fields_overrides_and_unknown_records_reconstruct_exactly():
    source = {"id": "m", "summary": "x" * 3000, "content": "y" * 2000, "category": "training"}
    packet = {
        "relevant_memories": [source],
        "world_memories": [dict(source, category="risk", note="different view")],
        "observation_memories": [{"id": "unknown", "summary": "not in sources"}, {"id": []}],
        "strategy_memory_guidance": {
            "failed_strategies": [{"id": "m", "summary": source["summary"], "usage": "avoid"}],
            "policy": "risk wins",
        },
    }
    original = deepcopy(packet)
    serialized = serialize_coaching_prompt({"context_packet": packet})
    assert decode_views(json.loads(serialized)["context_packet"]) == original
    assert packet == original
    assert len(serialized.encode("utf-8")) < len(
        json.dumps({"context_packet": packet}, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


@pytest.mark.parametrize("case", ["short", "duplicate_id", "reserved_policy"])
def test_unhelpful_or_ambiguous_reference_encoding_is_not_used(case):
    memory = {"id": "same", "summary": "x" if case == "short" else "x" * 3000}
    packet = {"relevant_memories": [memory], "world_memories": [deepcopy(memory)]}
    if case == "duplicate_id":
        packet["relevant_memories"].append(dict(memory, summary="conflicting source"))
    if case == "reserved_policy":
        packet["_memory_reference_policy"] = "preexisting source value"
    payload = {"context_packet": packet}
    assert json.loads(serialize_coaching_prompt(payload)) == payload


@pytest.mark.parametrize("stream", [False, True])
def test_reply_entry_sends_one_complete_memory_with_resolvable_views(monkeypatch, stream):
    memory = {
        "id": "risk-1",
        "memory_network": "world",
        "category": "risk",
        "summary": "synthetic constraint",
        "content": "SYNTHETIC_RISK_CONSTRAINT " * 100,
        "evidence": [{"id": "source-1", "table": "synthetic"}],
    }
    packet = {
        "relevant_memories": [memory],
        "world_memories": [deepcopy(memory)],
        "active_risk_notes": [{"note": "protected constraint"}],
        "current_request_policy": {"allow_plan_content": False},
    }
    original = deepcopy(packet)
    received = []

    class Provider:
        settings = SimpleNamespace(chat_model="qwen-plus")

        def has_live_model(self):
            return True

        async def coach_reply(self, system, user):
            received.append(user)
            return "scripted reply"

        async def stream_coach_reply(self, system, user):
            received.append(user)
            yield "scripted reply"

    service = CoachAgentService.__new__(CoachAgentService)
    service.db = None
    service.model_provider = Provider()
    service.cache = SimpleNamespace(get=lambda *args: None, set=lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_get_or_create_profile", lambda user: SimpleNamespace())
    monkeypatch.setattr(service, "_profile_payload", lambda profile: {})
    monkeypatch.setattr(service, "get_active_plan", lambda user: None)
    monkeypatch.setattr(
        "fast_api.app.services.coach_agent.get_adaptive_system_prompt",
        lambda *args: ("", {}),
    )

    async def run():
        if stream:
            return "".join(
                [part async for part in service._coaching_reply_stream(uuid4(), "问题", packet)]
            )
        return await service._coaching_reply(uuid4(), "问题", packet)

    assert asyncio.run(run()) == "scripted reply"
    sent = json.loads(received[0])["context_packet"]
    assert sent["relevant_memories"][0] == memory
    assert sent["world_memories"] == [{"_memory_ref": "risk-1"}]
    assert sent["active_risk_notes"] == packet["active_risk_notes"]
    assert received[0].count("SYNTHETIC_RISK_CONSTRAINT") == 100
    assert packet == original
