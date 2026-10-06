"""Actual model callback protocol with local fake models, no network or keys."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.execution_trace import recorded_execution_trace
from fast_api.app.services.model_call_records import (
    ModelCallRecorder,
    model_recording,
    public_content,
)


def test_real_callback_protocol_records_input_and_end_without_private_fields(tmp_path):
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, session, tmp_path)
    model = FakeListChatModel(
        responses=["public reply"], callbacks=[ModelCallRecorder("fake", "test", "reply")]
    )

    async def invoke():
        with model_recording(journal):
            await model.ainvoke(
                [
                    SystemMessage(content="application instruction"),
                    HumanMessage(content="synthetic request"),
                ]
            )
        # A call outside this scope must not append to the previous owner's journal.
        await model.ainvoke("not scoped")

    asyncio.run(invoke())
    journal.close()
    saved = journals.read_stream_journal(identity, owner, session, tmp_path)
    start, end = saved["entries"]
    assert start["type"] == "model.start" and end["status"] == "completed"
    assert start["details"]["model_call_id"] == end["details"]["model_call_id"]
    assert start["input_summary"]["message_batches"][0][1]["content"] == "synthetic request"
    assert end["details"]["public_outputs"][0]["content"] == "public reply"
    assert start["input_summary"]["exact_request"] is False
    assert "not scoped" not in str(saved)


def test_media_and_hidden_reasoning_blocks_are_not_recorded():
    content = public_content(
        [
            {"type": "text", "text": "public"},
            {"type": "reasoning", "text": "private"},
            {"type": "thinking", "thinking": "private"},
            {"type": "image_url", "image_url": {"url": "data:image/private"}},
        ]
    )
    assert content == [{"type": "text", "text": "public"}, {"type": "image", "media_omitted": True}]


def test_start_persistence_failure_blocks_the_model_boundary(tmp_path, monkeypatch):
    journal = journals.DurableStreamJournal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), tmp_path)
    model = FakeListChatModel(
        responses=["first", "second"], callbacks=[ModelCallRecorder("fake", "test", "reply")]
    )

    def fail(entry):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(journal, "append", fail)

    async def invoke():
        with model_recording(journal):
            await model.ainvoke("must not reach model")

    try:
        with pytest.raises(OSError):
            asyncio.run(invoke())
        assert model.i == 0
    finally:
        journal.close()


def test_concurrent_calls_keep_owner_journals_separate(tmp_path):
    identities = [(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()) for _ in range(2)]
    logs = [journals.DurableStreamJournal(*identity, tmp_path) for identity in identities]
    model = FakeListChatModel(
        responses=["reply"], callbacks=[ModelCallRecorder("fake", "test", "reply")]
    )

    async def run(index):
        with model_recording(logs[index]):
            await asyncio.sleep(0)
            await model.ainvoke(f"owner-input-{index}")

    async def both():
        await asyncio.gather(run(0), run(1))

    try:
        asyncio.run(both())
    finally:
        for log in logs:
            log.close()
    for index, identity in enumerate(identities):
        saved = journals.read_stream_journal(*identity, tmp_path)
        assert len(saved["entries"]) == 2
        assert f"owner-input-{index}" in str(saved)
        assert f"owner-input-{1 - index}" not in str(saved)


def test_error_record_keeps_type_not_sensitive_error_text(tmp_path):
    journal = journals.DurableStreamJournal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), tmp_path)
    recorder = ModelCallRecorder("fake", "test", "reply")
    call = uuid.uuid4()
    with model_recording(journal):
        recorder.on_chat_model_start({}, [[HumanMessage(content="x")]], run_id=call)
        recorder.on_llm_error(RuntimeError("secret request payload"), run_id=call)
    journal.close()
    text = journal.path.read_text(encoding="utf-8")
    assert "RuntimeError" in text and "secret request payload" not in text


@pytest.mark.parametrize("purpose", ["chat", "intent"])
def test_provider_installs_recorder_on_actual_client_factory(tmp_path, monkeypatch, purpose):
    from fast_api.app.core.config import Settings
    from fast_api.app.services import model_provider as providers

    settings = Settings(_env_file=None, llm_provider="offline")
    settings.has_live_model_key = True
    provider = providers.ModelProvider(settings)
    monkeypatch.setattr(provider, "_reserve_live_call", lambda: True)

    def factory(**kwargs):
        assert len(kwargs["callbacks"]) == 1
        assert kwargs["callbacks"][0].purpose == purpose
        kwargs["http_client"].close()
        asyncio.run(kwargs["http_async_client"].aclose())
        return FakeListChatModel(responses=["reply"], callbacks=kwargs["callbacks"])

    monkeypatch.setattr(providers, "ChatOpenAI", factory)
    model = provider.chat_model() if purpose == "chat" else provider.intent_model()
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = journals.DurableStreamJournal(*identity, tmp_path)

    async def invoke():
        with model_recording(journal):
            await model.ainvoke("factory input")

    try:
        asyncio.run(invoke())
    finally:
        journal.close()
    entries = journals.read_stream_journal(*identity, tmp_path)["entries"]
    assert len(entries) == 2 and entries[0]["details"]["purpose"] == purpose


@pytest.mark.parametrize("streaming", [False, True])
def test_public_chat_entry_links_model_records_to_owner_trace(tmp_path, monkeypatch, streaming):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = models.User(email="model-trace@example.test", password_hash="synthetic")
        db.add(user)
        db.flush()
        session = models.ConversationSession(user_id=user.id, title="model calls")
        db.add(session)
        db.commit()
        service = object.__new__(CoachAgentService)
        service.db = db
        fake = FakeListChatModel(
            responses=["reply"], callbacks=[ModelCallRecorder("fake", "test", "reply")]
        )

        async def perform(*args):
            await fake.ainvoke([HumanMessage(content="visible input")])
            run = models.AgentRun(user_id=user.id, session_id=session.id, run_type="chat", nodes=[])
            db.add(run)
            db.commit()
            return {"agent_run_id": str(run.id), "assistant_message": "reply"}

        async def stream(*args):
            result = await perform()
            yield json.dumps({"type": "done", "run_id": result["agent_run_id"]}) + "\n"

        monkeypatch.setattr(service, "_handle_chat_message_once", perform)
        monkeypatch.setattr(service, "_stream_chat_events_once", stream)

        async def invoke():
            if streaming:
                events = [
                    json.loads(raw)
                    async for raw in service.stream_chat_events(session.id, user.id, "x", "scoped")
                ]
                return events[-1]["run_id"]
            result = await service.handle_chat_message(session.id, user.id, "x", "scoped")
            return result["agent_run_id"]

        run_id = asyncio.run(invoke())
        trace = recorded_execution_trace(db, uuid.UUID(run_id), user.id)
        starts = [event for event in trace["events"] if event["name"] == "model.start"]
        assert len(starts) == 1
        assert "visible input" in str(starts[0]["input"])
        assert any(event["name"] == "model.end" for event in trace["events"])
        assert trace["coverage"]["exact_model_requests"] is False
    engine.dispose()
