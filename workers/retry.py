"""
workers/retry.py — Exponential backoff retry for async callables.

Used by the native executor to wrap Anthropic API calls with transient
error recovery (429 rate limits, 529 overloaded, network errors).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
from typing import Any, Callable, Tuple, Type

logger = logging.getLogger(__name__)

# Default exceptions considered transient for the Anthropic API.
_DEFAULT_RETRIABLE: Tuple[Type[BaseException], ...] = (Exception,)


async def retry_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retriable_exceptions: Tuple[Type[BaseException], ...] = _DEFAULT_RETRIABLE,
    **kwargs: Any,
) -> Any:
    """Call *fn* with exponential backoff on transient errors.

    Parameters
    ----------
    fn:
        Sync or async callable to invoke.
    max_retries:
        Maximum number of retry attempts (total calls = max_retries + 1).
    base_delay:
        Initial delay in seconds before the first retry.
    max_delay:
        Ceiling for the backoff delay.
    retriable_exceptions:
        Tuple of exception types that trigger a retry.  Exceptions not
        in this tuple are re-raised immediately.

    Returns the result of *fn* on success.  Re-raises the last exception
    after all retries are exhausted.
    """
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            result = fn(*args, **kwargs)
            # Use isawaitable instead of iscoroutinefunction: the
            # Anthropic SDK's beta accessor chain (__getattr__) wraps
            # async methods in a way that iscoroutinefunction misses,
            # leaving coroutines silently unawaited.
            if inspect.isawaitable(result):
                result = await result
            return result
        except retriable_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            # Jitter: multiply by random factor in [0.5, 1.5)
            delay *= 0.5 + random.random()
            logger.warning(
                "retry_with_backoff attempt=%d/%d error=%s delay=%.1fs",
                attempt + 1,
                max_retries + 1,
                exc,
                delay,
            )
            try:
                from workers.metrics import celery_native_llm_retry_total

                celery_native_llm_retry_total.labels(
                    task_name="computeruse.execute_task",
                ).inc()
            except Exception:
                pass
            await asyncio.sleep(delay)
        except BaseException:
            # Non-retriable exception — propagate immediately
            raise

    raise last_exc  # type: ignore[misc]
