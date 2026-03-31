"""Tests for computeruse.track — Playwright Page tracking context manager."""

import asyncio
import json
from pathlib import Path

import pytest
from unittest.mock import patch

from computeruse.models import ActionType
from computeruse.track import TrackConfig, TrackedPage, track


# ---------------------------------------------------------------------------
# Mock Playwright objects
# ---------------------------------------------------------------------------


class MockContext:
    async def cookies(self):
        return []

    async def add_cookies(self, cookies):
        pass


class MockPage:
    def __init__(self):
        self._context = MockContext()
        self.url = "https://example.com"

    @property
    def context(self):
        return self._context

    async def goto(self, url, **kw):
        return None

    async def click(self, selector, **kw):
        return None

    async def fill(self, selector, value, **kw):
        return None

    async def type(self, selector, text, **kw):
        return None

    async def select_option(self, selector, **kw):
        return ["option1"]

    async def press(self, selector, key, **kw):
        return None

    async def wait_for_selector(self, selector, **kw):
        return None

    async def screenshot(self, **kw):
        return b"fake-jpeg-bytes"

    async def title(self):
        return "Example Page"

    async def evaluate(self, script, *args):
        return {}


# ---------------------------------------------------------------------------
# Context manager basics
# ---------------------------------------------------------------------------


class TestTrackContextManager:
    async def test_yields_tracked_page(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            assert isinstance(t, TrackedPage)

    async def test_tracked_methods_record_steps(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.goto("https://example.com")
            await t.click("#btn")
            await t.fill("#email", "secret@test.com")
            await t.type("#name", "secret-name")
            await t.select_option("#dropdown")
            await t.press("#input", "Enter")
            await t.wait_for_selector("#loaded")

        assert len(t.steps) == 7
        assert t.steps[0].action_type == ActionType.NAVIGATE
        assert t.steps[1].action_type == ActionType.CLICK
        assert t.steps[2].action_type == ActionType.TYPE
        assert t.steps[3].action_type == ActionType.TYPE
        assert t.steps[4].action_type == "select"
        assert t.steps[5].action_type == ActionType.KEY_PRESS
        assert t.steps[6].action_type == ActionType.WAIT

    async def test_step_numbers_increment(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.goto("https://example.com")
            await t.click("#btn")

        assert t.steps[0].step_number == 1
        assert t.steps[1].step_number == 2

    async def test_steps_have_timing(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        assert t.steps[0].duration_ms >= 0

    async def test_steps_have_timestamps(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        assert t.steps[0].timestamp is not None

    async def test_all_steps_marked_success(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.goto("https://example.com")
            await t.click("#btn")

        assert all(s.success for s in t.steps)


# ---------------------------------------------------------------------------
# Security: fill/type must NOT log values
# ---------------------------------------------------------------------------


class TestFillSecurity:
    async def test_fill_does_not_log_value(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.fill("#password", "super-secret-123")

        step = t.steps[0]
        assert "super-secret-123" not in step.description
        assert "#password" in step.description

    async def test_type_does_not_log_text(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.type("#secret", "my-password")

        step = t.steps[0]
        assert "my-password" not in step.description
        assert "#secret" in step.description


# ---------------------------------------------------------------------------
# Passthrough for untracked methods
# ---------------------------------------------------------------------------


class TestPassthrough:
    async def test_untracked_methods_passthrough(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            title = await t.title()

        assert title == "Example Page"
        assert len(t.steps) == 0

    async def test_passthrough_property_access(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            assert t.url == "https://example.com"


# ---------------------------------------------------------------------------
# goto() retry on transient errors
# ---------------------------------------------------------------------------


class TestGotoRetry:
    @pytest.fixture(autouse=True)
    def _fast_sleep(self, monkeypatch):
        """Make asyncio.sleep instant for retry tests."""
        async def instant_sleep(seconds):
            pass
        monkeypatch.setattr(asyncio, "sleep", instant_sleep)

    async def test_goto_retries_transient_errors(self, tmp_path):
        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection reset")
            return None

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=3,
        )
        async with track(page, config=config) as t:
            await t.goto("https://example.com")

        assert call_count == 3
        assert len(t.steps) == 1
        assert t.steps[0].success is True

    async def test_goto_raises_permanent_errors(self, tmp_path):
        async def failing_goto(url, **kw):
            raise ValueError("invalid url format")

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))

        with pytest.raises(ValueError, match="invalid url format"):
            async with track(page, config=config) as t:
                await t.goto("https://example.com")

        assert len(t.steps) == 1
        assert t.steps[0].success is False
        assert "invalid url format" in t.steps[0].error

    async def test_goto_no_retry_when_disabled(self, tmp_path):
        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection reset")

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            retry_navigations=False,
        )

        with pytest.raises(ConnectionError):
            async with track(page, config=config) as t:
                await t.goto("https://example.com")

        assert call_count == 1

    async def test_goto_exhausts_retries(self, tmp_path):
        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection reset")

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=2,
        )

        with pytest.raises(ConnectionError):
            async with track(page, config=config) as t:
                await t.goto("https://example.com")

        # 1 initial + 2 retries = 3 attempts
        assert call_count == 3
        assert len(t.steps) == 1
        assert t.steps[0].success is False


# ---------------------------------------------------------------------------
# Error recording
# ---------------------------------------------------------------------------


class TestErrorRecording:
    async def test_failed_step_records_error(self, tmp_path):
        async def failing_click(selector, **kw):
            raise RuntimeError("element not found")

        page = MockPage()
        page.click = failing_click
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.click("#missing")

        assert len(t.steps) == 1
        assert t.steps[0].success is False
        assert "element not found" in t.steps[0].error


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------


class TestScreenshots:
    async def test_screenshots_captured(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        assert t.steps[0].screenshot_bytes == b"fake-jpeg-bytes"

    async def test_screenshots_disabled(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            capture_screenshots=False,
        )
        async with track(page, config=config) as t:
            await t.click("#btn")

        assert t.steps[0].screenshot_bytes is None

    async def test_screenshot_failure_does_not_crash(self, tmp_path):
        async def failing_screenshot(**kw):
            raise RuntimeError("page closed")

        page = MockPage()
        page.screenshot = failing_screenshot
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        assert t.steps[0].screenshot_bytes is None
        assert t.steps[0].success is True

    async def test_screenshots_saved_to_disk(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="test-run",
        )
        async with track(page, config=config) as t:
            await t.click("#btn")
            await t.click("#btn2")

        screenshot_dir = tmp_path / ".observius" / "screenshots" / "test-run"
        assert (screenshot_dir / "step_001.jpg").exists()
        assert (screenshot_dir / "step_002.jpg").exists()
        assert (screenshot_dir / "step_001.jpg").read_bytes() == b"fake-jpeg-bytes"


# ---------------------------------------------------------------------------
# Output saving
# ---------------------------------------------------------------------------


class TestOutputSaving:
    async def test_run_metadata_saved(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="test-run",
        )
        async with track(page, config=config) as t:
            await t.click("#btn")

        metadata_path = tmp_path / ".observius" / "runs" / "test-run.json"
        assert metadata_path.exists()
        metadata = json.loads(metadata_path.read_text())
        assert metadata["task_id"] == "test-run"
        assert metadata["steps_count"] == 1
        assert metadata["success"] is True

    async def test_run_metadata_records_failure(self, tmp_path):
        async def failing_click(selector, **kw):
            raise RuntimeError("boom")

        page = MockPage()
        page.click = failing_click
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="fail-run",
        )

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.click("#btn")

        metadata_path = tmp_path / ".observius" / "runs" / "fail-run.json"
        metadata = json.loads(metadata_path.read_text())
        assert metadata["success"] is False

    async def test_save_replay_creates_html(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="test-run",
        )
        async with track(page, config=config) as t:
            await t.click("#btn")

        replay_path = t.save_replay(str(tmp_path / "replay.html"))
        assert Path(replay_path).exists()
        content = Path(replay_path).read_text()
        assert "<html" in content.lower()

    async def test_save_replay_default_path(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="test-run",
        )
        async with track(page, config=config) as t:
            await t.click("#btn")

        replay_path = t.save_replay()
        assert Path(replay_path).exists()
        assert "test-run" in replay_path

    async def test_outputs_saved_even_on_exception(self, tmp_path):
        async def failing_click(selector, **kw):
            raise RuntimeError("crash")

        page = MockPage()
        page.click = failing_click
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="crash-run",
        )

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.click("#btn")

        # Metadata and screenshots should still be saved
        assert (tmp_path / ".observius" / "runs" / "crash-run.json").exists()


# ---------------------------------------------------------------------------
# TrackConfig kwargs forwarding
# ---------------------------------------------------------------------------


class TestTrackConfig:
    async def test_kwargs_forwarded_to_config(self, tmp_path):
        page = MockPage()
        async with track(
            page,
            capture_screenshots=False,
            output_dir=str(tmp_path / ".observius"),
            task_id="custom-id",
        ) as t:
            await t.click("#btn")

        assert t.steps[0].screenshot_bytes is None
        assert t._run_id == "custom-id"

    async def test_default_config(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            pass

        assert t._config.capture_screenshots is True
        assert t._config.retry_navigations is True
        assert t._config.max_navigation_retries == 3


# ---------------------------------------------------------------------------
# Return value forwarding
# ---------------------------------------------------------------------------


class TestReturnValues:
    async def test_goto_forwards_return_value(self, tmp_path):
        sentinel = object()

        async def goto_with_response(url, **kw):
            return sentinel

        page = MockPage()
        page.goto = goto_with_response
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            result = await t.goto("https://example.com")

        assert result is sentinel

    async def test_click_forwards_return_value(self, tmp_path):
        async def click_returning(selector, **kw):
            return "clicked"

        page = MockPage()
        page.click = click_returning
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            result = await t.click("#btn")

        assert result == "clicked"

    async def test_select_option_forwards_return_value(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            result = await t.select_option("#dropdown")

        assert result == ["option1"]

    async def test_wait_for_selector_forwards_return_value(self, tmp_path):
        sentinel = object()

        async def wait_returning(selector, **kw):
            return sentinel

        page = MockPage()
        page.wait_for_selector = wait_returning
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            result = await t.wait_for_selector("#el")

        assert result is sentinel


# ---------------------------------------------------------------------------
# Kwargs forwarding to underlying page methods
# ---------------------------------------------------------------------------


class TestKwargsForwarding:
    async def test_goto_passes_kwargs(self, tmp_path):
        received_kwargs = {}

        async def capturing_goto(url, **kw):
            received_kwargs.update(kw)
            return None

        page = MockPage()
        page.goto = capturing_goto
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.goto("https://example.com", wait_until="networkidle", timeout=5000)

        assert received_kwargs["wait_until"] == "networkidle"
        assert received_kwargs["timeout"] == 5000

    async def test_click_passes_kwargs(self, tmp_path):
        received_kwargs = {}

        async def capturing_click(selector, **kw):
            received_kwargs.update(kw)

        page = MockPage()
        page.click = capturing_click
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn", force=True, position={"x": 10, "y": 20})

        assert received_kwargs["force"] is True
        assert received_kwargs["position"] == {"x": 10, "y": 20}

    async def test_fill_passes_value_and_kwargs(self, tmp_path):
        received = {}

        async def capturing_fill(selector, value, **kw):
            received["selector"] = selector
            received["value"] = value
            received.update(kw)

        page = MockPage()
        page.fill = capturing_fill
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.fill("#input", "hello", force=True)

        assert received["selector"] == "#input"
        assert received["value"] == "hello"
        assert received["force"] is True


# ---------------------------------------------------------------------------
# Steps list defensive copy
# ---------------------------------------------------------------------------


class TestStepsDefensiveCopy:
    async def test_steps_returns_copy(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        steps_a = t.steps
        steps_b = t.steps
        assert steps_a is not steps_b
        assert steps_a == steps_b

    async def test_mutating_returned_steps_does_not_affect_internal(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#a")
            await t.click("#b")

        steps = t.steps
        steps.clear()
        assert len(t.steps) == 2  # internal list unaffected


# ---------------------------------------------------------------------------
# generate_replay() (in-memory HTML)
# ---------------------------------------------------------------------------


class TestGenerateReplay:
    async def test_returns_html_string(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        html = t.generate_replay()
        assert isinstance(html, str)
        assert "<html" in html.lower()

    async def test_contains_replay_data(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        html = t.generate_replay()
        assert "replayData" in html
        # Should NOT contain the placeholder anymore
        assert '"__REPLAY_DATA__"' not in html

    async def test_replay_with_no_steps(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            pass

        html = t.generate_replay()
        assert "<html" in html.lower()


# ---------------------------------------------------------------------------
# No-steps edge case
# ---------------------------------------------------------------------------


class TestNoSteps:
    async def test_metadata_saved_with_zero_steps(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="empty-run",
        )
        async with track(page, config=config) as t:
            pass

        metadata_path = tmp_path / ".observius" / "runs" / "empty-run.json"
        assert metadata_path.exists()
        metadata = json.loads(metadata_path.read_text())
        assert metadata["steps_count"] == 0
        assert metadata["steps"] == []
        assert metadata["success"] is True

    async def test_no_screenshot_dir_created_without_steps(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="empty-run",
        )
        async with track(page, config=config) as t:
            pass

        screenshot_dir = tmp_path / ".observius" / "screenshots" / "empty-run"
        assert not screenshot_dir.exists()

    async def test_save_replay_with_no_steps(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="empty-run",
        )
        async with track(page, config=config) as t:
            pass

        path = t.save_replay(str(tmp_path / "empty.html"))
        assert Path(path).exists()


# ---------------------------------------------------------------------------
# goto() retry edge cases
# ---------------------------------------------------------------------------


class TestGotoRetryEdgeCases:
    @pytest.fixture(autouse=True)
    def _fast_sleep(self, monkeypatch):
        async def instant_sleep(seconds):
            pass
        monkeypatch.setattr(asyncio, "sleep", instant_sleep)

    async def test_max_navigation_retries_zero(self, tmp_path):
        """max_navigation_retries=0 means exactly one attempt, no retries."""
        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection reset")

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=0,
        )

        with pytest.raises(ConnectionError):
            async with track(page, config=config) as t:
                await t.goto("https://example.com")

        assert call_count == 1

    async def test_timeout_error_is_retriable(self, tmp_path):
        """TimeoutError is classified as transient_network, should retry."""
        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("DNS lookup timed out")
            return None

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=3,
        )
        async with track(page, config=config) as t:
            await t.goto("https://example.com")

        assert call_count == 2
        assert t.steps[0].success is True

    async def test_transient_then_permanent_error(self, tmp_path):
        """First attempt transient (retried), second permanent (raises)."""
        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("connection reset")  # transient
            raise ValueError("bad url")  # permanent

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=3,
        )

        with pytest.raises(ValueError, match="bad url"):
            async with track(page, config=config) as t:
                await t.goto("https://example.com")

        assert call_count == 2
        assert len(t.steps) == 1
        assert t.steps[0].success is False
        assert "bad url" in t.steps[0].error

    async def test_retry_sleep_durations(self, tmp_path):
        """Verify exponential backoff: sleep(1), sleep(2), sleep(4)."""
        sleep_durations = []

        async def recording_sleep(seconds):
            sleep_durations.append(seconds)

        import computeruse.track as track_module
        original_sleep = asyncio.sleep
        asyncio.sleep = recording_sleep

        call_count = 0

        async def failing_goto(url, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("connection reset")
            return None

        page = MockPage()
        page.goto = failing_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=3,
        )
        try:
            async with track(page, config=config) as t:
                await t.goto("https://example.com")
        finally:
            asyncio.sleep = original_sleep

        assert sleep_durations == [1, 2, 4]


# ---------------------------------------------------------------------------
# Multiple errors in sequence
# ---------------------------------------------------------------------------


class TestMultipleErrors:
    async def test_multiple_failed_steps_each_recorded(self, tmp_path):
        fail_on = {"#a", "#b"}

        async def selective_click(selector, **kw):
            if selector in fail_on:
                raise RuntimeError(f"failed on {selector}")

        page = MockPage()
        page.click = selective_click
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))

        with pytest.raises(RuntimeError, match="failed on #a"):
            async with track(page, config=config) as t:
                await t.click("#a")

        assert len(t.steps) == 1
        assert t.steps[0].success is False

    async def test_success_then_failure(self, tmp_path):
        call_count = 0

        async def flaky_click(selector, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("second call fails")

        page = MockPage()
        page.click = flaky_click
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.click("#first")   # succeeds
                await t.click("#second")  # fails

        assert len(t.steps) == 2
        assert t.steps[0].success is True
        assert t.steps[1].success is False

    async def test_interleaved_success_failure_metadata(self, tmp_path):
        """Metadata reports failure if any step failed."""
        call_count = 0

        async def flaky_click(selector, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("boom")

        page = MockPage()
        page.click = flaky_click
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="mixed-run",
        )

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.click("#ok")
                await t.click("#fail")

        metadata = json.loads(
            (tmp_path / ".observius" / "runs" / "mixed-run.json").read_text()
        )
        assert metadata["success"] is False
        assert metadata["steps_count"] == 2
        assert metadata["steps"][0]["success"] is True
        assert metadata["steps"][1]["success"] is False
        assert metadata["steps"][1]["error"] is not None


# ---------------------------------------------------------------------------
# Special characters in selectors and URLs
# ---------------------------------------------------------------------------


class TestSpecialCharacters:
    async def test_selector_with_brackets_and_quotes(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click('div[data-id="foo-bar"]')

        assert 'div[data-id="foo-bar"]' in t.steps[0].description

    async def test_url_with_query_params(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.goto("https://example.com/search?q=hello+world&page=2")

        assert "q=hello+world" in t.steps[0].description

    async def test_unicode_selector(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#\u65e5\u672c\u8a9e")  # Japanese characters

        assert "\u65e5\u672c\u8a9e" in t.steps[0].description

    async def test_press_description_includes_key(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.press("#input", "Control+A")

        desc = t.steps[0].description
        assert "#input" in desc
        assert "Control+A" in desc


# ---------------------------------------------------------------------------
# TrackConfig frozen
# ---------------------------------------------------------------------------


class TestTrackConfigFrozen:
    def test_config_is_immutable(self):
        config = TrackConfig()
        with pytest.raises(AttributeError):
            config.capture_screenshots = False  # type: ignore[misc]

    def test_config_is_hashable(self):
        config = TrackConfig()
        # frozen dataclasses are hashable
        assert hash(config) is not None


# ---------------------------------------------------------------------------
# Run ID auto-generation
# ---------------------------------------------------------------------------


class TestRunIdGeneration:
    async def test_auto_generated_run_id_format(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            pass

        # Should be a 12-char hex string
        assert len(t._run_id) == 12
        int(t._run_id, 16)  # should not raise

    async def test_custom_task_id_used_as_run_id(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="my-custom-id",
        )
        async with track(page, config=config) as t:
            pass

        assert t._run_id == "my-custom-id"

    async def test_two_tracks_get_different_run_ids(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))

        async with track(page, config=config) as t1:
            pass
        async with track(page, config=config) as t2:
            pass

        assert t1._run_id != t2._run_id


# ---------------------------------------------------------------------------
# Output directory creation
# ---------------------------------------------------------------------------


class TestOutputDirCreation:
    async def test_deeply_nested_output_dir_created(self, tmp_path):
        deep_dir = str(tmp_path / "a" / "b" / "c" / ".observius")
        page = MockPage()
        config = TrackConfig(output_dir=deep_dir, task_id="nested-run")
        async with track(page, config=config) as t:
            await t.click("#btn")

        assert (Path(deep_dir) / "runs" / "nested-run.json").exists()
        assert (Path(deep_dir) / "screenshots" / "nested-run" / "step_001.jpg").exists()


# ---------------------------------------------------------------------------
# Metadata JSON structure validation
# ---------------------------------------------------------------------------


class TestMetadataStructure:
    async def test_metadata_has_all_expected_fields(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="struct-run",
        )
        async with track(page, config=config) as t:
            await t.click("#btn")

        metadata = json.loads(
            (tmp_path / ".observius" / "runs" / "struct-run.json").read_text()
        )
        assert "task_id" in metadata
        assert "generated_at" in metadata
        assert "duration_ms" in metadata
        assert "success" in metadata
        assert "steps_count" in metadata
        assert "steps" in metadata
        assert isinstance(metadata["duration_ms"], int)
        assert metadata["duration_ms"] >= 0

    async def test_metadata_step_entry_structure(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="step-struct",
        )
        async with track(page, config=config) as t:
            await t.click("#btn")

        metadata = json.loads(
            (tmp_path / ".observius" / "runs" / "step-struct.json").read_text()
        )
        step = metadata["steps"][0]
        assert step["step_number"] == 1
        assert step["action_type"] == "click"
        assert "click(#btn)" == step["description"]
        assert step["success"] is True
        assert step["duration_ms"] >= 0
        assert step["error"] is None

    async def test_metadata_step_error_populated_on_failure(self, tmp_path):
        async def bad_click(selector, **kw):
            raise RuntimeError("oops")

        page = MockPage()
        page.click = bad_click
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="err-struct",
        )

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.click("#x")

        metadata = json.loads(
            (tmp_path / ".observius" / "runs" / "err-struct.json").read_text()
        )
        step = metadata["steps"][0]
        assert step["success"] is False
        assert "oops" in step["error"]


# ---------------------------------------------------------------------------
# Timing accuracy (slow mock)
# ---------------------------------------------------------------------------


class TestTimingAccuracy:
    async def test_duration_reflects_actual_time(self, tmp_path):
        async def slow_click(selector, **kw):
            await asyncio.sleep(0.05)  # 50ms

        page = MockPage()
        page.click = slow_click
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.click("#btn")

        # Should be at least 40ms (allowing some tolerance)
        assert t.steps[0].duration_ms >= 40


# ---------------------------------------------------------------------------
# Screenshot on failed step
# ---------------------------------------------------------------------------


class TestScreenshotOnFailure:
    async def test_screenshot_captured_on_step_failure(self, tmp_path):
        async def bad_fill(selector, value, **kw):
            raise RuntimeError("fill failed")

        page = MockPage()
        page.fill = bad_fill
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))

        with pytest.raises(RuntimeError):
            async with track(page, config=config) as t:
                await t.fill("#input", "text")

        # Screenshot should still be captured even though the step failed
        assert t.steps[0].screenshot_bytes == b"fake-jpeg-bytes"
        assert t.steps[0].success is False

    async def test_screenshot_on_goto_final_failure(self, tmp_path, monkeypatch):
        async def instant_sleep(seconds):
            pass
        monkeypatch.setattr(asyncio, "sleep", instant_sleep)

        async def always_fail_goto(url, **kw):
            raise ConnectionError("down")

        page = MockPage()
        page.goto = always_fail_goto
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            max_navigation_retries=1,
        )

        with pytest.raises(ConnectionError):
            async with track(page, config=config) as t:
                await t.goto("https://example.com")

        assert t.steps[0].screenshot_bytes == b"fake-jpeg-bytes"


# ---------------------------------------------------------------------------
# Security: fill/type values not in metadata/replay
# ---------------------------------------------------------------------------


class TestValueSecurityInOutputs:
    async def test_fill_value_not_in_metadata_json(self, tmp_path):
        page = MockPage()
        config = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="sec-run",
        )
        async with track(page, config=config) as t:
            await t.fill("#pw", "hunter2")

        raw = (tmp_path / ".observius" / "runs" / "sec-run.json").read_text()
        assert "hunter2" not in raw

    async def test_type_value_not_in_replay_html(self, tmp_path):
        page = MockPage()
        config = TrackConfig(output_dir=str(tmp_path / ".observius"))
        async with track(page, config=config) as t:
            await t.type("#secret", "p@ssw0rd!")

        html = t.generate_replay()
        assert "p@ssw0rd!" not in html


# ---------------------------------------------------------------------------
# Independent sequential track() calls
# ---------------------------------------------------------------------------


class TestSequentialTracks:
    async def test_two_tracks_are_independent(self, tmp_path):
        page = MockPage()

        config1 = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="run-1",
        )
        async with track(page, config=config1) as t1:
            await t1.click("#a")

        config2 = TrackConfig(
            output_dir=str(tmp_path / ".observius"),
            task_id="run-2",
        )
        async with track(page, config=config2) as t2:
            await t2.click("#b")
            await t2.click("#c")

        assert len(t1.steps) == 1
        assert len(t2.steps) == 2
        assert t1.steps[0].step_number == 1
        assert t2.steps[0].step_number == 1  # resets per track

        # Both have separate metadata files
        assert (tmp_path / ".observius" / "runs" / "run-1.json").exists()
        assert (tmp_path / ".observius" / "runs" / "run-2.json").exists()


# ---------------------------------------------------------------------------
# API reporting
# ---------------------------------------------------------------------------


class TestApiReporting:
    """Tests for optional API reporting in track()."""

    async def test_reports_on_success(self, tmp_path):
        page = MockPage()
        with patch(
            "computeruse._reporting.report_to_api", return_value=True
        ) as mock_report:
            config = TrackConfig(
                output_dir=str(tmp_path / ".observius"),
                api_url="http://localhost:3000",
                api_key="test-key",
                task_id="report-ok",
            )
            async with track(page, config=config) as t:
                await t.click("#btn")

        mock_report.assert_awaited_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["api_url"] == "http://localhost:3000"
        assert call_kwargs["api_key"] == "test-key"
        assert call_kwargs["task_id"] == "report-ok"

    async def test_reports_failure_status_on_failed_step(self, tmp_path):
        async def bad_click(selector, **kw):
            raise RuntimeError("boom")

        page = MockPage()
        page.click = bad_click
        with patch(
            "computeruse._reporting.report_to_api", return_value=True
        ) as mock_report:
            config = TrackConfig(
                output_dir=str(tmp_path / ".observius"),
                api_url="http://localhost:3000",
                api_key="test-key",
            )
            with pytest.raises(RuntimeError):
                async with track(page, config=config) as t:
                    await t.click("#missing")

        mock_report.assert_awaited_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["status"] == "failed"

    async def test_no_report_without_config(self, tmp_path):
        page = MockPage()
        with patch(
            "computeruse._reporting.report_to_api"
        ) as mock_report:
            config = TrackConfig(
                output_dir=str(tmp_path / ".observius"),
            )
            async with track(page, config=config) as t:
                await t.click("#btn")

        mock_report.assert_not_called()

    async def test_continues_if_reporting_fails(self, tmp_path):
        page = MockPage()
        with patch(
            "computeruse._reporting.report_to_api", return_value=False
        ):
            config = TrackConfig(
                output_dir=str(tmp_path / ".observius"),
                api_url="http://localhost:3000",
                api_key="test-key",
                task_id="report-false",
            )
            async with track(page, config=config) as t:
                await t.click("#btn")

        # Steps and metadata should still be saved
        assert len(t.steps) == 1
        assert (tmp_path / ".observius" / "runs" / "report-false.json").exists()
