from unittest.mock import MagicMock

from fast_api.app.core.config import Settings
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.semantic_cache import SemanticCacheService


def test_offline_provider_reports_vector_unavailable_without_hash_embedding():
    provider = ModelProvider(Settings(EMBEDDING_PROVIDER="offline"))
    assert provider.embedding_mode() == "vector_unavailable_bm25_fallback"
    assert provider.embed_text("two semantically related sentences") is None


def test_semantic_cache_is_disabled_when_real_embeddings_are_unavailable():
    db = MagicMock()
    provider = MagicMock()
    provider.embed_text.return_value = None
    cache = SemanticCacheService(db, provider)

    assert cache.get("system prompt long enough", "user prompt long enough") is None
    cache.set("system prompt long enough", "user prompt long enough", "response long enough")

    assert db.query.called is False
    assert db.add.called is False
