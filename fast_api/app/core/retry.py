"""Exponential backoff retry for LLM and external API calls.

Provides a decorator and a direct helper for retrying async operations
with configurable exponential backoff, jitter, and retryable exception detection.
"""

import asyncio
import functools
import logging
import random
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    asyncio.TimeoutError,
)

RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    """Check whether an exception indicates a transient failure worth retrying."""
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    # httpx.HTTPStatusError / requests.HTTPError with retryable codes
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return getattr(exc.response, "status_code") in RETRYABLE_HTTP_STATUSES
    msg = str(exc).lower()
    return any(
        phrase in msg
        for phrase in [
            "timeout",
            "connection reset",
            "connection refused",
            "too many requests",
            "service unavailable",
            "internal server error",
            "rate limit",
            "overloaded",
            "temporarily unavailable",
            "server disconnected",
        ]
    )


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
):
    """Decorator for async and sync functions with exponential backoff.

    Args:
        max_retries: Maximum retry attempts (default 3, so 4 total including initial).
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier for successive delays.
        jitter: Add random jitter (±50%) to avoid thundering-herd.
        retryable_exceptions: Specific types to retry; defaults to auto-detection.

    Example::

        @retry_with_backoff(max_retries=3, base_delay=1.0)
        async def call_llm(prompt: str) -> str: ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    check = retryable_exceptions if retryable_exceptions is not None else None
                    should_retry = (
                        isinstance(exc, check) if check else _is_retryable(exc)
                    )
                    if not should_retry:
                        break
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random()
                    logger.warning(
                        "Retry %d/%d after %.1fs for %s: %s",
                        attempt + 1, max_retries, delay, func.__name__, exc,
                    )
                    await asyncio.sleep(delay)
            logger.error("%s failed after %d attempts: %s", func.__name__, max_retries + 1, last_exc)
            raise last_exc  # type: ignore[misc]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    check = retryable_exceptions if retryable_exceptions is not None else None
                    should_retry = (
                        isinstance(exc, check) if check else _is_retryable(exc)
                    )
                    if not should_retry:
                        break
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random()
                    logger.warning(
                        "Retry %d/%d after %.1fs for %s: %s",
                        attempt + 1, max_retries, delay, func.__name__, exc,
                    )
                    time.sleep(delay)
            logger.error("%s failed after %d attempts: %s", func.__name__, max_retries + 1, last_exc)
            raise last_exc  # type: ignore[misc]

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


async def async_retry_call(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    **kwargs: Any,
) -> Any:
    """Call an async function with exponential backoff retry (non-decorator form).

    Useful for one-off calls or when you need dynamic retry configuration.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            if not _is_retryable(exc):
                break
            delay = min(base_delay * (backoff_factor ** attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random()
            logger.warning(
                "Retry %d/%d after %.1fs: %s",
                attempt + 1, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
