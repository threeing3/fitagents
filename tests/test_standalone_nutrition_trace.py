"""Standalone image tracing with file transactions and no paid model calls."""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fast_api.app.api.nutrition_api import get_nutrition_service, nutrition_router
from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import Base, get_db
from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.execution_trace import recorded_execution_trace
from fast_api.app.services.model_call_records import record_auxiliary_call
from fast_api.app.services.standalone_operation_trace import StandaloneOperationTrace
from fast_api.app.services.stream_event_pages import stream_event_page


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'nutrition.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = models.User(email="image@example.test", password_hash="synthetic")
        other = models.User(email="other-image@example.test", password_hash="synthetic")
        db.add_all([owner, other])
        db.commit()
        yield db, owner, other
    engine.dispose()


@pytest.mark.parametrize(
    "result_status,trace_status", [("ok", "completed"), ("offline", "unconfirmed")]
)
def test_real_api_returns_owned_run_without_saving_meal(state, result_status, trace_status):
    from PIL import Image

    db, owner, other = state

    class FakeService:
        async def analyze_food_photo(self, user_id, image_bytes, media_type):
            assert user_id == owner.id and image_bytes and media_type == "image/png"
            record_auxiliary_call("vision.local_test", "completed", {"media_omitted": True})
            return {"status": result_status, "food_items": []}

    app = FastAPI()
    app.include_router(nutrition_router)
    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_nutrition_service] = lambda: FakeService()
    image = BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    with TestClient(app) as client:
        response = client.post(
            "/v1/nutrition/recognize", files={"image": ("test.png", image.getvalue(), "image/png")}
        )
    assert response.status_code == 200, response.text
    from uuid import UUID

    identity = UUID(response.json()["agent_run_id"])
    page = stream_event_page(db, identity, owner.id)
    assert page["status"] == trace_status
    history = recorded_execution_trace(db, identity, owner.id)
    assert history is not None
    assert page["events"][-1]["event"]["business_write"] is False
    assert any(item["event"].get("name") == "vision.local_test" for item in page["events"])
    with pytest.raises(LookupError):
        stream_event_page(db, identity, other.id)
    assert db.scalar(select(func.count()).select_from(models.NutritionLog)) == 0
    assert db.scalar(select(func.count()).select_from(models.NutritionDailySummary)) == 0


def test_diagnostic_commit_does_not_commit_pending_business_data(state):
    db, owner, _ = state
    owner_id = owner.id
    pending = models.ConversationSession(user_id=owner_id, title="not committed")
    db.add(pending)
    trace = StandaloneOperationTrace(db, owner_id, "local.test")
    with trace.recording():
        trace.finish("completed")
    with Session(db.get_bind()) as reader:
        assert reader.scalar(select(func.count()).select_from(models.ConversationSession)) == 0
        assert reader.get(models.AgentRun, trace.id).status == "completed"
    assert pending in db.new
    db.rollback()


@pytest.mark.parametrize("error", [RuntimeError("sensitive text"), asyncio.CancelledError()])
def test_error_and_cancel_preserve_prefix_without_retry(state, error):
    db, owner, _ = state
    trace = StandaloneOperationTrace(db, owner.id, "local.test")
    with pytest.raises(type(error)):
        with trace.recording():
            record_auxiliary_call("vision.started", "running", {})
            raise error
    page = stream_event_page(db, trace.id, owner.id)
    assert page["status"] == ("failed" if isinstance(error, Exception) else "interrupted")
    assert "sensitive text" not in str(page)
    assert page["may_repeat_writes"] is False


def test_missing_terminal_retains_owned_prefix_as_unconfirmed(state):
    db, owner, _ = state
    trace = StandaloneOperationTrace(db, owner.id, "local.test")
    trace.journal.close()  # Represents stored prefix without claiming a process kill.
    page = stream_event_page(db, trace.id, owner.id)
    assert page["status"] == "unconfirmed"
    assert page["events"][0]["event"]["type"] == "operation.start"
    assert page["liveness"] == "not_checked"


def test_shared_memory_database_rejected_before_any_operation():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as db, pytest.raises(RuntimeError, match="Independent trace"):
        StandaloneOperationTrace(db, None, "local.test")
    engine.dispose()


def test_terminal_storage_error_does_not_replace_original_cancel(state, monkeypatch):
    db, owner, _ = state
    trace = StandaloneOperationTrace(db, owner.id, "local.test")

    def unavailable(*args, **kwargs):
        raise OSError("sensitive disk path")

    monkeypatch.setattr(trace, "finish", unavailable)
    with pytest.raises(asyncio.CancelledError):
        with trace.recording():
            raise asyncio.CancelledError()
    page = stream_event_page(db, trace.id, owner.id)
    assert page["status"] == "unconfirmed"
    assert "sensitive disk path" not in str(page)
