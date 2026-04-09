"""
tests/unit/test_shutdown.py — Unit tests for graceful shutdown handler.

Tests:
- SIGTERM stops consumer (sets shutdown flag)
- Partial state saved on interrupted task
- Browser resources released on shutdown
- Redis lock released on shutdown
- In-flight task registry lifecycle
- Process shutdown handler does immediate cleanup
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _reset_shutdown_state() -> None:
    """Reset all module-level state between tests."""
    import time
    from workers import shutdown

    shutdown._shutting_down.clear()
    shutdown._in_flight.clear()
    # Set to current time so the Redis poll interval hasn't elapsed — prevents
    # the unit tests from actually connecting to Redis (which may be running
    # locally with the worker_shutdown_flag key set from a previous run).
    shutdown._last_redis_check = time.monotonic()
    shutdown._redis_shutdown_cached = False


class TestShutdownSignal:
    """Tests for shutdown flag behavior."""

    def setup_method(self) -> None:
        _reset_shutdown_state()

    def test_is_shutting_down_false_initially(self) -> None:
        from workers.shutdown import is_shutting_down

        # Redis connection will fail (no server), caught by except Exception.
        # Event is clear and cache is False, so returns False.
        assert is_shutting_down() is False

    def test_is_shutting_down_true_after_signal(self) -> None:
        from workers.shutdown import _shutting_down, is_shutting_down

        _shutting_down.set()
        assert is_shutting_down() is True

    def test_worker_shutting_down_sets_flag(self) -> None:
        from workers.shutdown import GracefulShutdownHandler, is_shutting_down

        # Redis setex will fail silently (no server), but flag is still set in-memory
        handler = GracefulShutdownHandler(grace_period=1)
        handler._on_worker_shutting_down(sig="SIGTERM", how="warm", exitcode=0)
        assert is_shutting_down() is True

    def test_is_shutting_down_caches_redis_result(self) -> None:
        """Once Redis flag is detected, it should be cached in-memory."""
        from workers import shutdown
        from workers.shutdown import is_shutting_down

        shutdown._redis_shutdown_cached = True
        # Should return True via cache without needing Redis
        assert is_shutting_down() is True


class TestInFlightRegistry:
    """Tests for register/deregister in-flight task tracking."""

    def setup_method(self) -> None:
        _reset_shutdown_state()

    def test_register_and_deregister(self) -> None:
        from workers.shutdown import _in_flight, deregister_in_flight, register_in_flight

        mock_lock = MagicMock()
        register_in_flight(
            task_id="task-1",
            lock=mock_lock,
            browser_manager=None,
            step_data=[],
            config_json='{"url": "https://example.com"}',
        )
        assert "task-1" in _in_flight

        deregister_in_flight("task-1")
        assert "task-1" not in _in_flight

    def test_deregister_nonexistent_is_noop(self) -> None:
        from workers.shutdown import deregister_in_flight

        # Should not raise
        deregister_in_flight("nonexistent-task")

    def test_multiple_tasks_tracked(self) -> None:
        from workers.shutdown import _in_flight, register_in_flight

        for i in range(3):
            register_in_flight(
                task_id=f"task-{i}",
                lock=MagicMock(),
                browser_manager=None,
                step_data=[],
                config_json="{}",
            )
        assert len(_in_flight) == 3


class TestPartialStateSave:
    """Tests for saving partial state and requeueing tasks."""

    def setup_method(self) -> None:
        _reset_shutdown_state()

    @patch("workers.db.get_sync_session")
    def test_interrupted_task_status_set_to_queued(self, mock_get_session: MagicMock) -> None:
        from workers.shutdown import InFlightTask, _save_partial_and_requeue

        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        entry = InFlightTask(
            task_id="task-requeue",
            lock=MagicMock(),
            browser_manager=None,
            step_data=[],  # No steps -> skip replay
            config_json='{"url": "https://example.com"}',
        )

        _save_partial_and_requeue("task-requeue", entry)

        # Verify SQL was executed to requeue
        mock_session.execute.assert_called_once()
        sql_call = mock_session.execute.call_args
        # The text object's text attribute contains the SQL
        sql_text = str(sql_call[0][0].text) if hasattr(sql_call[0][0], 'text') else str(sql_call[0][0])
        assert "status = 'queued'" in sql_text
        assert "worker_id = NULL" in sql_text
        assert "started_at = NULL" in sql_text
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    @patch("workers.db.get_sync_session")
    def test_worker_id_cleared_on_requeue(self, mock_get_session: MagicMock) -> None:
        from workers.shutdown import InFlightTask, _save_partial_and_requeue

        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        entry = InFlightTask(
            task_id="task-clear-worker",
            lock=MagicMock(),
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        _save_partial_and_requeue("task-clear-worker", entry)

        sql_call = mock_session.execute.call_args
        sql_text = str(sql_call[0][0].text) if hasattr(sql_call[0][0], 'text') else str(sql_call[0][0])
        assert "worker_id = NULL" in sql_text

    @patch("workers.db.get_sync_session")
    def test_db_error_does_not_raise(self, mock_get_session: MagicMock) -> None:
        from workers.shutdown import InFlightTask, _save_partial_and_requeue

        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("DB connection lost")
        mock_get_session.return_value = mock_session

        entry = InFlightTask(
            task_id="task-db-err",
            lock=MagicMock(),
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        # Should not raise
        _save_partial_and_requeue("task-db-err", entry)
        mock_session.rollback.assert_called_once()


class TestBrowserRelease:
    """Tests for browser resource release during shutdown."""

    def setup_method(self) -> None:
        _reset_shutdown_state()

    def test_browser_released_on_shutdown(self) -> None:
        from workers.shutdown import InFlightTask, _release_resources

        mock_lock = MagicMock()
        mock_bm = MagicMock()
        mock_bm._session_id = None
        mock_bm._playwright = None

        # _release_cloud_session is async, mock it
        async def noop_release():
            pass

        mock_bm._release_cloud_session = MagicMock(return_value=noop_release())

        entry = InFlightTask(
            task_id="task-browser",
            lock=mock_lock,
            browser_manager=mock_bm,
            step_data=[],
            config_json="{}",
        )

        _release_resources(entry)

        # Lock should be released
        mock_lock.release.assert_called_once()
        # Cloud session release should be attempted
        mock_bm._release_cloud_session.assert_called_once()

    def test_browser_release_error_does_not_block_shutdown(self) -> None:
        from workers.shutdown import InFlightTask, _release_resources

        mock_lock = MagicMock()
        mock_bm = MagicMock()

        async def failing_release():
            raise RuntimeError("Session already terminated")

        mock_bm._release_cloud_session = MagicMock(return_value=failing_release())
        mock_bm._playwright = None

        entry = InFlightTask(
            task_id="task-browser-err",
            lock=mock_lock,
            browser_manager=mock_bm,
            step_data=[],
            config_json="{}",
        )

        # Should not raise
        _release_resources(entry)
        mock_lock.release.assert_called_once()

    def test_none_browser_manager_skipped(self) -> None:
        from workers.shutdown import InFlightTask, _release_resources

        mock_lock = MagicMock()
        entry = InFlightTask(
            task_id="task-no-browser",
            lock=mock_lock,
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        _release_resources(entry)
        mock_lock.release.assert_called_once()


class TestRedisLockRelease:
    """Tests for Redis lock release during shutdown."""

    def setup_method(self) -> None:
        _reset_shutdown_state()

    def test_redis_lock_released_on_shutdown(self) -> None:
        from workers.shutdown import InFlightTask, _release_resources

        mock_lock = MagicMock()
        entry = InFlightTask(
            task_id="task-lock",
            lock=mock_lock,
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        _release_resources(entry)
        mock_lock.release.assert_called_once()

    def test_lock_release_error_logged_not_raised(self) -> None:
        from workers.shutdown import InFlightTask, _release_resources

        mock_lock = MagicMock()
        mock_lock.release.side_effect = RuntimeError("Lock already released")

        entry = InFlightTask(
            task_id="task-lock-err",
            lock=mock_lock,
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        # Should not raise
        _release_resources(entry)
        mock_lock.release.assert_called_once()

    def test_none_lock_skipped(self) -> None:
        from workers.shutdown import InFlightTask, _release_resources

        entry = InFlightTask(
            task_id="task-no-lock",
            lock=None,
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        # Should not raise
        _release_resources(entry)


class TestProcessShutdownHandler:
    """Tests for _on_worker_process_shutdown (immediate cleanup, no grace period)."""

    def setup_method(self) -> None:
        _reset_shutdown_state()

    @patch("workers.shutdown._save_partial_and_requeue")
    @patch("workers.shutdown._release_resources")
    def test_no_in_flight_tasks_is_noop(
        self, mock_release: MagicMock, mock_save: MagicMock
    ) -> None:
        """If no tasks are in-flight, nothing to clean up."""
        from workers.shutdown import GracefulShutdownHandler

        handler = GracefulShutdownHandler(grace_period=5)
        handler._on_worker_process_shutdown(pid=12345, exitcode=0)

        mock_save.assert_not_called()
        mock_release.assert_not_called()

    @patch("workers.shutdown._save_partial_and_requeue")
    @patch("workers.shutdown._release_resources")
    def test_remaining_tasks_force_cleaned(
        self, mock_release: MagicMock, mock_save: MagicMock
    ) -> None:
        """Tasks still in-flight at process shutdown should be force-cleaned immediately."""
        from workers.shutdown import GracefulShutdownHandler, register_in_flight

        register_in_flight(
            task_id="task-stuck",
            lock=MagicMock(),
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        handler = GracefulShutdownHandler(grace_period=30)
        handler._on_worker_process_shutdown(pid=12345, exitcode=0)

        mock_save.assert_called_once()
        mock_release.assert_called_once()
        assert mock_save.call_args[0][0] == "task-stuck"

    @patch("workers.shutdown._save_partial_and_requeue")
    @patch("workers.shutdown._release_resources")
    def test_already_deregistered_task_not_cleaned(
        self, mock_release: MagicMock, mock_save: MagicMock
    ) -> None:
        """If task finished (deregistered) before process shutdown, no cleanup needed."""
        from workers.shutdown import (
            GracefulShutdownHandler,
            deregister_in_flight,
            register_in_flight,
        )

        register_in_flight(
            task_id="task-done",
            lock=MagicMock(),
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )
        deregister_in_flight("task-done")

        handler = GracefulShutdownHandler(grace_period=5)
        handler._on_worker_process_shutdown(pid=12345, exitcode=0)

        mock_save.assert_not_called()
        mock_release.assert_not_called()

    @patch("workers.shutdown._save_partial_and_requeue")
    @patch("workers.shutdown._release_resources")
    def test_multiple_remaining_tasks_all_cleaned(
        self, mock_release: MagicMock, mock_save: MagicMock
    ) -> None:
        """All remaining in-flight tasks should be cleaned up."""
        from workers.shutdown import GracefulShutdownHandler, register_in_flight

        for i in range(3):
            register_in_flight(
                task_id=f"task-{i}",
                lock=MagicMock(),
                browser_manager=None,
                step_data=[],
                config_json="{}",
            )

        handler = GracefulShutdownHandler(grace_period=30)
        handler._on_worker_process_shutdown(pid=12345, exitcode=0)

        assert mock_save.call_count == 3
        assert mock_release.call_count == 3

    @patch("workers.shutdown._save_partial_and_requeue")
    @patch("workers.shutdown._release_resources")
    def test_in_flight_cleared_after_shutdown(
        self, mock_release: MagicMock, mock_save: MagicMock
    ) -> None:
        """_in_flight should be empty after process shutdown handler runs."""
        from workers.shutdown import GracefulShutdownHandler, _in_flight, register_in_flight

        register_in_flight(
            task_id="task-clear",
            lock=MagicMock(),
            browser_manager=None,
            step_data=[],
            config_json="{}",
        )

        handler = GracefulShutdownHandler(grace_period=30)
        handler._on_worker_process_shutdown(pid=12345, exitcode=0)

        assert len(_in_flight) == 0
