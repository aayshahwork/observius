"""Tests for PokantTracker — generic agent tracking."""

import asyncio
import base64
import gc
import json
import logging
import signal
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from computeruse.cost import calculate_cost_cents
from computeruse.models import StepData
from computeruse.tracker import PokantTracker, TrackerConfig, create_tracker


class TestPokantTracker:

    def test_basic_tracking(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()

        for i in range(3):
            tracker.record_step(
                action_type="click",
                description=f"Step {i + 1}",
            )

        tracker.complete()

        assert len(tracker.steps) == 3
        for i, step in enumerate(tracker.steps):
            assert isinstance(step, StepData)
            assert step.step_number == i + 1

    def test_screenshot_formats(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()

        raw = b"fake-png-bytes"
        b64 = base64.b64encode(raw).decode()

        tracker.record_step(screenshot=raw)
        tracker.record_step(screenshot=b64)
        tracker.record_step(screenshot=None)

        steps = tracker.steps
        assert steps[0].screenshot_bytes == raw
        assert steps[1].screenshot_bytes == raw  # decoded from base64
        assert steps[2].screenshot_bytes is None

    def test_stuck_detection(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=True,
            stuck_screenshot_threshold=3,
        ))
        tracker.start()

        screenshot = b"identical-screenshot"
        for _ in range(3):
            tracker.record_step(screenshot=screenshot)

        assert tracker.is_stuck is True
        assert tracker.stuck_reason == "visual_stagnation"

    def test_cost_accumulation(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()

        for _ in range(3):
            tracker.record_step(tokens_in=1000, tokens_out=500)

        expected = calculate_cost_cents(3000, 1500)
        assert tracker.cost_cents == expected
        assert tracker.cost_cents > 0

    def test_complete_saves_files(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            save_screenshots=True,
            generate_replay=True,
            enable_stuck_detection=False,
        ))
        tracker.start()

        tracker.record_step(
            action_type="navigate",
            description="Go to page",
            screenshot=b"fake-screenshot-bytes",
        )
        tracker.record_step(
            action_type="click",
            description="Click button",
            screenshot=b"another-screenshot",
        )

        tracker.complete(result={"key": "value"})

        # Runs metadata
        runs_dir = tmp_path / "runs"
        assert runs_dir.exists()
        metadata_file = runs_dir / f"{tracker.task_id}.json"
        assert metadata_file.exists()
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "completed"
        assert metadata["step_count"] == 2
        assert metadata["result"] == {"key": "value"}

        # Screenshots
        ss_dir = tmp_path / "screenshots" / tracker.task_id
        assert ss_dir.exists()
        assert len(list(ss_dir.glob("*.png"))) == 2

        # Replay
        assert tracker.replay_path is not None
        assert Path(tracker.replay_path).exists()

    def test_fail_classifies_error(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(description="before failure")

        tracker.fail(error=ConnectionError("connection refused"))

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "failed"
        assert metadata["error_category"] == "transient_network"

    def test_fail_with_explicit_category(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(description="before failure")

        tracker.fail(error="bad request", error_category="permanent_llm")

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["error_category"] == "permanent_llm"

    def test_api_reporting(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            api_url="http://localhost:8000",
            api_key="cu_test_key",
            task_description="Test task",
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(description="step 1", tokens_in=100, tokens_out=50)

        with patch(
            "computeruse._reporting._report_to_api_sync"
        ) as mock_report:
            mock_report.return_value = True
            tracker.complete()

            mock_report.assert_called_once()
            kwargs = mock_report.call_args.kwargs
            assert kwargs["task_id"] == tracker.task_id
            assert kwargs["status"] == "completed"
            assert kwargs["api_url"] == "http://localhost:8000"
            assert kwargs["api_key"] == "cu_test_key"
            assert kwargs["task_description"] == "Test task"
            assert kwargs["cost_cents"] == tracker.cost_cents

    def test_no_reporting_without_config(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(description="step 1")

        with patch(
            "computeruse._reporting._report_to_api_sync"
        ) as mock_report:
            tracker.complete()
            mock_report.assert_not_called()

    def test_duration_auto_calculated(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()

        time.sleep(0.01)
        tracker.record_step(description="step 1")
        time.sleep(0.01)
        tracker.record_step(description="step 2")

        steps = tracker.steps
        assert steps[0].duration_ms >= 5  # at least ~10ms sleep
        assert steps[1].duration_ms >= 5

    def test_create_tracker_factory(self, tmp_path: Path) -> None:
        tracker = create_tracker(
            task_description="test task",
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        )
        assert isinstance(tracker, PokantTracker)
        assert tracker.task_id  # non-empty UUID string


class TestTrackerAutoScreenshot:
    """Tests for page auto-screenshot detection and capture."""

    def test_page_auto_detect_async_playwright(self) -> None:
        """Async screenshot method (Playwright async API) is detected."""
        async def fake_screenshot(**kwargs: object) -> bytes:
            return b"async-screenshot"

        page = SimpleNamespace(screenshot=fake_screenshot)
        tracker = PokantTracker(TrackerConfig(
            enable_stuck_detection=False,
            page=page,
        ))
        assert tracker._screenshot_fn is fake_screenshot

    def test_page_auto_detect_sync_selenium(self) -> None:
        """Selenium's get_screenshot_as_png is detected."""
        def fake_get_screenshot_as_png() -> bytes:
            return b"selenium-screenshot"

        page = SimpleNamespace(get_screenshot_as_png=fake_get_screenshot_as_png)
        tracker = PokantTracker(TrackerConfig(
            enable_stuck_detection=False,
            page=page,
        ))
        assert tracker._screenshot_fn is fake_get_screenshot_as_png

    def test_page_auto_detect_generic(self) -> None:
        """Plain sync screenshot() method is detected."""
        def fake_screenshot() -> bytes:
            return b"generic-screenshot"

        page = SimpleNamespace(screenshot=fake_screenshot)
        tracker = PokantTracker(TrackerConfig(
            enable_stuck_detection=False,
            page=page,
        ))
        assert tracker._screenshot_fn is fake_screenshot

    def test_page_none_no_screenshot(self, tmp_path: Path) -> None:
        """page=None results in no screenshot function and no crash."""
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_step(action_type="click", description="no page")
        assert step.screenshot_bytes is None

    def test_page_no_method_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Page object with no screenshot method logs a warning."""
        page = SimpleNamespace(title="fake page")
        with caplog.at_level(logging.WARNING, logger="pokant"):
            tracker = PokantTracker(TrackerConfig(
                enable_stuck_detection=False,
                page=page,
            ))
        assert tracker._screenshot_fn is None
        assert "Cannot auto-detect screenshot method" in caplog.text

    def test_auto_screenshot_on_record_step(self, tmp_path: Path) -> None:
        """Sync page auto-captures screenshot when none passed."""
        fake_bytes = b"auto-captured-screenshot"

        def fake_screenshot() -> bytes:
            return fake_bytes

        page = SimpleNamespace(screenshot=fake_screenshot)
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            page=page,
        ))
        tracker.start()
        step = tracker.record_step(action_type="click", description="auto")
        assert step.screenshot_bytes == fake_bytes

    def test_arecord_step_async_screenshot(self, tmp_path: Path) -> None:
        """arecord_step awaits async screenshot functions."""
        fake_bytes = b"async-auto-captured"

        async def fake_screenshot(**kwargs: object) -> bytes:
            return fake_bytes

        page = SimpleNamespace(screenshot=fake_screenshot)
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            page=page,
        ))
        tracker.start()

        step = asyncio.run(tracker.arecord_step(
            action_type="navigate",
            description="async auto",
        ))
        assert step.screenshot_bytes == fake_bytes

    def test_explicit_screenshot_overrides(self, tmp_path: Path) -> None:
        """Explicit screenshot= takes precedence over auto-capture."""
        explicit_bytes = b"explicit-screenshot"

        def fake_screenshot() -> bytes:
            return b"should-not-be-used"

        page = SimpleNamespace(screenshot=fake_screenshot)
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            page=page,
        ))
        tracker.start()
        step = tracker.record_step(
            action_type="click",
            description="explicit",
            screenshot=explicit_bytes,
        )
        assert step.screenshot_bytes == explicit_bytes

    def test_screenshot_fn_failure_silent(self, tmp_path: Path) -> None:
        """Screenshot function that raises does not crash record_step."""
        def bad_screenshot() -> bytes:
            raise RuntimeError("browser crashed")

        page = SimpleNamespace(screenshot=bad_screenshot)
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            page=page,
        ))
        tracker.start()
        step = tracker.record_step(action_type="click", description="crash")
        assert step.screenshot_bytes is None
        assert step.action_type == "click"

    def test_backward_compat_no_page(self, tmp_path: Path) -> None:
        """Tracker without page works identically to pre-change behavior."""
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()

        tracker.record_step(action_type="api_call", description="Step 1")
        tracker.record_step(
            action_type="extract",
            description="Step 2",
            screenshot=b"manual-screenshot",
        )
        tracker.complete()

        assert len(tracker.steps) == 2
        assert tracker.steps[0].screenshot_bytes is None
        assert tracker.steps[1].screenshot_bytes == b"manual-screenshot"

        runs_dir = tmp_path / "runs"
        metadata = json.loads(
            (runs_dir / f"{tracker.task_id}.json").read_text()
        )
        assert metadata["status"] == "completed"


class TestScreenshotFn:
    """Tests for explicit screenshot_fn parameter."""

    def test_screenshot_fn_overrides_page(self, tmp_path: Path) -> None:
        """screenshot_fn takes priority over page auto-detect."""
        page = SimpleNamespace(screenshot=lambda: b"page-screenshot")
        fn_bytes = b"fn-screenshot"
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            page=page,
            screenshot_fn=lambda: fn_bytes,
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_step(action_type="click", description="test")
        assert step.screenshot_bytes == fn_bytes

    def test_screenshot_fn_sync(self, tmp_path: Path) -> None:
        """Sync screenshot_fn returns bytes used as screenshot."""
        expected = b"custom-screenshot"
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            screenshot_fn=lambda: expected,
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_step(action_type="click", description="test")
        assert step.screenshot_bytes == expected

    async def test_screenshot_fn_async(self, tmp_path: Path) -> None:
        """Async screenshot_fn works with arecord_step."""
        expected = b"async-screenshot"

        async def async_fn() -> bytes:
            return expected

        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            screenshot_fn=async_fn,
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = await tracker.arecord_step(action_type="click", description="test")
        assert step.screenshot_bytes == expected

    def test_screenshot_fn_failure_silent(self, tmp_path: Path) -> None:
        """screenshot_fn exception doesn't prevent step recording."""
        def failing_fn() -> bytes:
            raise RuntimeError("screenshot failed")

        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            screenshot_fn=failing_fn,
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_step(action_type="click", description="test")
        assert step.screenshot_bytes is None
        assert step.action_type == "click"


class TestStepContext:
    """Tests for step context system."""

    def test_record_step_with_context(self, tmp_path: Path) -> None:
        ctx = {"key": "value", "nested": {"a": 1}}
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_step(
            action_type="click",
            description="test",
            context=ctx,
        )
        assert step.context == ctx

    def test_context_in_run_metadata(self, tmp_path: Path) -> None:
        ctx = {"prompt": "hello", "response": "world"}
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(action_type="llm_call", context=ctx)
        tracker.complete()

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["steps"][0]["context"] == ctx

    def test_record_llm_step(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_llm_step(
            prompt="What should I do?",
            response="Click the button.",
            model="claude-sonnet-4-6",
            tokens_in=100,
            tokens_out=50,
        )
        assert step.action_type == "llm_call"
        assert step.context["type"] == "llm_call"
        assert step.context["prompt"] == "What should I do?"
        assert step.context["response"] == "Click the button."
        assert step.context["model"] == "claude-sonnet-4-6"
        assert step.tokens_in == 100
        assert step.tokens_out == 50

    def test_record_api_step(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_api_step(
            method="GET",
            url="https://api.example.com/data",
            status_code=200,
            response_body={"key": "value"},
        )
        assert step.action_type == "api_call"
        assert step.context["type"] == "api_call"
        assert step.context["method"] == "GET"
        assert step.context["url"] == "https://api.example.com/data"
        assert step.context["status_code"] == 200
        assert step.context["response_body"] == {"key": "value"}
        assert "GET https://api.example.com/data" in step.description

    def test_record_state_snapshot(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_state_snapshot(
            state={"page": "checkout", "items": 3},
            decision="Cart is full, proceeding",
        )
        assert step.action_type == "state_snapshot"
        assert step.context["type"] == "state_snapshot"
        assert step.context["state"] == {"page": "checkout", "items": 3}
        assert step.context["decision"] == "Cart is full, proceeding"
        assert step.description == "Cart is full, proceeding"

    def test_context_truncation(self) -> None:
        from computeruse.tracker import _safe_serialize

        large_obj = {"data": "x" * 20000}
        result = _safe_serialize(large_obj, max_length=100)
        assert isinstance(result, str)
        assert len(result) <= 115  # 100 + len("...(truncated)")
        assert result.endswith("...(truncated)")

    def test_context_serialization(self) -> None:
        from datetime import datetime as dt
        from computeruse.tracker import _safe_serialize

        # datetime handled by default=str
        result = _safe_serialize({"ts": dt(2024, 1, 1)})
        assert result is not None
        assert isinstance(result, dict)

        # Circular reference triggers ValueError fallback
        d: dict = {}
        d["self"] = d
        result = _safe_serialize(d)
        assert isinstance(result, str)

    def test_replay_includes_context(self, tmp_path: Path) -> None:
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(
            action_type="llm_call",
            description="test step",
            context={"prompt": "hello world", "response": "goodbye world"},
        )
        tracker.complete()

        replay_path = tracker.replay_path
        assert replay_path is not None
        html = Path(replay_path).read_text()
        assert "hello world" in html
        assert "goodbye world" in html

    def test_non_serializable_context_in_metadata(self, tmp_path: Path) -> None:
        """Non-JSON-serializable context doesn't crash save/replay."""
        from datetime import datetime as dt

        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        tracker.record_step(
            action_type="custom",
            context={"ts": dt(2024, 1, 1), "data": "ok"},
        )
        tracker.complete()  # should not raise

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["steps"][0]["context"]["data"] == "ok"
        assert "2024" in metadata["steps"][0]["context"]["ts"]


class TestDesktopTracking:
    """Tests for record_desktop_step and desktop ActionTypes."""

    def test_record_desktop_step(self, tmp_path: Path) -> None:
        """record_desktop_step creates step with desktop context."""
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_desktop_step(
            action_type="desktop_click",
            description="Clicked OK button",
            window_title="Settings",
        )
        assert step.action_type == "desktop_click"
        assert step.description == "Clicked OK button"
        assert step.context is not None
        assert step.context["type"] == "desktop_action"
        assert step.context["window_title"] == "Settings"
        assert "coordinates" not in step.context

    def test_record_desktop_step_with_coordinates(self, tmp_path: Path) -> None:
        """Coordinates are stored in context when provided."""
        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
        ))
        tracker.start()
        step = tracker.record_desktop_step(
            action_type="desktop_click",
            description="Clicked at (100, 200)",
            window_title="App",
            coordinates=(100, 200),
        )
        assert step.context is not None
        assert step.context["coordinates"] == {"x": 100, "y": 200}

    def test_desktop_action_types_exist(self) -> None:
        """All desktop ActionType values exist on the enum."""
        from computeruse.models import ActionType

        expected = [
            "desktop_click", "desktop_type", "desktop_hotkey",
            "desktop_scroll", "desktop_drag", "desktop_launch",
            "desktop_focus", "window_switch", "menu_select",
            "file_open", "file_save",
        ]
        for value in expected:
            assert value in ActionType._value2member_map_, f"Missing ActionType: {value}"


class TestContextManager:
    """Tests for context manager crash resilience."""

    def test_context_manager_success(self, tmp_path: Path) -> None:
        """Normal exit auto-calls complete()."""
        with PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        )) as tracker:
            tracker.record_step(action_type="click", description="step 1")

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "completed"
        assert metadata["step_count"] == 1

    def test_context_manager_exception(self, tmp_path: Path) -> None:
        """Exception inside with-block saves as failure."""
        with pytest.raises(ValueError, match="boom"):
            with PokantTracker(TrackerConfig(
                output_dir=str(tmp_path),
                enable_stuck_detection=False,
                generate_replay=False,
                save_screenshots=False,
            )) as tracker:
                tracker.record_step(action_type="click", description="step 1")
                raise ValueError("boom")

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "failed"
        assert "boom" in metadata["error"]

    def test_context_manager_keyboard_interrupt(self, tmp_path: Path) -> None:
        """KeyboardInterrupt inside with-block saves as failure."""
        with pytest.raises(KeyboardInterrupt):
            with PokantTracker(TrackerConfig(
                output_dir=str(tmp_path),
                enable_stuck_detection=False,
                generate_replay=False,
                save_screenshots=False,
            )) as tracker:
                tracker.record_step(action_type="click", description="step 1")
                raise KeyboardInterrupt()

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "failed"
        assert metadata["step_count"] == 1

    def test_context_manager_already_completed(self, tmp_path: Path) -> None:
        """No double-save if complete() called manually inside with-block."""
        with PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        )) as tracker:
            tracker.record_step(action_type="click", description="step")
            tracker.complete(result={"ok": True})

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "completed"
        assert metadata["result"] == {"ok": True}

    async def test_async_context_manager(self, tmp_path: Path) -> None:
        """Async with-block auto-completes on normal exit."""
        async with PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        )) as tracker:
            await tracker.arecord_step(action_type="click", description="async step")

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "completed"

    async def test_async_context_manager_exception(self, tmp_path: Path) -> None:
        """Async with-block saves failure on exception."""
        with pytest.raises(RuntimeError, match="async boom"):
            async with PokantTracker(TrackerConfig(
                output_dir=str(tmp_path),
                enable_stuck_detection=False,
                generate_replay=False,
                save_screenshots=False,
            )) as tracker:
                await tracker.arecord_step(action_type="click", description="step")
                raise RuntimeError("async boom")

        metadata_file = tmp_path / "runs" / f"{tracker.task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "failed"
        assert "async boom" in metadata["error"]


class TestDel:
    """Tests for __del__ fallback."""

    def test_del_saves_data(self, tmp_path: Path) -> None:
        """__del__ saves data when tracker is GC'd without complete/fail."""
        config = TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        )
        tracker = PokantTracker(config=config)
        tracker.start()
        tracker.record_step(action_type="click", description="orphaned step")
        task_id = tracker.task_id

        del tracker
        gc.collect()

        metadata_file = tmp_path / "runs" / f"{task_id}.json"
        assert metadata_file.exists()
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "failed"
        assert "destroyed" in metadata["error"].lower()

    def test_del_no_op_if_completed(self, tmp_path: Path) -> None:
        """__del__ is a no-op if complete() was already called."""
        config = TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        )
        tracker = PokantTracker(config=config)
        tracker.start()
        tracker.record_step(action_type="click", description="step")
        tracker.complete()
        task_id = tracker.task_id

        # Verify completed status
        metadata_file = tmp_path / "runs" / f"{task_id}.json"
        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "completed"

        # __del__ should not overwrite
        del tracker
        gc.collect()

        metadata = json.loads(metadata_file.read_text())
        assert metadata["status"] == "completed"


class TestSignalHandler:
    """Tests for SIGINT handler registration and restoration."""

    def test_signal_handler_restored_after_complete(self, tmp_path: Path) -> None:
        """Original SIGINT handler is restored after complete()."""
        original = signal.getsignal(signal.SIGINT)

        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        ))
        tracker.start()

        # Handler should be ours now
        during = signal.getsignal(signal.SIGINT)
        assert during != original

        tracker.complete()

        # Original restored
        assert signal.getsignal(signal.SIGINT) == original

    def test_signal_handler_restored_after_fail(self, tmp_path: Path) -> None:
        """Original SIGINT handler is restored after fail()."""
        original = signal.getsignal(signal.SIGINT)

        tracker = PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        ))
        tracker.start()
        tracker.record_step(action_type="click", description="step")
        tracker.fail(error="test error")

        assert signal.getsignal(signal.SIGINT) == original

    def test_signal_handler_restored_by_context_manager(self, tmp_path: Path) -> None:
        """Context manager restores original handler on exit."""
        original = signal.getsignal(signal.SIGINT)

        with PokantTracker(TrackerConfig(
            output_dir=str(tmp_path),
            enable_stuck_detection=False,
            generate_replay=False,
            save_screenshots=False,
        )) as tracker:
            tracker.record_step(action_type="click", description="step")

        assert signal.getsignal(signal.SIGINT) == original
