"""Optional Redis integration used for distributed rate limiting and caching."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from fast_api.app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_redis_client():
    """Return a Redis client when REDIS_URL is configured and reachable."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        from redis import Redis

        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
        client.ping()
        return client
    except Exception as exc:  # pragma: no cover - depends on local Redis availability
        logger.warning("Redis unavailable; falling back to local-only behavior: %s", exc)
        return None


def get_json_cache(key: str) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
    client = get_redis_client()
    if client is None:
        return None
    value = client.get(key)
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def set_json_cache(key: str, value: Any, ttl_seconds: int) -> bool:
    client = get_redis_client()
    if client is None:
        return False
    client.setex(key, ttl_seconds, json.dumps(value, default=str, ensure_ascii=False))
    return True
