"""Tests for computeruse._reporting — API result reporting."""

import base64
import json
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from computeruse._reporting import _encode_screenshot, _try_compile_workflow, report_to_api


# ---------------------------------------------------------------------------
# report_to_api
# ---------------------------------------------------------------------------


class TestReportToApi:
    """Tests for report_to_api()."""

    @pytest.fixture()
    def sample_steps(self):
        return [
            SimpleNamespace(
                action_type="click",
                description="click(#btn)",
                tokens_in=100,
                tokens_out=50,
                duration_ms=200,
                success=True,
                error=None,
                screenshot_bytes=None,
            ),
        ]

    async def test_sends_correct_post(self, sample_steps):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.return_value = MagicMock()
            result = await report_to_api(
                api_url="http://localhost:3000",
                api_key="test-key-123",
                task_id="task-abc",
                task_description="Test task",
                status="completed",
                steps=sample_steps,
                cost_cents=1.5,
                error_category=None,
                error_message=None,
                duration_ms=500,
                created_at=created,
            )

        assert result is True
        mock.assert_called_once()
        req = mock.call_args[0][0]
        assert req.full_url == "http://localhost:3000/api/v1/tasks/ingest"
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("X-api-key") == "test-key-123"

        body = json.loads(req.data.decode("utf-8"))
        # Non-UUID task_id is replaced with a generated UUID; original appears in description
        import uuid as _uuid
        _uuid.UUID(body["task_id"])  # must be a valid UUID now
        assert body["task_description"] == "[task-abc] Test task"
        assert body["status"] == "completed"
        assert body["executor_mode"] == "sdk"
        assert body["cost_cents"] == 1.5
        assert body["total_tokens_in"] == 100
        assert body["total_tokens_out"] == 50
        assert body["duration_ms"] == 500
        assert len(body["steps"]) == 1
        assert body["steps"][0]["step_number"] == 1
        assert body["steps"][0]["action_type"] == "click"
        assert body["steps"][0]["success"] is True
        assert body["created_at"] is not None
        assert body["completed_at"] is not None

    async def test_returns_true_on_success(self):
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.return_value = MagicMock()
            result = await report_to_api(
                api_url="http://localhost:3000",
                api_key="key",
                task_id="t1",
                task_description="",
                status="completed",
                steps=[],
                cost_cents=0.0,
                error_category=None,
                error_message=None,
                duration_ms=0,
                created_at=datetime.now(timezone.utc),
            )
        assert result is True

    async def test_returns_false_on_network_error(self):
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.side_effect = urllib.error.URLError("Connection refused")
            result = await report_to_api(
                api_url="http://localhost:3000",
                api_key="key",
                task_id="t2",
                task_description="",
                status="completed",
                steps=[],
                cost_cents=0.0,
                error_category=None,
                error_message=None,
                duration_ms=0,
                created_at=datetime.now(timezone.utc),
            )
        assert result is False

    async def test_returns_false_on_http_error(self):
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.side_effect = urllib.error.HTTPError(
                url="http://localhost:3000/api/v1/tasks/ingest",
                code=409,
                msg="Conflict",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )
            result = await report_to_api(
                api_url="http://localhost:3000",
                api_key="key",
                task_id="t3",
                task_description="",
                status="completed",
                steps=[],
                cost_cents=0.0,
                error_category=None,
                error_message=None,
                duration_ms=0,
                created_at=datetime.now(timezone.utc),
            )
        assert result is False

    async def test_strips_trailing_slash_from_api_url(self):
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.return_value = MagicMock()
            await report_to_api(
                api_url="http://localhost:3000/",
                api_key="key",
                task_id="t4",
                task_description="",
                status="completed",
                steps=[],
                cost_cents=0.0,
                error_category=None,
                error_message=None,
                duration_ms=0,
                created_at=datetime.now(timezone.utc),
            )
        req = mock.call_args[0][0]
        assert req.full_url == "http://localhost:3000/api/v1/tasks/ingest"

    async def test_handles_none_created_at(self):
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.return_value = MagicMock()
            result = await report_to_api(
                api_url="http://localhost:3000",
                api_key="key",
                task_id="t5",
                task_description="",
                status="completed",
                steps=[],
                cost_cents=0.0,
                error_category=None,
                error_message=None,
                duration_ms=0,
                created_at=None,
            )
        assert result is True
        body = json.loads(mock.call_args[0][0].data.decode("utf-8"))
        assert body["created_at"] is not None  # falls back to now()

    async def test_encodes_screenshot_in_steps(self):
        steps = [
            SimpleNamespace(
                action_type="click",
                description="",
                tokens_in=0,
                tokens_out=0,
                duration_ms=0,
                success=True,
                error=None,
                screenshot_bytes=b"png-data",
            ),
        ]
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.return_value = MagicMock()
            await report_to_api(
                api_url="http://localhost:3000",
                api_key="key",
                task_id="t6",
                task_description="",
                status="completed",
                steps=steps,
                cost_cents=0.0,
                error_category=None,
                error_message=None,
                duration_ms=0,
                created_at=datetime.now(timezone.utc),
            )
        body = json.loads(mock.call_args[0][0].data.decode("utf-8"))
        expected = base64.b64encode(b"png-data").decode("ascii")
        assert body["steps"][0]["screenshot_base64"] == expected


# ---------------------------------------------------------------------------
# _encode_screenshot
# ---------------------------------------------------------------------------


class TestEncodeScreenshot:
    def test_encodes_bytes(self):
        step = SimpleNamespace(screenshot_bytes=b"hello")
        result = _encode_screenshot(step)
        assert result == base64.b64encode(b"hello").decode("ascii")

    def test_returns_none_for_none(self):
        step = SimpleNamespace(screenshot_bytes=None)
        assert _encode_screenshot(step) is None

    def test_returns_none_for_missing_attr(self):
        step = SimpleNamespace()
        assert _encode_screenshot(step) is None

    def test_returns_none_for_empty_bytes(self):
        step = SimpleNamespace(screenshot_bytes=b"")
        assert _encode_screenshot(step) is None


# ---------------------------------------------------------------------------
# _try_compile_workflow
# ---------------------------------------------------------------------------


class TestTryCompileWorkflow:
    def test_compiles_for_failed_status(self):
        steps = [SimpleNamespace(action_type="click", description="click btn")]
        result = _try_compile_workflow("t1", steps, "https://example.com", "failed")
        assert result is not None

    def test_returns_none_for_empty_steps(self):
        result = _try_compile_workflow("t1", [], "https://example.com", "completed")
        assert result is None

    def test_compiles_completed_run(self):
        steps = [
            SimpleNamespace(
                action_type="navigate",
                description="goto page",
                pre_url="https://example.com",
            ),
            SimpleNamespace(
                action_type="click",
                description="click login",
                selectors=[{"type": "css", "value": "#login", "confidence": 0.9}],
            ),
        ]
        result = _try_compile_workflow("t1", steps, "https://example.com", "completed")
        assert result is not None
        assert result["name"] == "t1"
        assert result["start_url"] == "https://example.com"
        assert len(result["steps"]) == 2

    def test_includes_compiled_workflow_in_payload(self):
        steps = [
            SimpleNamespace(
                action_type="click",
                description="click btn",
                tokens_in=10,
                tokens_out=5,
                duration_ms=100,
                success=True,
                error=None,
                screenshot_bytes=None,
                selectors=[{"type": "css", "value": "#btn", "confidence": 0.95}],
            ),
        ]
        with patch("computeruse._reporting.urllib.request.urlopen") as mock:
            mock.return_value = MagicMock()
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                report_to_api(
                    api_url="http://localhost:3000",
                    api_key="key",
                    task_id="t1",
                    task_description="test",
                    status="completed",
                    steps=steps,
                    cost_cents=0.1,
                    error_category=None,
                    error_message=None,
                    duration_ms=100,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
        body = json.loads(mock.call_args[0][0].data.decode("utf-8"))
        assert "compiled_workflow" in body
        assert body["compiled_workflow"] is not None
        assert body["compiled_workflow"]["name"] == "t1"

    def test_compilation_failure_returns_none(self):
        """If compiler raises, _try_compile_workflow returns None gracefully."""
        with patch("computeruse.compiler.WorkflowCompiler") as mock_cls:
            mock_cls.return_value.compile_from_steps.side_effect = Exception("boom")
            result = _try_compile_workflow("t1", [object()], "https://example.com", "completed")
        assert result is None
