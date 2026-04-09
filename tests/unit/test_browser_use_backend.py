"""
tests/unit/test_browser_use_backend.py — Tests for BrowserUseBackend implementation.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.backends._browser_use import BrowserUseBackend, _safe_call, _safe_call_scalar
from workers.backends.protocol import BackendCapabilities
from workers.shared_types import Observation, StepIntent, StepResult


# ---------------------------------------------------------------------------
# Helpers: mock browser-use objects
# ---------------------------------------------------------------------------


def _mock_agent_step(
    *,
    result: Optional[list] = None,
    model_output: Optional[Any] = None,
    metadata: Optional[Any] = None,
    state: Optional[Any] = None,
) -> MagicMock:
    step = MagicMock()
    step.result = result
    step.model_output = model_output
    step.metadata = metadata
    step.state = state
    return step


def _mock_metadata(*, input_tokens: int = 100, output_tokens: int = 50, step_duration: float = 1.5) -> MagicMock:
    meta = MagicMock()
    meta.input_tokens = input_tokens
    meta.output_tokens = output_tokens
    meta.step_duration = step_duration
    return meta


def _mock_model_output(*, next_goal: str = "", evaluation_previous_goal: str = "") -> MagicMock:
    mo = MagicMock()
    mo.next_goal = next_goal
    mo.evaluation_previous_goal = evaluation_previous_goal
    return mo


def _mock_state(*, url: str = "") -> MagicMock:
    s = MagicMock()
    s.url = url
    return s


def _mock_action_result(*, error: Optional[str] = None) -> MagicMock:
    r = MagicMock()
    r.error = error
    return r


def _mock_history(
    *,
    history: Optional[list] = None,
    screenshots: Optional[list] = None,
    action_names: Optional[list] = None,
    errors: Optional[list] = None,
    model_actions: Optional[list] = None,
    model_thoughts: Optional[list] = None,
    urls: Optional[list] = None,
    final_result: Any = None,
    is_done: Any = None,
    is_successful: Any = None,
    total_cost: Any = None,
) -> MagicMock:
    """Build a mock AgentHistoryList."""
    h = MagicMock()
    h.history = history or []
    h.screenshots = MagicMock(return_value=screenshots or [])
    h.action_names = MagicMock(return_value=action_names or [])
    h.errors = MagicMock(return_value=errors or [])
    h.model_actions = MagicMock(return_value=model_actions or [])
    h.model_thoughts = MagicMock(return_value=model_thoughts or [])
    h.urls = MagicMock(return_value=urls or [])
    h.final_result = MagicMock(return_value=final_result)
    h.is_done = MagicMock(return_value=is_done)
    h.is_successful = MagicMock(return_value=is_successful)
    h.total_cost = MagicMock(return_value=total_cost)
    return h


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_capabilities_class_attribute(self):
        backend = BrowserUseBackend()
        assert isinstance(backend.capabilities, BackendCapabilities)

    def test_delegation_only(self):
        caps = BrowserUseBackend.capabilities
        assert caps.supports_single_step is False
        assert caps.supports_goal_delegation is True
        assert caps.supports_screenshots is True

    def test_no_har_trace_video(self):
        caps = BrowserUseBackend.capabilities
        assert caps.supports_har is False
        assert caps.supports_trace is False
        assert caps.supports_video is False
        assert caps.supports_ax_tree is False


# ---------------------------------------------------------------------------
# Name and protocol
# ---------------------------------------------------------------------------


class TestNameAndProtocol:
    def test_name(self):
        assert BrowserUseBackend().name == "browser_use"

    def test_execute_step_raises(self):
        backend = BrowserUseBackend()
        with pytest.raises(NotImplementedError, match="delegation mode only"):
            asyncio.get_event_loop().run_until_complete(
                backend.execute_step(StepIntent())
            )


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    def test_stores_config(self):
        backend = BrowserUseBackend()
        asyncio.get_event_loop().run_until_complete(
            backend.initialize({"model": "claude-haiku-4-5-20251001", "headless": False})
        )
        assert backend._config["model"] == "claude-haiku-4-5-20251001"
        assert backend._model == "claude-haiku-4-5-20251001"

    def test_default_model(self):
        backend = BrowserUseBackend()
        asyncio.get_event_loop().run_until_complete(backend.initialize({}))
        assert backend._model == "claude-sonnet-4-6"

    def test_creates_llm(self):
        backend = BrowserUseBackend()
        asyncio.get_event_loop().run_until_complete(backend.initialize({}))
        assert backend._llm is not None

    def test_creates_browser_session(self):
        backend = BrowserUseBackend()
        asyncio.get_event_loop().run_until_complete(backend.initialize({}))
        assert backend._browser_session is not None


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_teardown_without_initialize(self):
        """Teardown on fresh instance should not raise."""
        backend = BrowserUseBackend()
        asyncio.get_event_loop().run_until_complete(backend.teardown())
        assert backend._browser_session is None
        assert backend._llm is None

    def test_teardown_clears_state(self):
        backend = BrowserUseBackend()
        asyncio.get_event_loop().run_until_complete(backend.initialize({}))
        assert backend._llm is not None
        asyncio.get_event_loop().run_until_complete(backend.teardown())
        assert backend._llm is None
        assert backend._browser_session is None
        assert backend._last_history is None

    def test_teardown_calls_close(self):
        backend = BrowserUseBackend()
        mock_session = MagicMock()
        mock_session.close = MagicMock(return_value=None)
        backend._browser_session = mock_session
        asyncio.get_event_loop().run_until_complete(backend.teardown())
        mock_session.close.assert_called_once()

    def test_teardown_handles_async_close(self):
        backend = BrowserUseBackend()
        mock_session = MagicMock()
        mock_session.close = AsyncMock(return_value=None)
        backend._browser_session = mock_session
        asyncio.get_event_loop().run_until_complete(backend.teardown())
        mock_session.close.assert_awaited_once()

    def test_teardown_handles_close_exception(self):
        backend = BrowserUseBackend()
        mock_session = MagicMock()
        mock_session.close = MagicMock(side_effect=RuntimeError("boom"))
        backend._browser_session = mock_session
        # Should not raise
        asyncio.get_event_loop().run_until_complete(backend.teardown())
        assert backend._browser_session is None


# ---------------------------------------------------------------------------
# execute_goal: requires initialized backend
# ---------------------------------------------------------------------------


class TestExecuteGoalGuard:
    def test_raises_without_initialize(self):
        backend = BrowserUseBackend()
        with pytest.raises(RuntimeError, match="not initialized"):
            asyncio.get_event_loop().run_until_complete(
                backend.execute_goal("test goal")
            )


# ---------------------------------------------------------------------------
# _convert_history
# ---------------------------------------------------------------------------


class TestConvertHistory:
    def test_empty_history(self):
        backend = BrowserUseBackend()
        history = _mock_history()
        steps = backend._convert_history(history)
        assert steps == []

    def test_single_step(self):
        step = _mock_agent_step(
            metadata=_mock_metadata(input_tokens=200, output_tokens=80, step_duration=2.0),
            state=_mock_state(url="https://example.com/page"),
        )
        history = _mock_history(
            history=[step],
            screenshots=["iVBOR..."],
            action_names=["ClickElementAction"],
            urls=["https://example.com/page"],
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert len(results) == 1
        r = results[0]
        assert r.success is True
        assert r.error is None
        assert r.tokens_in == 200
        assert r.tokens_out == 80
        assert r.duration_ms == 2000
        assert r.observation is not None
        assert r.observation.url == "https://example.com/page"
        assert r.observation.screenshot_b64 == "iVBOR..."
        assert any("action:ClickElementAction" in s for s in r.side_effects)

    def test_step_with_error(self):
        action_result = _mock_action_result(error="Element not found")
        step = _mock_agent_step(result=[action_result])
        history = _mock_history(history=[step])
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "Element not found"

    def test_step_with_multiple_errors(self):
        r1 = _mock_action_result(error="Error A")
        r2 = _mock_action_result(error="Error B")
        step = _mock_agent_step(result=[r1, r2])
        history = _mock_history(history=[step])
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert results[0].error == "Error A; Error B"

    def test_errors_list_fallback(self):
        """When step.result has no errors, fall back to history.errors()."""
        step = _mock_agent_step()
        history = _mock_history(
            history=[step],
            errors=["Timeout exceeded"],
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert results[0].success is False
        assert results[0].error == "Timeout exceeded"

    def test_screenshot_bytes_converted(self):
        """Bytes screenshots are base64-encoded."""
        step = _mock_agent_step()
        history = _mock_history(
            history=[step],
            screenshots=[b"\x89PNG\r\n"],
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert results[0].observation.screenshot_b64 is not None
        assert isinstance(results[0].observation.screenshot_b64, str)

    def test_screenshot_string_kept(self):
        """String screenshots are kept as-is."""
        step = _mock_agent_step()
        history = _mock_history(
            history=[step],
            screenshots=["base64data"],
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert results[0].observation.screenshot_b64 == "base64data"

    def test_model_output_in_side_effects(self):
        mo = _mock_model_output(
            next_goal="Click the submit button",
            evaluation_previous_goal="Page loaded successfully",
        )
        step = _mock_agent_step(model_output=mo)
        history = _mock_history(history=[step])
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        se = results[0].side_effects
        assert any("goal:Click the submit button" in s for s in se)
        assert any("eval:Page loaded successfully" in s for s in se)

    def test_model_thoughts_in_side_effects(self):
        step = _mock_agent_step()
        history = _mock_history(
            history=[step],
            model_thoughts=["I should click the login button"],
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert any("thought:" in s for s in results[0].side_effects)

    def test_model_actions_in_side_effects(self):
        step = _mock_agent_step()
        history = _mock_history(
            history=[step],
            model_actions=["ClickElementAction(selector='#btn')"],
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert any("raw_action:" in s for s in results[0].side_effects)

    def test_final_result_on_last_step(self):
        s1 = _mock_agent_step()
        s2 = _mock_agent_step()
        history = _mock_history(
            history=[s1, s2],
            final_result={"title": "Example"},
            is_done=True,
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        # Only last step gets final_result
        assert not any("final_result:" in s for s in results[0].side_effects)
        assert any("final_result:" in s for s in results[1].side_effects)
        assert any("is_done:True" in s for s in results[1].side_effects)

    def test_timestamp_fallback_from_callbacks(self):
        step = _mock_agent_step(metadata=_mock_metadata(step_duration=None))
        # Manually set metadata with no step_duration
        step.metadata.step_duration = None
        history = _mock_history(history=[step])

        backend = BrowserUseBackend()
        now = time.monotonic()
        backend._step_timestamps = [now - 2.0]  # 2 seconds ago

        results = backend._convert_history(history)
        # Should use callback timestamps
        assert results[0].duration_ms > 0

    def test_multiple_steps(self):
        steps = [
            _mock_agent_step(
                metadata=_mock_metadata(input_tokens=100, output_tokens=50),
                state=_mock_state(url=f"https://example.com/step{i}"),
            )
            for i in range(5)
        ]
        history = _mock_history(
            history=steps,
            screenshots=["s1", "s2", "s3", "s4", "s5"],
            action_names=["GoToUrlAction", "ClickElementAction", "InputTextAction", "ScrollAction", "DoneAction"],
            urls=["https://example.com/step0"] * 5,
        )
        backend = BrowserUseBackend()
        results = backend._convert_history(history)

        assert len(results) == 5
        assert all(r.tokens_in == 100 for r in results)
        assert all(r.success is True for r in results)

    def test_graceful_on_broken_history(self):
        """_convert_history should not raise even on weird inputs."""
        backend = BrowserUseBackend()
        # Pass something that has no history attr
        results = backend._convert_history(None)
        assert results == []

        # Pass something whose .history raises
        bad = MagicMock()
        bad.history = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        results = backend._convert_history(bad)
        # Might return [] or partial, but should NOT raise
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# _on_agent_step callback
# ---------------------------------------------------------------------------


class TestOnAgentStep:
    def test_records_timestamp(self):
        backend = BrowserUseBackend()
        assert len(backend._step_timestamps) == 0
        backend._on_agent_step("some step info")
        assert len(backend._step_timestamps) == 1
        backend._on_agent_step("another step")
        assert len(backend._step_timestamps) == 2

    def test_timestamps_are_monotonic(self):
        backend = BrowserUseBackend()
        backend._on_agent_step()
        backend._on_agent_step()
        assert backend._step_timestamps[1] >= backend._step_timestamps[0]


# ---------------------------------------------------------------------------
# _on_step_end callback
# ---------------------------------------------------------------------------


class TestOnStepEnd:
    def test_calls_stuck_detector(self):
        backend = BrowserUseBackend()
        mock_signal = MagicMock()
        mock_signal.detected = False
        backend._stuck_detector.check_agent_step = MagicMock(return_value=mock_signal)

        agent = MagicMock()
        agent.history = [MagicMock()]

        asyncio.get_event_loop().run_until_complete(backend._on_step_end(agent))
        backend._stuck_detector.check_agent_step.assert_called_once()

    def test_stops_agent_when_stuck(self):
        backend = BrowserUseBackend()
        mock_signal = MagicMock()
        mock_signal.detected = True
        mock_signal.reason = "action_repetition"
        mock_signal.step_number = 5
        mock_signal.details = "repeated click 3 times"
        backend._stuck_detector.check_agent_step = MagicMock(return_value=mock_signal)

        agent = MagicMock()
        agent.history = [MagicMock()]
        agent.stop = MagicMock()

        asyncio.get_event_loop().run_until_complete(backend._on_step_end(agent))
        agent.stop.assert_called_once()

    def test_handles_no_history(self):
        backend = BrowserUseBackend()
        agent = MagicMock()
        agent.history = None
        # Should not raise
        asyncio.get_event_loop().run_until_complete(backend._on_step_end(agent))

    def test_handles_empty_history(self):
        backend = BrowserUseBackend()
        agent = MagicMock()
        agent.history = []
        asyncio.get_event_loop().run_until_complete(backend._on_step_end(agent))

    def test_handles_exception(self):
        backend = BrowserUseBackend()
        backend._stuck_detector.check_agent_step = MagicMock(
            side_effect=RuntimeError("detector crashed")
        )
        agent = MagicMock()
        agent.history = [MagicMock()]
        # Should not raise
        asyncio.get_event_loop().run_until_complete(backend._on_step_end(agent))


# ---------------------------------------------------------------------------
# get_observation
# ---------------------------------------------------------------------------


class TestGetObservation:
    def test_returns_empty_without_state(self):
        backend = BrowserUseBackend()
        obs = asyncio.get_event_loop().run_until_complete(backend.get_observation())
        assert isinstance(obs, Observation)
        assert obs.url == ""
        assert obs.has_screenshot is False

    def test_returns_from_history(self):
        backend = BrowserUseBackend()
        backend._last_history = _mock_history(
            screenshots=["abc123"],
            urls=["https://example.com/result"],
        )
        obs = asyncio.get_event_loop().run_until_complete(backend.get_observation())
        assert obs.url == "https://example.com/result"
        assert obs.screenshot_b64 == "abc123"

    def test_returns_from_history_bytes(self):
        backend = BrowserUseBackend()
        backend._last_history = _mock_history(
            screenshots=[b"\x89PNG"],
            urls=["https://example.com"],
        )
        obs = asyncio.get_event_loop().run_until_complete(backend.get_observation())
        assert obs.screenshot_b64 is not None
        assert isinstance(obs.screenshot_b64, str)

    def test_falls_back_to_page(self):
        backend = BrowserUseBackend()
        backend._last_history = None  # No history

        mock_page = MagicMock()
        mock_page.url = "https://example.com/fallback"
        mock_page.title = AsyncMock(return_value="Fallback Title")
        mock_page.screenshot = AsyncMock(return_value=b"\x89PNG")
        mock_page.viewport_size = {"width": 1920, "height": 1080}

        mock_session = MagicMock()
        mock_session.current_page = mock_page
        backend._browser_session = mock_session

        obs = asyncio.get_event_loop().run_until_complete(backend.get_observation())
        assert obs.url == "https://example.com/fallback"
        assert obs.page_title == "Fallback Title"
        assert obs.viewport_width == 1920
        assert obs.has_screenshot is True


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_safe_call_returns_list(self):
        obj = MagicMock()
        obj.screenshots = MagicMock(return_value=["a", "b"])
        assert _safe_call(obj, "screenshots") == ["a", "b"]

    def test_safe_call_missing_method(self):
        obj = MagicMock(spec=[])
        assert _safe_call(obj, "nonexistent") == []

    def test_safe_call_exception(self):
        obj = MagicMock()
        obj.screenshots = MagicMock(side_effect=RuntimeError("boom"))
        assert _safe_call(obj, "screenshots") == []

    def test_safe_call_non_list_result(self):
        obj = MagicMock()
        obj.screenshots = MagicMock(return_value="not a list")
        assert _safe_call(obj, "screenshots") == []

    def test_safe_call_scalar_returns_value(self):
        obj = MagicMock()
        obj.final_result = MagicMock(return_value={"key": "value"})
        assert _safe_call_scalar(obj, "final_result") == {"key": "value"}

    def test_safe_call_scalar_missing(self):
        obj = MagicMock(spec=[])
        assert _safe_call_scalar(obj, "nonexistent") is None

    def test_safe_call_scalar_exception(self):
        obj = MagicMock()
        obj.final_result = MagicMock(side_effect=RuntimeError("boom"))
        assert _safe_call_scalar(obj, "final_result") is None
