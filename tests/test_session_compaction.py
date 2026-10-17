import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.context_compaction import SUMMARY_FIELDS
from fast_api.app.services.context_compaction_store import CompactionBusy, ContextCompactionStore
from fast_api.app.services.session_compaction import (
    compact_runtime_context,
    prepare_session_context,
    session_surface,
)


@pytest.fixture
def context_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "context.db"))
    Base.metadata.create_all(
        engine,
        tables=[
            models.User.__table__,
            models.ConversationSession.__table__,
            models.ChatMessage.__table__,
            models.ContextCheckpointState.__table__,
            models.ContextCompactionEvent.__table__,
        ],
    )
    owner, session_id = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add(models.User(id=owner, email="context@example.test", password_hash="fixture"))
        db.add(models.ConversationSession(id=session_id, user_id=owner))
        for i in range(10):
            db.add(
                models.ChatMessage(
                    id=uuid.uuid4(),
                    user_id=owner,
                    session_id=session_id,
                    role="user" if i % 2 == 0 else "assistant",
                    content="周三休息，只做力量训练。" * 90,
                    created_at=datetime(2026, 10, 1) + timedelta(minutes=i),
                )
            )
        db.commit()
    with Session(engine) as db:
        yield db, owner, session_id
    engine.dispose()


class Provider:
    settings = SimpleNamespace(chat_model="unknown")

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def has_live_model(self):
        return True

    async def summarize_context(self, request):
        self.calls += 1
        if self.fail:
            return "{}"
        return json.dumps(
            {key: ["周三休息；只做力量训练；未获得修改授权"] for key in SUMMARY_FIELDS},
            ensure_ascii=False,
        )


def test_runtime_checkpoint_persists_source_and_preserves_recent_pair(context_db):
    db, owner, session_id = context_db
    messages = [
        {"id": "s", "role": "system", "content": "rules"},
        {"id": "u", "role": "user", "content": "review", "context_protected": True},
        {"id": "c1", "role": "assistant", "tool_calls": [{"id": "one"}]},
        {"id": "r1", "role": "tool", "tool_call_id": "one", "content": "x" * 9000},
        {"id": "c2", "role": "assistant", "tool_calls": [{"id": "two"}]},
        {"id": "r2", "role": "tool", "tool_call_id": "two", "content": "recent" * 240},
    ]
    result = asyncio.run(
        compact_runtime_context(
            db, Provider(), owner, session_id, "synthetic-run", messages, trigger="manual"
        )
    )
    assert [item["id"] for item in result[:2]] == ["s", "u"]
    assert result[2]["context_checkpoint"]
    assert result[-2:] == messages[-2:]
    state = db.get(models.ContextCheckpointState, "runtime:synthetic-run")
    assert state.revision == 2
    events = list(db.scalars(select(models.ContextCompactionEvent)))
    assert events[0].payload["input_messages"] == messages
    assert events[-1].payload["installed"]
    assert messages[3]["content"] == "x" * 9000


def test_large_tool_view_is_durable_without_paid_summary(context_db):
    db, owner, session_id = context_db
    provider = Provider()
    messages = [
        {"id": "s", "role": "system", "content": "rules"},
        {"id": "u", "role": "user", "content": "read", "context_protected": True},
        {"id": "c", "role": "assistant", "tool_calls": [{"id": "read"}]},
        {
            "id": "r",
            "role": "tool",
            "tool_call_id": "read",
            "content": "HEAD" + "x" * 40000 + "TAIL",
        },
    ]
    result = asyncio.run(
        compact_runtime_context(db, provider, owner, session_id, "pruning-run", messages)
    )
    assert provider.calls == 0
    assert result[-1]["tool_call_id"] == "read" and result[-1]["context_view_partial"]
    assert result[-1]["content"].startswith("HEAD") and result[-1]["content"].endswith("TAIL")
    assert len(messages[-1]["content"]) == 40008
    events = list(db.scalars(select(models.ContextCompactionEvent)))
    assert events[0].payload["input_messages"] == messages
    state = db.get(models.ContextCheckpointState, "runtime:pruning-run")
    assert state.checkpoint["context_view"] == result


def test_persistent_checkpoint_and_original_history(context_db):
    db, owner, session_id = context_db
    original = session_surface(db, owner, session_id)
    result = asyncio.run(prepare_session_context(db, Provider(), owner, session_id, "下周照这个来"))
    assert result["status"] == "completed"
    surface = session_surface(db, owner, session_id)
    assert surface[0]["context_checkpoint"]
    assert surface[-1]["id"] == original[-1]["id"]
    assert list(db.scalars(select(models.ChatMessage.content))) == [
        item["content"] for item in original
    ]
    events = list(db.scalars(select(models.ContextCompactionEvent)))
    assert len(events) % 4 == 0
    assert [row.event_type for row in events[:4]] == [
        "compaction/start",
        "compaction/summary",
        "compaction/installed",
        "compaction/end",
    ]
    assert events[-1].payload["installed"]
    provider = Provider()
    again = asyncio.run(prepare_session_context(db, provider, owner, session_id, "继续"))
    assert again["status"] == "skipped" and provider.calls == 0


def test_failure_does_not_hide_originals(context_db):
    db, owner, session_id = context_db
    before = session_surface(db, owner, session_id)
    result = asyncio.run(
        prepare_session_context(db, Provider(fail=True), owner, session_id, "继续")
    )
    assert result["status"] == "failed"
    assert session_surface(db, owner, session_id) == before
    assert list(db.scalars(select(models.ContextCompactionEvent)))[-1].payload["status"] == "failed"


def test_other_owner_cannot_compact(context_db):
    db, _, session_id = context_db
    with pytest.raises(PermissionError):
        asyncio.run(prepare_session_context(db, Provider(), uuid.uuid4(), session_id, "继续"))


def test_lease_and_compare_and_swap(context_db):
    db, owner, session_id = context_db
    first = ContextCompactionStore(db.get_bind(), "test-scope", owner, session_id)
    first.revision = 0
    asyncio.run(first.emit({"type": "compaction/start", "compaction_id": "first"}))
    second = ContextCompactionStore(db.get_bind(), "test-scope", owner, session_id)
    second.revision = 0
    with pytest.raises(CompactionBusy):
        asyncio.run(second.emit({"type": "compaction/start", "compaction_id": "second"}))
    asyncio.run(first.install([], {"id": "first"}))
    with pytest.raises(CompactionBusy):
        asyncio.run(first.install([], {"id": "stale"}))


def test_orphan_lease_recovery_records_unknown(context_db):
    db, owner, session_id = context_db
    first = ContextCompactionStore(db.get_bind(), "orphan", owner, session_id)
    asyncio.run(first.emit({"type": "compaction/start", "compaction_id": "old"}))
    with first.factory.begin() as writer:
        writer.get(models.ContextCheckpointState, "orphan").lease_until = datetime(2020, 1, 1)
    second = ContextCompactionStore(db.get_bind(), "orphan", owner, session_id)
    asyncio.run(second.emit({"type": "compaction/start", "compaction_id": "new"}))
    events = list(db.scalars(select(models.ContextCompactionEvent)))
    assert events[1].payload["status"] == "outcome_unknown"
    with pytest.raises(CompactionBusy):
        asyncio.run(first.install([], {"id": "old"}))


def test_source_change_rejects_install(context_db):
    db, owner, session_id = context_db
    store = ContextCompactionStore(db.get_bind(), "source-check", owner, session_id)
    source = session_surface(db, owner, session_id)[0]
    asyncio.run(store.emit({"type": "compaction/start", "compaction_id": "source"}))
    with store.factory.begin() as writer:
        writer.get(models.ChatMessage, uuid.UUID(source["id"])).content = "改口"
    with pytest.raises(CompactionBusy):
        asyncio.run(store.install([source["id"]], {"id": "source"}, source_rows=[source]))
