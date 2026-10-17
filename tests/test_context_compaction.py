import asyncio
import copy
import json

import pytest

from fast_api.app.services.context_compaction import (
    SUMMARY_FIELDS,
    CompactionPolicy,
    ContextCompactor,
    balanced_boundaries,
    parse_summary,
    prune_tool_result,
    select_range,
)
from fast_api.app.services.context_window_manager import (
    ContextWindowManager,
    build_context_packet_with_budget,
)


def history():
    return [
        {"id": str(i), "role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3000}
        for i in range(8)
    ]


def summary():
    return json.dumps({key: ["Preserve Wednesday rest constraint"] for key in SUMMARY_FIELDS})


def test_recent_history_direction():
    manager = ContextWindowManager("unknown")
    manager.set_history([{"id": key, "content": key * 5000} for key in "ABC"])
    assert [item["id"] for item in manager.budgets["history"].items] == ["C"]


def test_knowledge_projection_is_replaced():
    packet = {"knowledge_context": {"explanation_knowledge": [{"content": "x" * 20000}]}}
    result, _ = build_context_packet_with_budget(packet, "unknown")
    assert result["_knowledge_truncated"]
    assert len(result["knowledge_context"]["explanation_knowledge"][0]["content"]) < 20000


def test_policy_rejects_small_capacity_with_excessive_headroom():
    with pytest.raises(ValueError):
        CompactionPolicy(8000, 1200, 65536)


def test_tool_pair_remains_balanced():
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "tool_calls": [{"id": "read-1"}], "content": ""},
        {"role": "tool", "tool_call_id": "read-1", "content": "result"},
        {"role": "user", "content": "current"},
    ]
    assert balanced_boundaries(messages) == {0, 1, 3, 4}
    assert select_range(messages, 1) == (0, 3)
    with pytest.raises(ValueError, match="Orphan"):
        balanced_boundaries([messages[2]])


def test_unclosed_call_is_never_split():
    messages = [{"role": "user"}, {"role": "assistant", "tool_calls": [{"id": "pending"}]}]
    assert select_range(messages, 1) is None


def test_runtime_protected_request_does_not_block_old_tool_pairs():
    messages = [
        {"id": "system", "role": "system", "content": "rules"},
        {"id": "request", "role": "user", "content": "review", "context_protected": True},
        {"id": "c1", "role": "assistant", "tool_calls": [{"id": "one"}]},
        {"id": "r1", "role": "tool", "tool_call_id": "one", "content": "x" * 4000},
        {"id": "c2", "role": "assistant", "tool_calls": [{"id": "two"}]},
        {"id": "r2", "role": "tool", "tool_call_id": "two", "content": "recent"},
    ]
    assert select_range(messages, 1) == (2, 4)
    # A previously installed checkpoint is not a new user request either.
    messages[2:4] = [{"id": "checkpoint", "role": "user", "context_checkpoint": True}]
    assert select_range(messages, 1) == (2, 3)


def test_prune_preserves_identity_and_original():
    original = {"id": "tool-result-1", "role": "tool", "content": "HEAD" + "x" * 1000 + "TAIL"}
    before = copy.deepcopy(original)
    view = prune_tool_result(original, 200)
    assert original == before
    assert view["content"].startswith("HEAD") and view["content"].endswith("TAIL")
    assert len(view["content"]) <= 200
    assert view["source_message_id"] == original["id"]


def test_summary_contract_rejects_partial_fields():
    with pytest.raises(ValueError):
        parse_summary('{"request_and_intent": ["hello"]}')


def test_transaction_installs_only_after_summary_record():
    async def run():
        events = []
        installed = []
        messages = history()
        before = copy.deepcopy(messages)

        async def emit(event):
            events.append(event)

        async def summarize(request):
            assert "Return only a JSON" in request[-1]["content"]
            return summary()

        async def install(ids, checkpoint):
            assert events[-1]["type"] == "compaction/summary"
            installed.append((ids, checkpoint))

        engine = ContextCompactor(CompactionPolicy(20000, 2000, 1000))
        result = await engine.compact(
            messages, request_units=25000, summarize=summarize, emit=emit, install=install
        )
        assert messages == before
        assert result["context_checkpoint"] and installed
        assert "6" not in installed[0][0]  # Latest user request is retained verbatim.
        assert events[-1]["status"] == "completed"

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_summary_keeps_original_and_records_end(cancel):
    async def run():
        events = []
        installed = []

        async def emit(event):
            events.append(event)

        async def summarize(request):
            if cancel:
                raise asyncio.CancelledError
            return "{}"

        async def install(ids, checkpoint):
            installed.append(checkpoint)

        expected = asyncio.CancelledError if cancel else ValueError
        engine = ContextCompactor(CompactionPolicy(20000, 2000, 1000))
        with pytest.raises(expected):
            await engine.compact(
                history(), request_units=25000, summarize=summarize, emit=emit, install=install
            )
        assert not installed
        assert events[-1]["status"] == ("cancelled" if cancel else "failed")
        assert events[-1]["installed"] is False

    asyncio.run(run())


def test_pressure_noop_has_no_events():
    async def run():
        async def forbidden(*args):
            raise AssertionError("No event or call allowed below pressure threshold")

        engine = ContextCompactor(CompactionPolicy(20000, 2000, 1000))
        assert (
            await engine.compact(
                history(),
                request_units=1000,
                summarize=forbidden,
                emit=forbidden,
                install=forbidden,
            )
            is None
        )

    asyncio.run(run())
