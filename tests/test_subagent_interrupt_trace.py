"""Real runtime, scoped journals and model callbacks under cancel/resume."""

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.outputs import ChatResult

from fast_api.app.services.domain_subagents import DomainSubagents, project_read
from fast_api.app.services.durable_stream_journal import DurableStreamJournal, read_stream_journal
from fast_api.app.services.model_call_records import ModelCallRecorder, model_recording


class BlockingModel(BaseChatModel):
    started: Any
    cancelled: Any

    @property
    def _llm_type(self):
        return "blocking-local-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("Only the asynchronous test boundary may run")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("Blocking test model unexpectedly returned")


def reader(domain, tool):
    return project_read(
        {"relevant_memories": [], "core_profile": {"goal": "maintenance"}}, domain, tool
    )


def provider(model):
    return SimpleNamespace(
        settings=SimpleNamespace(chat_model="fake"),
        has_live_model=lambda: True,
        chat_model=lambda **kwargs: model,
    )


def test_parent_cancel_persists_failed_child_and_no_later_domain_starts(tmp_path):
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)

    async def run():
        model = BlockingModel(
            started=asyncio.Event(),
            cancelled=asyncio.Event(),
            callbacks=[ModelCallRecorder("fake", "blocking", "chat")],
        )
        manager = DomainSubagents(provider(model), reader)
        manager.runtime.bind(str(identity[1]), str(identity[2]))

        async def consume():
            with model_recording(journal):
                return [
                    event
                    async for event in manager.run({"intent": "weekly_review"}, "合成取消请求")
                ]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(model.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert model.cancelled.is_set()
        rows = manager.runtime.catalog(owner_id=str(identity[1]), session_id=str(identity[2]))
        assert len(rows) == 1 and rows[0]["status"] == "failed"
        assert rows[0]["failure_reason"] == "parent_cancelled"
        return rows[0]

    try:
        child = asyncio.run(run())
    finally:
        journal.close()
    saved = read_stream_journal(*identity, tmp_path)
    assert saved["state"] == "unconfirmed"  # No fabricated overall terminal.
    entries = saved["entries"]
    states = [entry for entry in entries if entry.get("name") == "subagent.lifecycle"]
    assert [entry["status"] for entry in states] == ["pending", "running", "failed"]
    assert states[-1]["details"]["child_id"] == child["child_id"]
    assert states[-1]["details"]["evidence_kind"] == "runtime_observation"
    calls = [
        entry
        for entry in entries
        if entry.get("name") in {"model.start", "model.end", "model.interrupted"}
    ]
    assert len(calls) == 2 and calls[-1]["status"] == "outcome_unknown"
    assert calls[-1]["details"]["local_stop_reason"] == "CancelledError"
    assert calls[-1]["details"]["remote_result_confirmed"] is False
    assert calls[0]["details"]["model_call_id"] == calls[-1]["details"]["model_call_id"]
    assert all(entry["details"]["domain"] == "training" for entry in states)


def test_consumer_close_after_delegation_records_terminal_without_model_call(tmp_path):
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)
    model = FakeListChatModel(
        responses=["unused"], callbacks=[ModelCallRecorder("fake", "test", "chat")]
    )
    manager = DomainSubagents(provider(model), reader)

    async def run():
        with model_recording(journal):
            stream = manager.run({"intent": "training_plan"}, "合成请求")
            await anext(stream)
            await stream.aclose()

    try:
        asyncio.run(run())
    finally:
        journal.close()
    entries = read_stream_journal(*identity, tmp_path)["entries"]
    assert [entry["status"] for entry in entries] == ["pending", "failed"]
    assert entries[-1]["details"]["failure_reason"] == "consumer_closed"


def test_resume_keeps_child_identity_but_records_new_activation_and_steps(tmp_path):
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)
    result = {
        "action": "final",
        "summary": "合成结果",
        "recommendations": [],
        "uncertainties": [],
        "evidence_ids": [],
    }
    model = FakeListChatModel(
        responses=[json.dumps(result)], callbacks=[ModelCallRecorder("fake", "test", "chat")]
    )
    manager = DomainSubagents(provider(model), reader)
    manager.runtime.allow_continuation = True

    async def run():
        with model_recording(journal):
            first = [
                entry
                async for entry in manager.run(
                    {"intent": "training_plan"}, "首次", mode="continuable"
                )
            ]
            child_id = first[-1]["results"][0]["child_id"]
            revision = manager.runtime.children[child_id]["revision"]
            second = [
                entry
                async for entry in manager.run(
                    {"intent": "training_plan"},
                    "继续",
                    roles=["training"],
                    resume_child_id=child_id,
                    expected_revision=revision,
                    mode="continuable",
                )
            ]
            assert second[-1]["results"][0]["child_id"] == child_id
            # An old checkpoint must not initiate a third activation or model call.
            with pytest.raises(ValueError, match="checkpoint_conflict"):
                async for _ in manager.run(
                    {"intent": "training_plan"},
                    "旧版本",
                    roles=["training"],
                    resume_child_id=child_id,
                    expected_revision=revision,
                    mode="continuable",
                ):
                    pass

    try:
        asyncio.run(run())
    finally:
        journal.close()
    entries = read_stream_journal(*identity, tmp_path)["entries"]
    states = [entry for entry in entries if entry.get("name") == "subagent.lifecycle"]
    assert [entry["status"] for entry in states] == ["pending", "running", "completed"] * 2
    calls = [entry for entry in entries if entry.get("name") == "model.start"]
    assert [entry["details"]["activation"] for entry in calls] == [1, 2]
    assert calls[0]["details"]["step_id"] != calls[1]["details"]["step_id"]
