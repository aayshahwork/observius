"""Unit tests for workers/retry.py — exponential backoff retry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.retry import retry_with_backoff


async def test_succeeds_first_try():
    fn = AsyncMock(return_value="ok")
    result = await retry_with_backoff(fn, max_retries=3)
    assert result == "ok"
    assert fn.call_count == 1


async def test_retries_on_transient_error():
    fn = AsyncMock(side_effect=[RuntimeError("err"), RuntimeError("err"), "ok"])
    with patch("workers.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await retry_with_backoff(fn, max_retries=3)
    assert result == "ok"
    assert fn.call_count == 3


async def test_exhausts_retries():
    fn = AsyncMock(side_effect=RuntimeError("permanent"))
    with patch("workers.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="permanent"):
            await retry_with_backoff(fn, max_retries=2)
    assert fn.call_count == 3  # initial + 2 retries


async def test_backoff_delay_increases():
    fn = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("b"), "ok"])
    sleep_mock = AsyncMock()
    with patch("workers.retry.asyncio.sleep", sleep_mock), \
         patch("workers.retry.random.random", return_value=0.5):
        await retry_with_backoff(fn, max_retries=3, base_delay=1.0)

    # With jitter factor 0.5 + 0.5 = 1.0:
    # attempt 0 fail -> delay = min(1.0 * 2^0, 30) * 1.0 = 1.0
    # attempt 1 fail -> delay = min(1.0 * 2^1, 30) * 1.0 = 2.0
    assert sleep_mock.call_count == 2
    delays = [call.args[0] for call in sleep_mock.call_args_list]
    assert delays[0] == pytest.approx(1.0)
    assert delays[1] == pytest.approx(2.0)


async def test_respects_max_delay():
    fn = AsyncMock(side_effect=[RuntimeError("x")] * 5 + ["ok"])
    sleep_mock = AsyncMock()
    with patch("workers.retry.asyncio.sleep", sleep_mock), \
         patch("workers.retry.random.random", return_value=0.5):
        await retry_with_backoff(fn, max_retries=5, base_delay=10.0, max_delay=15.0)

    delays = [call.args[0] for call in sleep_mock.call_args_list]
    # All delays should be capped at max_delay * jitter_factor(1.0)
    for d in delays:
        assert d <= 15.0 * 1.5 + 0.01  # max_delay * max jitter


async def test_non_retriable_exception_raises_immediately():
    fn = AsyncMock(side_effect=ValueError("bad"))
    with pytest.raises(ValueError, match="bad"):
        await retry_with_backoff(
            fn,
            max_retries=5,
            retriable_exceptions=(RuntimeError,),
        )
    assert fn.call_count == 1
