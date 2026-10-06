"""Background diagnostic boundaries with real local transactions."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services import background_tasks as tasks
from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.model_call_records import record_auxiliary_call
from fast_api.app.services.stream_event_pages import stream_event_page


@pytest.mark.parametrize("mode", ["completed", "failed", "cancelled"])
def test_background_prefix_terminal_and_no_write_retry(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'jobs.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    calls = []

    def handler(db, task):
        calls.append(task.id)
        # Metadata is independently visible before handler execution.
        with Session(engine) as reader:
            identity = uuid.UUID(task.payload_json["execution_trace_run_id"])
            assert reader.get(models.AgentRun, identity).status == "running"
        record_auxiliary_call("background.fake_model", "running", {})
        if mode == "cancelled":
            raise asyncio.CancelledError()
        if mode == "failed":
            raise ValueError("local handler failure")
        return {"status": "ok"}

    monkeypatch.setattr(tasks, "_execute_task", handler)
    with Session(engine) as db:
        owner = models.User(email="background@example.test", password_hash="synthetic")
        db.add(owner)
        db.commit()
        owner_id = owner.id
        task = tasks.BackgroundTaskQueue(db).enqueue(owner_id, "plan.generate", {})
        if mode == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                tasks.run_one_background_task(db, user_id=owner_id)
        else:
            tasks.run_one_background_task(db, user_id=owner_id)
        db.refresh(task)
        identity = uuid.UUID(task.payload_json["execution_trace_run_id"])
        page = stream_event_page(db, identity, owner_id)
        assert (
            page["status"]
            == {"completed": "completed", "failed": "outcome_unknown", "cancelled": "interrupted"}[
                mode
            ]
        )
        assert page["events"][0]["event"]["type"] == "background.start"
        assert any(item["event"].get("name") == "background.fake_model" for item in page["events"])
        assert page["may_repeat_writes"] is False
        with pytest.raises(LookupError):
            stream_event_page(db, identity, uuid.uuid4())
        assert tasks.run_one_background_task(db, user_id=owner_id) is None
        assert len(calls) == 1
        if mode == "cancelled":
            assert task.status == "running"  # Cancellation is not a reconciled job outcome.
    engine.dispose()
