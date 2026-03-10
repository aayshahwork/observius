"""Unit tests for computeruse.retry.RetryHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from computeruse.exceptions import (
    APIError,
    AuthenticationError,
    RetryExhaustedError,
    TimeoutError,
    ValidationError,
)
from computeruse.retry import RetryHandler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def handler() -> RetryHandler:
    """Default RetryHandler with 3 attempts and no real sleep delays."""
    return RetryHandler(
        max_attempts=3,
        base_delay=1.0,
        max_delay=10.0,
        backoff_factor=2.0,
    )


# ---------------------------------------------------------------------------
# execute_with_retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_execution_no_retry(handler: RetryHandler) -> None:
    """A function that succeeds on the first attempt is called exactly once."""
    func = AsyncMock(return_value="ok")

    result = await handler.execute_with_retry(func, "arg1", kwarg="val")

    assert result == "ok"
    func.assert_awaited_once_with("arg1", kwarg="val")


@pytest.mark.asyncio
async def test_retry_on_failure_succeeds_third_attempt(handler: RetryHandler) -> None:
    """A function that fails twice and succeeds on the third attempt returns the value."""
    transient = ConnectionError("network blip")
    func = AsyncMock(side_effect=[transient, transient, "recovered"])

    with patch("computeruse.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await handler.execute_with_retry(func)

    assert result == "recovered"
    assert func.await_count == 3
    # Sleep is called after each failure except the last attempt.
    assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_max_retries_exhausted_raises(handler: RetryHandler) -> None:
    """When all attempts fail the last exception is wrapped in RetryExhaustedError."""
    err = ConnectionError("always broken")
    func = AsyncMock(side_effect=err)

    with patch("computeruse.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RetryExhaustedError) as exc_info:
            await handler.execute_with_retry(func)

    assert func.await_count == 3
    assert exc_info.value.last_error is err
    assert "3" in exc_info.value.message  # mentions attempt count


@pytest.mark.asyncio
async def test_non_retryable_error_raised_immediately(handler: RetryHandler) -> None:
    """A non-retryable error (AuthenticationError) is re-raised after the first attempt."""
    func = AsyncMock(side_effect=AuthenticationError("bad creds"))

    with patch("computeruse.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(AuthenticationError):
            await handler.execute_with_retry(func)

    # Only one attempt; no sleep.
    func.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_validation_error_is_not_retried(handler: RetryHandler) -> None:
    """ValidationError must NOT trigger a retry."""
    func = AsyncMock(side_effect=ValidationError("schema mismatch"))

    with patch("computeruse.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(ValidationError):
            await handler.execute_with_retry(func)

    func.assert_awaited_once()
    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exponential_backoff_delays_increase(handler: RetryHandler) -> None:
    """Sleep durations must follow the backoff formula and be capped at max_delay."""
    func = AsyncMock(side_effect=ConnectionError("boom"))

    with patch("computeruse.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RetryExhaustedError):
            await handler.execute_with_retry(func)

    # With base=1.0, factor=2.0, max=10.0:
    #   attempt 0 → delay = min(1.0 * 2^0, 10.0) = 1.0
    #   attempt 1 → delay = min(1.0 * 2^1, 10.0) = 2.0
    #   attempt 2 → no sleep (last attempt)
    sleep_calls = [c.args[0] for c in mock_sleep.await_args_list]
    assert sleep_calls == [1.0, 2.0]


@pytest.mark.asyncio
async def test_backoff_capped_at_max_delay() -> None:
    """Backoff delay is never larger than max_delay."""
    handler = RetryHandler(
        max_attempts=5,
        base_delay=10.0,
        max_delay=15.0,
        backoff_factor=4.0,
    )
    func = AsyncMock(side_effect=ConnectionError("nope"))

    with patch("computeruse.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RetryExhaustedError):
            await handler.execute_with_retry(func)

    for c in mock_sleep.await_args_list:
        assert c.args[0] <= 15.0


def test_backoff_delay_formula(handler: RetryHandler) -> None:
    """_backoff_delay returns the correct values without clamping."""
    assert handler._backoff_delay(0) == 1.0   # 1.0 * 2^0
    assert handler._backoff_delay(1) == 2.0   # 1.0 * 2^1
    assert handler._backoff_delay(2) == 4.0   # 1.0 * 2^2
    # capped
    assert handler._backoff_delay(100) == 10.0


# ---------------------------------------------------------------------------
# is_retryable_error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error,expected", [
    # Retryable SDK types
    (TimeoutError("timed out"),             True),
    (ConnectionError("refused"),            True),
    (OSError("I/O error"),                  True),
    (APIError("rate limit", 429),           True),
    (APIError("server error", 500),         True),
    (APIError("bad gateway", 502),          True),
    (APIError("unavailable", 503),          True),
    # Non-retryable SDK types
    (ValidationError("bad schema"),         False),
    (AuthenticationError("wrong password"), False),
    # Non-retryable API status codes
    (APIError("not found", 404),            False),
    (APIError("forbidden", 403),            False),
    # Keyword-matched retryable messages
    (Exception("connection reset by peer"), True),
    (Exception("timeout waiting for lock"), True),
    (Exception("rate_limit exceeded"),      True),
    (Exception("too many requests"),        True),
    (Exception("service unavailable"),      True),
    # Generic non-retryable
    (ValueError("bad value"),               False),
    (RuntimeError("crash"),                 False),
])
def test_is_retryable_error(
    handler: RetryHandler,
    error: Exception,
    expected: bool,
) -> None:
    assert handler.is_retryable_error(error) is expected


# ---------------------------------------------------------------------------
# execute_with_timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_with_timeout_succeeds(handler: RetryHandler) -> None:
    """A fast function completes before the timeout and returns its value."""
    func = AsyncMock(return_value=42)
    result = await handler.execute_with_timeout(func, timeout_seconds=5)
    assert result == 42


@pytest.mark.asyncio
async def test_execute_with_timeout_raises_sdk_error(handler: RetryHandler) -> None:
    """asyncio.TimeoutError is converted to the SDK's TimeoutError."""
    import asyncio as _asyncio

    async def slow():
        raise _asyncio.TimeoutError

    with pytest.raises(TimeoutError) as exc_info:
        await handler.execute_with_timeout(slow, timeout_seconds=1)

    assert "slow" in exc_info.value.message
    assert "1s" in exc_info.value.message


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

def test_constructor_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryHandler(max_attempts=0)


def test_constructor_rejects_negative_delay() -> None:
    with pytest.raises(ValueError, match="[Dd]elay"):
        RetryHandler(base_delay=-1.0)
