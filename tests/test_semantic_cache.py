"""Tests for semantic cache service."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestSemanticCacheModel:
    def test_model_fields(self):
        from fast_api.app.db.models import SemanticCache
        from sqlalchemy import inspect

        columns = {c.name for c in inspect(SemanticCache).columns}
        expected = {
            "id", "prompt_hash", "system_prompt_hash", "embedding",
            "response", "model_name", "hit_count", "last_hit_at",
            "ttl_seconds", "created_at", "updated_at",
        }
        assert columns >= expected

    def test_model_tablename(self):
        from fast_api.app.db.models import SemanticCache
        assert SemanticCache.__tablename__ == "semantic_cache"


class TestSemanticCacheMigration:
    def test_migration_revision(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "m003", "alembic/versions/003_semantic_cache.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "003_semantic_cache"
        assert mod.down_revision == "002_feedback"

    def test_migration_has_upgrade_downgrade(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "m003", "alembic/versions/003_semantic_cache.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestSemanticCacheService:
    def test_init(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)
        assert svc.similarity_threshold == 0.95
        assert svc.ttl_seconds == 86400

    def test_hash_deterministic(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)

        h1 = svc._hash("hello")
        h2 = svc._hash("hello")
        h3 = svc._hash("world")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # SHA-256 hex digest

    def test_combined_text(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)

        result = svc._combined_text("system prompt", "user prompt")
        assert "system prompt" in result
        assert "user prompt" in result
        assert "---" in result

    def test_get_short_prompt_returns_none(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)

        result = svc.get("sys", "hi")  # too short
        assert result is None

    def test_set_short_prompt_skipped(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)

        svc.set("sys", "hi", "response text")
        # db.add should not have been called
        mock_db.add.assert_not_called()

    def test_set_short_response_skipped(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)

        svc.set("system prompt here", "user prompt here long enough", "ok")
        mock_db.add.assert_not_called()

    def test_set_stores_entry(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        from fast_api.app.db.models import SemanticCache

        mock_db = MagicMock()
        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = [0.1] * 768

        svc = SemanticCacheService(mock_db, mock_provider)
        svc.set(
            "You are a fitness coach",
            "What should I eat today?",
            "Eat protein and vegetables.",
            model_name="gpt-4",
        )
        assert mock_db.add.called
        # Check the entry was created
        call_args = mock_db.add.call_args[0][0]
        assert isinstance(call_args, SemanticCache)
        assert call_args.response == "Eat protein and vegetables."
        assert call_args.model_name == "gpt-4"
        assert mock_db.commit.called

    def test_get_calls_embed_text(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        from fast_api.app.db.models import SemanticCache

        mock_db = MagicMock()
        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = [0.1] * 768

        # Setup mock db query to return None (cache miss)
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        svc = SemanticCacheService(mock_db, mock_provider)
        result = svc.get(
            "You are a fitness coach",
            "What should I eat today for lunch?",
        )
        assert result is None
        mock_provider.embed_text.assert_called()

    def test_get_with_cache_hit(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService
        from fast_api.app.db.models import SemanticCache

        mock_db = MagicMock()
        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = [0.1] * 768

        cache_entry = SemanticCache(
            id=uuid.uuid4(),
            prompt_hash="abc",
            system_prompt_hash="def",
            embedding=[0.1] * 768,
            response="Cached healthy meal advice",
            model_name="gpt-4",
            hit_count=1,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.first.return_value = cache_entry
        mock_db.query.return_value = mock_query

        svc = SemanticCacheService(mock_db, mock_provider)
        result = svc.get(
            "You are a fitness coach",
            "What should I eat today for lunch?",
        )
        assert result == "Cached healthy meal advice"
        # hit_count should be incremented
        assert cache_entry.hit_count == 2

    def test_stats(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_db.query.return_value = mock_query

        # Mock counts
        mock_query.count.side_effect = [10, 8]  # total, valid
        mock_query.scalar.return_value = 42  # total hits

        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider)
        stats = svc.stats()

        assert stats["total_entries"] == 10
        assert stats["valid_entries"] == 8
        assert stats["total_hits"] == 42

    def test_ttl_parameter(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService

        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider, ttl_seconds=3600)
        assert svc.ttl_seconds == 3600

    def test_similarity_threshold_parameter(self):
        from fast_api.app.services.semantic_cache import SemanticCacheService

        mock_db = MagicMock()
        mock_provider = MagicMock()
        svc = SemanticCacheService(mock_db, mock_provider, similarity_threshold=0.90)
        assert svc.similarity_threshold == 0.90


class TestCoachAgentCacheIntegration:
    def test_cache_service_initialized(self):
        """CoachAgentService should initialize SemanticCacheService in __init__."""
        with open("fast_api/app/services/coach_agent.py") as f:
            content = f.read()
        assert "self.cache = SemanticCacheService(db, self.model_provider)" in content

    def test_coaching_reply_checks_cache(self):
        """_coaching_reply should check cache before LLM call."""
        with open("fast_api/app/services/coach_agent.py") as f:
            content = f.read()
        assert "# Check semantic cache first" in content
        assert "self.cache.get(system_prompt, user_prompt)" in content

    def test_coaching_reply_caches_result(self):
        """_coaching_reply should cache successful LLM replies."""
        with open("fast_api/app/services/coach_agent.py") as f:
            content = f.read()
        assert "self.cache.set(system_prompt, user_prompt, live_reply" in content

    def test_stream_caches_result(self):
        """_coaching_reply_stream should cache after streaming completes."""
        with open("fast_api/app/services/coach_agent.py") as f:
            content = f.read()
        # Should collect chunks into a list and cache the full reply
        assert "chunks: list[str] = []" in content
