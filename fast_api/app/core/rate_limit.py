"""Rate limiter configuration shared by the FastAPI app and routers."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from fast_api.app.core.config import get_settings

settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.redis_url,
    in_memory_fallback_enabled=True,
)
