"""Tests for user feedback loop — model, API, and coach agent integration."""

import uuid
from unittest.mock import MagicMock, patch

import pytest


class TestFeedbackModel:
    def test_feedback_model_fields(self):
        from fast_api.app.db.models import UserFeedback
        from sqlalchemy import inspect

        columns = {c.name: c for c in inspect(UserFeedback).columns}
        assert "id" in columns
        assert "user_id" in columns
        assert "session_id" in columns
        assert "message_id" in columns
        assert "rating" in columns
        assert "category" in columns
        assert "comment" in columns
        assert "coach_reply_snapshot" in columns
        assert "user_message_snapshot" in columns
        assert "metadata_json" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_feedback_unique_constraint(self):
        from fast_api.app.db.models import UserFeedback
        from sqlalchemy import inspect

        constraints = inspect(UserFeedback).table_constraints
        constraint_names = [c.name for c in constraints if hasattr(c, 'name')]
        assert "uq_user_feedback_message" in constraint_names

    def test_feedback_foreign_keys(self):
        from fast_api.app.db.models import UserFeedback
        from sqlalchemy import inspect

        fks = {c.name: c for c in inspect(UserFeedback).foreign_keys}
        assert "user_id" in str(fks)
        assert "session_id" in str(fks) or "session_id" in fks
        assert "message_id" in str(fks) or "message_id" in fks


class TestFeedbackSchemas:
    def test_submit_request_validation(self):
        from fast_api.app.schemas.agent import FeedbackSubmitRequest

        req = FeedbackSubmitRequest(message_id=uuid.uuid4(), rating=4)
        assert req.rating == 4
        assert req.category is None
        assert req.comment is None

    def test_submit_request_with_all_fields(self):
        from fast_api.app.schemas.agent import FeedbackSubmitRequest

        req = FeedbackSubmitRequest(
            message_id=uuid.uuid4(),
            rating=5,
            category="helpful",
            comment="Great advice!",
        )
        assert req.rating == 5
        assert req.category == "helpful"
        assert req.comment == "Great advice!"

    def test_submit_request_rating_bounds(self):
        from fast_api.app.schemas.agent import FeedbackSubmitRequest
        from pydantic import ValidationError

        # Valid range
        for r in [1, 3, 5]:
            req = FeedbackSubmitRequest(message_id=uuid.uuid4(), rating=r)
            assert req.rating == r

        # Invalid
        with pytest.raises(ValidationError):
            FeedbackSubmitRequest(message_id=uuid.uuid4(), rating=0)
        with pytest.raises(ValidationError):
            FeedbackSubmitRequest(message_id=uuid.uuid4(), rating=6)

    def test_response_schema(self):
        from fast_api.app.schemas.agent import FeedbackResponse

        fid = uuid.uuid4()
        uid = uuid.uuid4()
        mid = uuid.uuid4()
        from datetime import datetime

        resp = FeedbackResponse(
            id=fid,
            user_id=uid,
            session_id=None,
            message_id=mid,
            rating=4,
            category=None,
            comment=None,
            created_at=datetime.utcnow(),
        )
        assert resp.rating == 4
        assert resp.id == fid

    def test_stats_response_schema(self):
        from fast_api.app.schemas.agent import FeedbackStatsResponse

        stats = FeedbackStatsResponse(
            total_feedback=10,
            average_rating=4.2,
            rating_distribution={1: 1, 2: 0, 3: 2, 4: 3, 5: 4},
            top_categories=[],
            recent_feedback=[],
        )
        assert stats.total_feedback == 10
        assert stats.average_rating == 4.2


class TestFeedbackMigration:
    def test_migration_revision_id(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "migration_002",
            "alembic/versions/002_feedback.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "002_feedback"
        assert mod.down_revision == "001_initial_schema"

    def test_migration_has_upgrade_downgrade(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "migration_002",
            "alembic/versions/002_feedback.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestCoachAgentFeedbackIntegration:
    def test_save_message_returns_chat_message(self):
        """_save_message should now return the ChatMessage object."""
        from fast_api.app.db import models
        from fast_api.app.services.coach_agent import CoachAgentService
        from unittest.mock import MagicMock

        svc = CoachAgentService.__new__(CoachAgentService)
        svc.db = MagicMock()

        mock_msg = MagicMock(spec=models.ChatMessage)
        mock_msg.id = uuid.uuid4()

        def fake_add(msg):
            svc.db._added = msg
        svc.db.add = fake_add
        svc.db.flush = MagicMock()
        svc.db._added = None

        result = svc._save_message(
            uuid.uuid4(), uuid.uuid4(), "assistant", "Hello"
        )
        # Should have called db.add with a ChatMessage
        assert svc.db.add.called
        assert svc.db.flush.called
        # Should return the message object
        assert result is not None
        assert isinstance(result, models.ChatMessage)

    def test_handle_chat_message_includes_feedback_id(self):
        """The return dict should include feedback_message_id."""
        import inspect
        source = inspect.getsource(CoachAgentService.handle_chat_message)
        assert "feedback_message_id" in source


class TestFeedbackAPIRoutes:
    def test_router_is_registered(self):
        """Feedback router should be importable."""
        from fast_api.app.api.feedback_api import feedback_router
        assert feedback_router is not None
        assert feedback_router.prefix == "/v1/feedback"

    def test_main_includes_feedback_router(self):
        """main.py should import and include the feedback router."""
        with open("fast_api/app/main.py") as f:
            content = f.read()
        assert "from fast_api.app.api.feedback_api import feedback_router" in content
        assert "app.include_router(feedback_router)" in content
