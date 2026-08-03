"""Rate limiter configuration shared by the FastAPI app and routers."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from fast_api.app.core.config import get_settings

settings = get_settings()

try:
    import redis  # noqa: F401
except ImportError:
    storage_uri = None
else:
    storage_uri = settings.redis_url

limiter_kwargs = {
    "key_func": get_remote_address,
    "default_limits": [settings.rate_limit_default],
    "in_memory_fallback_enabled": True,
}
if storage_uri:
    limiter_kwargs["storage_uri"] = storage_uri

limiter = Limiter(**limiter_kwargs)
