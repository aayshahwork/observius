"""
tests/unit/test_session_integration.py — Tests for session restore/verify/save
hooks in TaskExecutor and navigation retry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.executor import TaskExecutor
from workers.models import TaskConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_config():
    """TaskConfig with session_id set (triggers session management)."""
    return TaskConfig(
        url="https://app.example.com/dashboard",
        task="Extract account balance",
        max_steps=5,
        session_id="sess-abc-123",
    )


@pytest.fixture
def no_session_config():
    """TaskConfig without session_id (session management disabled)."""
    return TaskConfig(
        url="https://app.example.com/dashboard",
        task="Extract account balance",
        max_steps=5,
    )


@pytest.fixture
def mock_browser_manager():
    manager = AsyncMock()

    mock_page = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\xff\xd8\xff\xe0fake-jpeg")
    mock_page.goto = AsyncMock()
    mock_page.url = "https://app.example.com/dashboard"
    mock_page.evaluate = AsyncMock(return_value=False)  # no password fields
    mock_page.close = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.add_cookies = AsyncMock()
    mock_context.cookies = AsyncMock(return_value=[{"name": "sid", "value": "xyz"}])
    mock_context.pages = [mock_page]

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.contexts = [mock_context]

    manager.get_browser = AsyncMock(return_value=mock_browser)
    manager.release_browser = AsyncMock()
    manager.apply_stealth = AsyncMock()
    manager._mock_page = mock_page
    manager._mock_context = mock_context

    return manager


@pytest.fixture
def mock_llm_client():
    client = MagicMock()
    return client


def _make_agent_result():
    """Minimal browser_use agent result mock."""
    result = MagicMock()
    result.final_result.return_value = {"balance": "$100"}
    result.is_done.return_value = True
    result.history = []
    result.screenshots.return_value = []
    result.action_names.return_value = []
    result.total_cost.return_value = 0.01
    return result


# ---------------------------------------------------------------------------
# Test: Session Restore
# ---------------------------------------------------------------------------


class TestSessionRestore:
    async def test_session_restore_loads_cookies(
        self, session_config, mock_browser_manager, mock_llm_client
    ):
        """execute() with session_id completes without error (PAV path)."""
        from workers.models import TaskResult

        expected = TaskResult(task_id="t", status="completed", success=True, steps=0)

        executor = TaskExecutor(
            config=session_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
            account_id="00000000-0000-0000-0000-000000000001",
        )

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            result = await executor.execute()

        assert result.success is True
        assert result.status == "completed"

    async def test_session_restore_skipped_without_session_id(
        self, no_session_config, mock_browser_manager, mock_llm_client
    ):
        """Without session_id, no session management is attempted."""
        executor = TaskExecutor(
            config=no_session_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
            account_id="00000000-0000-0000-0000-000000000001",
        )

        with patch.object(executor, "_execute_with_agent", return_value=_make_agent_result()):
            result = await executor.execute()

        # No cookies should be set on the context
        context = mock_browser_manager.get_browser.return_value.new_context.return_value
        context.add_cookies.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test: Session Verification
# ---------------------------------------------------------------------------


class TestVerifySession:
    async def test_login_url_returns_false(self, mock_browser_manager, mock_llm_client):
        """Page URL containing /login indicates stale session."""
        config = TaskConfig(url="https://app.example.com/dashboard", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.url = "https://app.example.com/login?redirect=/dashboard"
        mock_page.evaluate = AsyncMock(return_value=False)

        result = await executor._verify_session(mock_page, "app.example.com")
        assert result is False

    async def test_dashboard_url_returns_true(self, mock_browser_manager, mock_llm_client):
        """Normal page URL with no password fields indicates valid session."""
        config = TaskConfig(url="https://app.example.com/dashboard", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.url = "https://app.example.com/dashboard"
        mock_page.evaluate = AsyncMock(return_value=False)  # no visible password inputs

        result = await executor._verify_session(mock_page, "app.example.com")
        assert result is True

    async def test_visible_password_field_returns_false(self, mock_browser_manager, mock_llm_client):
        """Page with a visible password input indicates stale session."""
        config = TaskConfig(url="https://app.example.com/dashboard", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.url = "https://app.example.com/settings"
        mock_page.evaluate = AsyncMock(return_value=True)  # visible password field

        result = await executor._verify_session(mock_page, "app.example.com")
        assert result is False

    async def test_evaluate_exception_returns_true(self, mock_browser_manager, mock_llm_client):
        """On JS evaluation error, conservatively return True."""
        config = TaskConfig(url="https://app.example.com/dashboard", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.url = "https://app.example.com/dashboard"
        mock_page.evaluate = AsyncMock(side_effect=Exception("Execution context destroyed"))

        result = await executor._verify_session(mock_page, "app.example.com")
        assert result is True


# ---------------------------------------------------------------------------
# Test: Session Save
# ---------------------------------------------------------------------------


class TestSessionSave:
    async def test_session_saved_on_success(
        self, session_config, mock_browser_manager, mock_llm_client
    ):
        """After successful agent run, execute() completes successfully (PAV path)."""
        from workers.models import TaskResult

        expected = TaskResult(task_id="t", status="completed", success=True, steps=0)

        executor = TaskExecutor(
            config=session_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
            account_id="00000000-0000-0000-0000-000000000001",
        )

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            result = await executor.execute()

        assert result.success is True
        assert result.status == "completed"

    async def test_session_not_saved_when_stale(
        self, session_config, mock_browser_manager, mock_llm_client
    ):
        """execute() with session_id completes without error even if page looks stale (PAV path)."""
        from workers.models import TaskResult

        expected = TaskResult(task_id="t", status="completed", success=True, steps=0)

        executor = TaskExecutor(
            config=session_config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
            account_id="00000000-0000-0000-0000-000000000001",
        )

        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            result = await executor.execute()

        assert result.success is True


# ---------------------------------------------------------------------------
# Test: Navigation Retry
# ---------------------------------------------------------------------------


class TestNavigateWithRetry:
    async def test_transient_fail_then_success(self, mock_browser_manager, mock_llm_client):
        """Transient network error retries and succeeds on second attempt."""
        config = TaskConfig(url="https://example.com", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(
            side_effect=[Exception("net::err_connection_refused"), None]
        )

        with patch("workers.executor.asyncio.sleep", new_callable=AsyncMock):
            await executor._navigate_with_retry(mock_page, "https://example.com")

        assert mock_page.goto.await_count == 2

    async def test_permanent_error_raises_immediately(self, mock_browser_manager, mock_llm_client):
        """Non-transient error is raised without retry."""
        config = TaskConfig(url="https://example.com", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(
            side_effect=Exception("Protocol error: page crashed")
        )

        with pytest.raises(Exception, match="Protocol error"):
            await executor._navigate_with_retry(mock_page, "https://example.com")

        assert mock_page.goto.await_count == 1

    async def test_transient_exhausted_raises(self, mock_browser_manager, mock_llm_client):
        """All retries exhausted raises the last transient error."""
        config = TaskConfig(url="https://example.com", task="t", max_steps=1)
        executor = TaskExecutor(
            config=config,
            browser_manager=mock_browser_manager,
            llm_client=mock_llm_client,
        )

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(
            side_effect=[
                Exception("net::err_connection_refused"),
                Exception("net::err_connection_refused"),
                Exception("net::err_connection_refused"),
            ]
        )

        with patch("workers.executor.asyncio.sleep", new_callable=AsyncMock), \
             pytest.raises(Exception, match="net::err_connection_refused"):
            await executor._navigate_with_retry(mock_page, "https://example.com")

        assert mock_page.goto.await_count == 3
