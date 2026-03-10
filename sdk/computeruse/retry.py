from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from rich.console import Console

from computeruse.exceptions import (
    APIError,
    AuthenticationError,
    RetryExhaustedError,
    TimeoutError,
    ValidationError,
)

console = Console()
logger = logging.getLogger(__name__)


class RetryHandler:
    """Executes async callables with exponential-backoff retry and timeout support.

    Typical usage::

        handler = RetryHandler(max_attempts=3, base_delay=2.0)
        result = await handler.execute_with_retry(my_async_fn, arg1, kwarg=val)

    The backoff delay for attempt *n* (0-indexed) is::

        delay = min(base_delay * (backoff_factor ** n), max_delay)

    So with defaults ``base_delay=2.0``, ``backoff_factor=2.0``, ``max_delay=30.0``
    the waits are 2 s → 4 s → 8 s → … capped at 30 s.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
    ) -> None:
        """
        Args:
            max_attempts:   Total number of attempts (including the first try).
            base_delay:     Initial wait in seconds before the second attempt.
            max_delay:      Upper bound on the inter-attempt wait in seconds.
            backoff_factor: Multiplier applied to the delay after each failure.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be ≥ 1")
        if base_delay < 0 or max_delay < 0:
            raise ValueError("Delay values must be non-negative")

        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_with_retry(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Execute *func* with automatic retry on retryable errors.

        Calls ``func(*args, **kwargs)`` up to ``max_attempts`` times.  After
        each failure the handler sleeps for an exponentially growing delay
        before the next attempt.  Non-retryable errors (e.g.
        ``AuthenticationError``, ``ValidationError``) are re-raised immediately
        without consuming further attempts.

        Args:
            func:    Async callable to execute.
            *args:   Positional arguments forwarded to *func*.
            **kwargs: Keyword arguments forwarded to *func*.

        Returns:
            The return value of *func* on success.

        Raises:
            RetryExhaustedError: When all attempts fail with retryable errors,
                wrapping the last exception as ``last_error``.
            Exception: Any non-retryable exception is re-raised immediately.
        """
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            try:
                console.log(
                    f"[bold cyan]Attempt {attempt + 1}/{self.max_attempts}[/] "
                    f"→ [dim]{func.__name__}[/]"
                )
                result = await func(*args, **kwargs)
                if attempt > 0:
                    console.log(
                        f"[bold green]Succeeded[/] on attempt {attempt + 1} "
                        f"after {attempt} failure(s)"
                    )
                return result

            except Exception as exc:
                last_error = exc

                if not self.is_retryable_error(exc):
                    logger.debug("Non-retryable error — propagating immediately: %s", exc)
                    raise

                delay = self._backoff_delay(attempt)
                console.log(
                    f"[yellow]Attempt {attempt + 1} failed:[/] {exc}. "
                    + (
                        f"Retrying in {delay:.1f}s…"
                        if attempt + 1 < self.max_attempts
                        else "No more attempts."
                    )
                )
                logger.warning(
                    "Retryable error on attempt %d/%d: %s",
                    attempt + 1,
                    self.max_attempts,
                    exc,
                )

                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(delay)

        raise RetryExhaustedError(
            message=(
                f"All {self.max_attempts} attempt(s) failed for '{func.__name__}'"
            ),
            last_error=last_error,
        )

    def is_retryable_error(self, error: Exception) -> bool:
        """Return ``True`` if *error* should trigger a retry attempt.

        Retryable conditions:
        - ``TimeoutError`` (SDK or built-in)
        - ``APIError`` with HTTP status 429, 500, 502, or 503
        - ``ConnectionError``, ``OSError``, ``asyncio.TimeoutError``
        - Any error whose message contains network-failure keywords

        Non-retryable conditions:
        - ``ValidationError`` — bad output schema, not a transient failure
        - ``AuthenticationError`` — wrong credentials won't change on retry

        Args:
            error: The exception to classify.

        Returns:
            ``True`` if the error is considered transient, ``False`` otherwise.
        """
        # Explicit non-retryable SDK types
        if isinstance(error, (ValidationError, AuthenticationError)):
            return False

        # SDK timeout is always retryable
        if isinstance(error, TimeoutError):
            return True

        # asyncio internal timeout
        if isinstance(error, asyncio.TimeoutError):
            return True

        # API errors — only retry on rate-limit and server-side faults
        if isinstance(error, APIError):
            return error.status_code in {429, 500, 502, 503}

        # Network / OS level errors
        if isinstance(error, (ConnectionError, OSError)):
            return True

        # Keyword scan as a last resort for third-party exceptions
        message = str(error).lower()
        retryable_keywords = {
            "timeout",
            "timed out",
            "connection",
            "network",
            "rate limit",
            "rate_limit",
            "too many requests",
            "service unavailable",
            "bad gateway",
            "internal server error",
        }
        return any(kw in message for kw in retryable_keywords)

    async def execute_with_timeout(
        self,
        func: Callable[..., Any],
        timeout_seconds: int,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute *func* and raise ``TimeoutError`` if it exceeds *timeout_seconds*.

        A thin wrapper around :func:`asyncio.wait_for` that converts
        ``asyncio.TimeoutError`` into the SDK's own ``TimeoutError`` so callers
        only need to handle one exception hierarchy.

        Args:
            func:            Async callable to execute.
            timeout_seconds: Maximum allowed wall-clock time in seconds.
            *args:           Positional arguments forwarded to *func*.
            **kwargs:        Keyword arguments forwarded to *func*.

        Returns:
            The return value of *func* if it completes within the timeout.

        Raises:
            TimeoutError: If *func* does not complete within *timeout_seconds*.
        """
        try:
            return await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=float(timeout_seconds),
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"'{func.__name__}' exceeded the {timeout_seconds}s timeout"
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _backoff_delay(self, attempt: int) -> float:
        """Compute the capped exponential delay for a given attempt index.

        Args:
            attempt: Zero-based attempt index (0 = first failure).

        Returns:
            Seconds to wait before the next attempt.
        """
        return min(self.base_delay * (self.backoff_factor ** attempt), self.max_delay)
