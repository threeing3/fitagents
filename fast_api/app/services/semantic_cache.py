"""Semantic cache for LLM responses using pgvector cosine similarity.

Caches LLM replies keyed by the embedding of (system_prompt + user_prompt).
When a semantically similar prompt is seen within the similarity threshold,
the cached response is returned instead of calling the LLM.
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from fast_api.app.core.metrics import cache_hits_total, cache_misses_total, cache_size
from fast_api.app.db.models import SemanticCache
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)

# Cosine similarity threshold: 0.95 = very strict, only near-identical prompts match
DEFAULT_SIMILARITY_THRESHOLD = 0.95
# Max age before a cache entry is considered stale (default 24h)
DEFAULT_TTL_SECONDS = 86400
# Minimum prompt length to consider caching (skip very short messages)
MIN_PROMPT_LENGTH = 20


class SemanticCacheService:
    """Caches LLM responses using semantic similarity via pgvector."""

    def __init__(
        self,
        db: Session,
        model_provider: ModelProvider,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.db = db
        self.model_provider = model_provider
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _combined_text(self, system_prompt: str, user_prompt: str) -> str:
        """Combine prompts for embedding — system prompt first (defines task)."""
        return f"{system_prompt}\n\n---\n\n{user_prompt}"

    def _compute_embedding(self, text: str) -> list[float]:
        return self.model_provider.embed_text(text)

    def get(self, system_prompt: str, user_prompt: str) -> str | None:
        """Look up a cached response for a semantically similar prompt.

        Returns the cached response, or None if no match found.
        """
        combined = self._combined_text(system_prompt, user_prompt)
        if len(combined) < MIN_PROMPT_LENGTH:
            return None

        embedding = self._compute_embedding(combined)
        sys_hash = self._hash(system_prompt)

        # Expire old entries
        cutoff = datetime.utcnow() - timedelta(seconds=self.ttl_seconds)

        # pgvector cosine similarity query
        # cosine_distance = 1 - cosine_similarity
        # threshold 0.95 similarity = max 0.05 distance
        max_distance = 1.0 - self.similarity_threshold

        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        try:
            result = (
                self.db.query(SemanticCache)
                .filter(
                    SemanticCache.system_prompt_hash == sys_hash,
                    SemanticCache.created_at >= cutoff,
                    func.cosine_distance(
                        SemanticCache.embedding,
                        text(f"'{embedding_str}'::vector"),
                    ) <= max_distance,
                )
                .order_by(
                    func.cosine_distance(
                        SemanticCache.embedding,
                        text(f"'{embedding_str}'::vector"),
                    )
                )
                .limit(1)
                .first()
            )
        except Exception as exc:
            logger.warning("Semantic cache lookup failed (may be missing pgvector): %s", exc)
            cache_misses_total.inc()
            return None

        if result is None:
            cache_misses_total.inc()
            return None

        # Update hit stats
        cache_hits_total.inc()
        result.hit_count = (result.hit_count or 0) + 1
        result.last_hit_at = datetime.utcnow()
        self.db.commit()
        return result.response

    def set(
        self,
        system_prompt: str,
        user_prompt: str,
        response: str,
        model_name: str = "unknown",
    ) -> None:
        """Store a response in the cache."""
        combined = self._combined_text(system_prompt, user_prompt)
        if len(combined) < MIN_PROMPT_LENGTH:
            return
        if len(response) < 10:
            return

        embedding = self._compute_embedding(combined)

        entry = SemanticCache(
            prompt_hash=self._hash(user_prompt),
            system_prompt_hash=self._hash(system_prompt),
            embedding=embedding,
            response=response,
            model_name=model_name,
            ttl_seconds=self.ttl_seconds,
        )
        self.db.add(entry)
        self.db.commit()

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self.db.query(SemanticCache).count()
        valid = (
            self.db.query(SemanticCache)
            .filter(SemanticCache.created_at >= datetime.utcnow() - timedelta(seconds=self.ttl_seconds))
            .count()
        )
        total_hits = self.db.query(func.sum(SemanticCache.hit_count)).scalar() or 0
        cache_size.set(total)
        return {
            "total_entries": total,
            "valid_entries": valid,
            "total_hits": int(total_hits),
            "ttl_seconds": self.ttl_seconds,
            "similarity_threshold": self.similarity_threshold,
        }
