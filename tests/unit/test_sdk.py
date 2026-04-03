"""Tests for the Python SDK (ComputerUse client, models, edge cases)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from computeruse.client import ComputerUse, _parse_cloud_result
from computeruse.exceptions import (
    ComputerUseSDKError,
    NetworkError,
    RetryExhaustedError,
)
from computeruse.models import TaskConfig, TaskResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(**overrides) -> TaskResult:
    """Create a TaskResult with sensible defaults, overridden by kwargs."""
    defaults = dict(
        task_id="test-id-123",
        status="completed",
        success=True,
        result={"title": "Hello"},
        error=None,
        replay_path="/tmp/replay.json",
        steps=5,
        duration_ms=1234,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


# ---------------------------------------------------------------------------
# TaskResult serialization
# ---------------------------------------------------------------------------


class TestTaskResultSerialization:
    def test_to_json_and_from_json_round_trip(self):
        original = _make_result()
        json_str = original.to_json(indent=2)
        restored = TaskResult.from_json(json_str)

        assert restored.task_id == original.task_id
        assert restored.status == original.status
        assert restored.success == original.success
        assert restored.result == original.result
        assert restored.steps == original.steps
        assert restored.duration_ms == original.duration_ms
        assert restored.created_at == original.created_at
        assert restored.completed_at == original.completed_at

    def test_to_dict_converts_datetimes_to_iso(self):
        result = _make_result()
        d = result.to_dict()
        assert isinstance(d["created_at"], str)
        assert "2026" in d["created_at"]

    def test_from_dict_ignores_unknown_keys(self):
        data = {
            "task_id": "x",
            "status": "completed",
            "success": True,
            "unknown_field": "should be ignored",
        }
        result = TaskResult.from_dict(data)
        assert result.task_id == "x"
        assert not hasattr(result, "unknown_field")


# ---------------------------------------------------------------------------
# TaskConfig validation
# ---------------------------------------------------------------------------


class TestTaskConfigValidation:
    def test_valid_config(self):
        config = TaskConfig(url="https://example.com", task="Do something")
        assert config.url == "https://example.com"
        assert config.max_steps == 50

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="url must not be empty"):
            TaskConfig(url="", task="Do something")

    def test_whitespace_only_url_raises(self):
        with pytest.raises(ValueError, match="url must not be empty"):
            TaskConfig(url="   ", task="Do something")

    def test_empty_task_raises(self):
        with pytest.raises(ValueError, match="task must not be empty"):
            TaskConfig(url="https://example.com", task="")

    def test_task_over_2000_chars_raises(self):
        with pytest.raises(ValueError, match="2000 characters"):
            TaskConfig(url="https://example.com", task="x" * 2001)

    def test_max_steps_below_1_raises(self):
        with pytest.raises(ValueError, match="max_steps"):
            TaskConfig(url="https://example.com", task="t", max_steps=0)

    def test_timeout_below_1_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            TaskConfig(url="https://example.com", task="t", timeout_seconds=0)

    def test_negative_retry_attempts_raises(self):
        with pytest.raises(ValueError, match="retry_attempts"):
            TaskConfig(url="https://example.com", task="t", retry_attempts=-1)

    def test_max_cost_cents_zero_raises(self):
        with pytest.raises(ValueError, match="max_cost_cents"):
            TaskConfig(url="https://example.com", task="t", max_cost_cents=0)

    def test_max_cost_cents_positive_ok(self):
        config = TaskConfig(url="https://example.com", task="t", max_cost_cents=100)
        assert config.max_cost_cents == 100

    def test_invalid_schema_type_raises(self):
        with pytest.raises(ValueError, match="invalid type expression"):
            TaskConfig(
                url="https://example.com",
                task="t",
                output_schema={"x": "uuid"},
            )

    def test_valid_schema_types_pass(self):
        config = TaskConfig(
            url="https://example.com",
            task="t",
            output_schema={"price": "float", "tags": "list[str]"},
        )
        assert config.output_schema == {"price": "float", "tags": "list[str]"}

    def test_new_fields_present(self):
        config = TaskConfig(
            url="https://example.com",
            task="t",
            session_id="s1",
            idempotency_key="ik1",
            webhook_url="https://hook.example.com",
            max_cost_cents=500,
        )
        assert config.session_id == "s1"
        assert config.idempotency_key == "ik1"
        assert config.webhook_url == "https://hook.example.com"
        assert config.max_cost_cents == 500


# ---------------------------------------------------------------------------
# Local mode
# ---------------------------------------------------------------------------


class TestLocalMode:
    @patch("computeruse.executor.TaskExecutor")
    def test_run_task_returns_result(self, mock_executor_cls):
        """Mock executor, verify run_task() returns correct TaskResult."""
        expected = _make_result()
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value=expected)
        mock_executor_cls.return_value = mock_instance

        cu = ComputerUse(local=True)
        result = cu.run_task(
            url="https://example.com",
            task="Extract the title",
            output_schema={"title": "str"},
        )

        assert result.task_id == expected.task_id
        assert result.success is True
        assert result.result == {"title": "Hello"}
        mock_instance.execute.assert_called_once()

    @patch("computeruse.executor.TaskExecutor")
    def test_run_task_async_works(self, mock_executor_cls):
        """Verify run_task_async returns correctly."""
        expected = _make_result()
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value=expected)
        mock_executor_cls.return_value = mock_instance

        cu = ComputerUse(local=True)
        result = asyncio.run(
            cu.run_task_async(url="https://example.com", task="Do it")
        )

        assert result.task_id == expected.task_id
        assert result.success is True

    @patch("computeruse.executor.TaskExecutor")
    def test_each_call_gets_own_executor(self, mock_executor_cls):
        """Verify concurrent run_task calls each create their own executor."""
        expected = _make_result()
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value=expected)
        mock_executor_cls.return_value = mock_instance

        cu = ComputerUse(local=True)
        cu.run_task(url="https://a.com", task="Task 1")
        cu.run_task(url="https://b.com", task="Task 2")

        assert mock_executor_cls.call_count == 2


# ---------------------------------------------------------------------------
# Cloud mode
# ---------------------------------------------------------------------------


class TestCloudMode:
    def test_cloud_mode_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            ComputerUse(local=False)

    @patch("computeruse.client.httpx.AsyncClient")
    def test_submit_and_poll(self, mock_client_cls):
        """Mock httpx, verify POST + polling flow."""
        # POST response
        post_response = MagicMock()
        post_response.is_success = True
        post_response.json.return_value = {"task_id": "cloud-123"}

        # First poll: still running
        poll_running = MagicMock()
        poll_running.is_success = True
        poll_running.json.return_value = {
            "task_id": "cloud-123",
            "status": "running",
            "success": False,
        }

        # Second poll: completed
        poll_complete = MagicMock()
        poll_complete.is_success = True
        poll_complete.json.return_value = {
            "task_id": "cloud-123",
            "status": "completed",
            "success": True,
            "result": {"data": "extracted"},
            "steps": 3,
            "duration_ms": 2000,
            "created_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:01:00+00:00",
        }

        mock_instance = AsyncMock()
        mock_instance.post.return_value = post_response
        mock_instance.get.side_effect = [poll_running, poll_complete]
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        cu = ComputerUse(api_key="test-key", local=False)
        result = cu.run_task(url="https://example.com", task="Extract data")

        assert result.task_id == "cloud-123"
        assert result.success is True
        assert result.result == {"data": "extracted"}
        mock_instance.post.assert_called_once()
        assert mock_instance.get.call_count == 2


class TestParseCloudResult:
    def test_minimal_response(self):
        data = {"task_id": "t1", "status": "completed", "success": True}
        result = _parse_cloud_result(data)
        assert result.task_id == "t1"
        assert result.success is True
        assert result.steps == 0
        assert result.created_at is not None

    def test_full_response(self):
        data = {
            "task_id": "t2",
            "status": "failed",
            "success": False,
            "error": "Page not found",
            "steps": 10,
            "duration_ms": 5000,
            "created_at": "2026-03-10T12:00:00+00:00",
            "completed_at": "2026-03-10T12:01:00+00:00",
            "replay_url": "https://replay.example.com/t2",
        }
        result = _parse_cloud_result(data)
        assert result.error == "Page not found"
        assert result.replay_url == "https://replay.example.com/t2"
        assert result.completed_at is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_url_raises_value_error(self):
        cu = ComputerUse(local=True)
        with pytest.raises(ValueError, match="url must not be empty"):
            cu.run_task(url="", task="Do something")

    def test_whitespace_url_raises_value_error(self):
        cu = ComputerUse(local=True)
        with pytest.raises(ValueError, match="url must not be empty"):
            cu.run_task(url="   ", task="Do something")

    def test_task_over_2000_chars_raises_value_error(self):
        cu = ComputerUse(local=True)
        with pytest.raises(ValueError, match="2000 characters"):
            cu.run_task(url="https://example.com", task="x" * 2001)

    def test_unsupported_schema_type_raises_value_error(self):
        cu = ComputerUse(local=True)
        with pytest.raises(ValueError, match="invalid type expression"):
            cu.run_task(
                url="https://example.com",
                task="Get data",
                output_schema={"x": "uuid"},
            )

    def test_no_api_key_raises_environment_error_on_execute(self):
        """EnvironmentError raised on first LLM call, not __init__."""
        # __init__ should succeed
        cu = ComputerUse(local=True)

        # Clear the API key to simulate missing key
        with patch("computeruse.client.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = None
            mock_settings.DEFAULT_MODEL = "claude-sonnet-4-5"
            mock_settings.BROWSERBASE_API_KEY = None
            mock_settings.DEFAULT_MAX_STEPS = 50
            mock_settings.DEFAULT_TIMEOUT = 300

            with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
                cu.run_task(url="https://example.com", task="Do something")

    def test_cloud_mode_without_api_key_raises_value_error(self):
        with pytest.raises(ValueError, match="api_key"):
            ComputerUse(local=False, api_key=None)


# ---------------------------------------------------------------------------
# Poll retry on network error
# ---------------------------------------------------------------------------


class TestPollRetry:
    @patch("computeruse.client.asyncio.sleep", new_callable=AsyncMock)
    @patch("computeruse.client.httpx.AsyncClient")
    def test_retries_10_times_then_raises_network_error(self, mock_client_cls, mock_sleep):
        """Mock httpx.AsyncClient.get to raise ConnectError.

        Assert .get called exactly 10 times, final exception is NetworkError.
        """
        # POST succeeds
        post_response = MagicMock()
        post_response.is_success = True
        post_response.json.return_value = {"task_id": "retry-task"}

        mock_instance = AsyncMock()
        mock_instance.post.return_value = post_response
        mock_instance.get.side_effect = httpx.ConnectError("Connection refused")
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        cu = ComputerUse(api_key="test-key", local=False)

        # retry_attempts=1 means 1 outer attempt. The inner poll retry
        # exhausts 10 times, raises NetworkError, which the outer
        # RetryHandler wraps in RetryExhaustedError.
        with pytest.raises(RetryExhaustedError) as exc_info:
            cu.run_task(
                url="https://example.com",
                task="Test retry",
                retry_attempts=1,
            )

        # The underlying error should be NetworkError with "retry" in message
        assert isinstance(exc_info.value.last_error, NetworkError)
        assert "retry" in str(exc_info.value.last_error).lower()
        assert mock_instance.get.call_count == 10

    @patch("computeruse.client.httpx.AsyncClient")
    def test_recovers_after_transient_errors(self, mock_client_cls):
        """Network errors followed by success should still complete."""
        post_response = MagicMock()
        post_response.is_success = True
        post_response.json.return_value = {"task_id": "recover-task"}

        poll_complete = MagicMock()
        poll_complete.is_success = True
        poll_complete.json.return_value = {
            "task_id": "recover-task",
            "status": "completed",
            "success": True,
            "result": {"ok": True},
            "steps": 1,
            "duration_ms": 100,
            "created_at": "2026-01-01T00:00:00+00:00",
        }

        # 3 network errors then success
        mock_instance = AsyncMock()
        mock_instance.post.return_value = post_response
        mock_instance.get.side_effect = [
            httpx.ConnectError("err1"),
            httpx.ConnectError("err2"),
            httpx.ConnectError("err3"),
            poll_complete,
        ]
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        cu = ComputerUse(api_key="test-key", local=False)
        result = cu.run_task(url="https://example.com", task="Recover")

        assert result.success is True
        assert result.task_id == "recover-task"
        assert mock_instance.get.call_count == 4


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_sdk_error_is_base(self):
        from computeruse.exceptions import (
            APIError,
            AuthenticationError,
            BrowserError,
            NetworkError,
            RateLimitError,
            RetryExhaustedError,
            ServiceUnavailableError,
            TaskTimeoutError,
            ValidationError,
        )

        for exc_cls in (
            APIError,
            AuthenticationError,
            BrowserError,
            NetworkError,
            RateLimitError,
            RetryExhaustedError,
            ServiceUnavailableError,
            TaskTimeoutError,
            ValidationError,
        ):
            assert issubclass(exc_cls, ComputerUseSDKError)

    def test_backward_compat_aliases(self):
        from computeruse.exceptions import ComputerUseError, TimeoutError

        assert ComputerUseError is ComputerUseSDKError
        assert TimeoutError is not None  # not the builtin
        err = TimeoutError("test")
        assert isinstance(err, ComputerUseSDKError)

    def test_rate_limit_error_has_retry_after(self):
        from computeruse.exceptions import RateLimitError

        err = RateLimitError("slow down", retry_after_seconds=30.0)
        assert err.retry_after_seconds == 30.0
        d = err.to_dict()
        assert d["retry_after_seconds"] == 30.0
