"""Tests for computeruse.wrap — reliability wrapper for browser_use Agent."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from computeruse.models import ActionType
from computeruse.wrap import (
    WrappedAgent,
    WrapConfig,
    _ACTION_MAP,
    _extract_step_tokens,
    wrap,
)


# ---------------------------------------------------------------------------
# Mock Agent
# ---------------------------------------------------------------------------


class MockAgent:
    """Minimal mock of a browser_use Agent."""

    def __init__(
        self,
        result: Any = None,
        error: Optional[Exception] = None,
        errors: Optional[list[Exception]] = None,
    ) -> None:
        self._result = result
        self._error = error
        self._errors = list(errors) if errors else []
        self._call_count = 0
        self._stopped = False

    async def run(
        self, max_steps: int = 100, on_step_end: Any = None, **kwargs: Any
    ) -> Any:
        self._call_count += 1
        # If we have a sequence of errors, pop from front
        if self._errors:
            exc = self._errors.pop(0)
            if exc is not None:
                raise exc
        elif self._error:
            raise self._error
        return self._result

    def stop(self) -> None:
        self._stopped = True


class MockAgentNoOnStepEnd:
    """Agent whose run() does NOT accept on_step_end."""

    def __init__(self, result: Any = None) -> None:
        self._result = result

    async def run(self, max_steps: int = 100) -> Any:
        return self._result


def _make_history_step(
    action_name: str = "ClickElementAction",
    screenshot: Optional[bytes] = None,
    error: Optional[str] = None,
    tokens_in: int = 100,
    tokens_out: int = 50,
    duration: float = 0.5,
    next_goal: Optional[str] = None,
) -> SimpleNamespace:
    """Build a fake AgentHistoryStep."""
    result_list = []
    if error:
        result_list.append(SimpleNamespace(error=error))
    else:
        result_list.append(SimpleNamespace(error=None))

    mo = SimpleNamespace(
        action=[SimpleNamespace()],
        next_goal=next_goal or f"Do {action_name}",
        evaluation_previous_goal=None,
    )
    meta = SimpleNamespace(
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        step_duration=duration,
    )
    state = SimpleNamespace(screenshot=screenshot)
    return SimpleNamespace(
        result=result_list,
        model_output=mo,
        metadata=meta,
        state=state,
    )


def _make_result(
    steps: Optional[list[SimpleNamespace]] = None,
    action_names: Optional[list[str]] = None,
    screenshots: Optional[list[Optional[bytes]]] = None,
    total_cost_dollars: Optional[float] = None,
) -> SimpleNamespace:
    """Build a fake AgentHistoryList result."""
    steps = steps or []
    _action_names = action_names or []
    _screenshots = screenshots or []

    def _total_cost() -> Optional[float]:
        return total_cost_dollars

    return SimpleNamespace(
        history=steps,
        action_names=lambda: _action_names,
        screenshots=lambda: _screenshots,
        total_cost=_total_cost,
    )


# ---------------------------------------------------------------------------
# Helpers for Anthropic-like errors
# ---------------------------------------------------------------------------


def _make_transient_error(message: str = "overloaded") -> Exception:
    """An error that classify_error maps to transient_llm (retriable)."""

    class _Exc(Exception):
        __module__ = "anthropic._exceptions"

    _Exc.__name__ = "InternalServerError"
    exc = _Exc(message)
    exc.status_code = 529
    return exc


def _make_permanent_error(message: str = "invalid api key") -> Exception:
    """An error that classify_error maps to permanent_llm (not retriable)."""

    class _Exc(Exception):
        __module__ = "anthropic._exceptions"

    _Exc.__name__ = "AuthenticationError"
    exc = _Exc(message)
    exc.status_code = 401
    return exc


# ---------------------------------------------------------------------------
# Tests: WrapConfig
# ---------------------------------------------------------------------------


class TestWrapConfig:
    def test_defaults(self) -> None:
        cfg = WrapConfig()
        assert cfg.max_retries == 3
        assert cfg.enable_stuck_detection is True
        assert cfg.stuck_screenshot_threshold == 4
        assert cfg.stuck_action_threshold == 5
        assert cfg.stuck_failure_threshold == 3
        assert cfg.track_cost is True
        assert cfg.session_key is None
        assert cfg.save_screenshots is True
        assert cfg.output_dir == ".pokant"
        assert cfg.generate_replay is True
        assert cfg.task_id is None

    def test_frozen(self) -> None:
        cfg = WrapConfig()
        with pytest.raises(AttributeError):
            cfg.max_retries = 5  # type: ignore[misc]

    def test_custom_values(self) -> None:
        cfg = WrapConfig(max_retries=1, output_dir="/tmp/test")
        assert cfg.max_retries == 1
        assert cfg.output_dir == "/tmp/test"


# ---------------------------------------------------------------------------
# Tests: wrap() factory
# ---------------------------------------------------------------------------


class TestWrapFactory:
    def test_returns_wrapped_agent(self) -> None:
        agent = MockAgent()
        wrapped = wrap(agent)
        assert isinstance(wrapped, WrappedAgent)

    def test_rejects_non_runnable(self) -> None:
        with pytest.raises(TypeError, match="callable 'run' method"):
            wrap("not an agent")

    def test_rejects_non_callable_run(self) -> None:
        obj = SimpleNamespace(run="not callable")
        with pytest.raises(TypeError, match="callable 'run' method"):
            wrap(obj)

    def test_accepts_config(self) -> None:
        cfg = WrapConfig(max_retries=1)
        wrapped = wrap(MockAgent(), config=cfg)
        assert wrapped._config.max_retries == 1

    def test_accepts_kwargs(self) -> None:
        wrapped = wrap(MockAgent(), max_retries=2, output_dir="/tmp/x")
        assert wrapped._config.max_retries == 2
        assert wrapped._config.output_dir == "/tmp/x"


# ---------------------------------------------------------------------------
# Tests: run() — basic pass-through
# ---------------------------------------------------------------------------


class TestRunBasic:
    async def test_returns_agent_result(self) -> None:
        expected = _make_result()
        agent = MockAgent(result=expected)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_basic")
        result = await wrapped.run()
        assert result is expected

    async def test_task_id_auto_generated(self) -> None:
        agent = MockAgent(result=_make_result())
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_taskid")
        await wrapped.run()
        assert len(wrapped.task_id) == 36  # UUID

    async def test_task_id_custom(self) -> None:
        agent = MockAgent(result=_make_result())
        wrapped = wrap(
            agent, task_id="my-task", output_dir="/tmp/pokant_test_custom"
        )
        await wrapped.run()
        assert wrapped.task_id == "my-task"


# ---------------------------------------------------------------------------
# Tests: run() — retry behaviour
# ---------------------------------------------------------------------------


class TestRetry:
    async def test_retries_on_transient_error(self) -> None:
        """Transient error on attempt 1, success on attempt 2."""
        result = _make_result()
        agent = MockAgent(errors=[_make_transient_error(), None])
        agent._result = result
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_retry",
        )
        got = await wrapped.run()
        assert got is result
        assert agent._call_count == 2

    async def test_no_retry_on_permanent_error(self) -> None:
        """Permanent error raises immediately without retry."""
        agent = MockAgent(error=_make_permanent_error())
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_noretry",
        )
        with pytest.raises(Exception, match="invalid api key"):
            await wrapped.run()
        assert agent._call_count == 1

    async def test_retries_exhausted(self) -> None:
        """All retries fail with transient errors."""
        errors = [_make_transient_error(f"fail {i}") for i in range(4)]
        agent = MockAgent(errors=errors)
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_exhaust",
        )
        with pytest.raises(Exception, match="fail 3"):
            await wrapped.run()
        assert agent._call_count == 4  # initial + 3 retries


# ---------------------------------------------------------------------------
# Tests: on_step_end feature detection
# ---------------------------------------------------------------------------


class TestOnStepEndDetection:
    async def test_on_step_end_passed_when_supported(self) -> None:
        """When agent.run() accepts on_step_end, it should be passed."""
        received_kwargs: dict[str, Any] = {}

        class CapturingAgent:
            async def run(
                self,
                max_steps: int = 100,
                on_step_end: Any = None,
                **kw: Any,
            ) -> Any:
                received_kwargs["on_step_end"] = on_step_end
                return _make_result()

        wrapped = wrap(
            CapturingAgent(),
            output_dir="/tmp/pokant_test_stepend",
        )
        await wrapped.run()
        assert received_kwargs["on_step_end"] is not None

    async def test_on_step_end_not_passed_when_unsupported(self) -> None:
        """When agent.run() doesn't accept on_step_end, it's not passed."""
        agent = MockAgentNoOnStepEnd(result=_make_result())
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_nostepend")
        # Should not raise TypeError about unexpected keyword
        result = await wrapped.run()
        assert result is not None

    async def test_user_on_step_end_not_overridden(self) -> None:
        """If user passes their own on_step_end, wrap doesn't override it."""
        user_callback = MagicMock()

        class CapturingAgent:
            async def run(
                self,
                max_steps: int = 100,
                on_step_end: Any = None,
                **kw: Any,
            ) -> Any:
                self.received_on_step_end = on_step_end
                return _make_result()

        agent = CapturingAgent()
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_usercb")
        await wrapped.run(on_step_end=user_callback)
        assert agent.received_on_step_end is user_callback


# ---------------------------------------------------------------------------
# Tests: step enrichment
# ---------------------------------------------------------------------------


class TestStepEnrichment:
    async def test_enriches_steps_from_history(self) -> None:
        steps = [
            _make_history_step(
                action_name="ClickElementAction",
                tokens_in=200,
                tokens_out=80,
                duration=1.5,
                next_goal="Click the button",
            ),
            _make_history_step(
                action_name="InputTextAction",
                tokens_in=150,
                tokens_out=60,
                duration=0.8,
                next_goal="Type email",
            ),
        ]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction", "InputTextAction"],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_enrich")
        await wrapped.run()

        assert len(wrapped.steps) == 2
        assert wrapped.steps[0].action_type == "click"
        assert wrapped.steps[1].action_type == "type"
        assert wrapped.steps[0].tokens_in == 200
        assert wrapped.steps[1].tokens_out == 60
        assert wrapped.steps[0].duration_ms == 1500
        assert "Click the button" in wrapped.steps[0].description

    async def test_enriches_errors(self) -> None:
        steps = [_make_history_step(error="Element not found")]
        result = _make_result(
            steps=steps, action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_enrich_err")
        await wrapped.run()

        assert wrapped.steps[0].success is False
        assert "Element not found" in wrapped.steps[0].error


# ---------------------------------------------------------------------------
# Tests: cost calculation
# ---------------------------------------------------------------------------


class TestCostCalculation:
    async def test_cost_from_total_cost(self) -> None:
        result = _make_result(total_cost_dollars=0.05)
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_cost")
        await wrapped.run()
        assert wrapped.cost_cents == pytest.approx(5.0)

    async def test_cost_from_step_tokens(self) -> None:
        """When total_cost() returns None, fall back to per-step tokens."""
        steps = [
            _make_history_step(tokens_in=1000, tokens_out=500),
        ]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
            total_cost_dollars=None,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_cost_tokens")
        await wrapped.run()
        assert wrapped.cost_cents > 0


# ---------------------------------------------------------------------------
# Tests: screenshot saving
# ---------------------------------------------------------------------------


class TestScreenshotSaving:
    async def test_screenshots_saved_to_disk(self, tmp_path: Path) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        steps = [
            _make_history_step(screenshot=png_bytes),
            _make_history_step(screenshot=None),
        ]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction", "WaitAction"],
            screenshots=[png_bytes, None],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="test-ss",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        ss_dir = tmp_path / "screenshots" / "test-ss"
        assert (ss_dir / "step_0.png").exists()
        assert (ss_dir / "step_0.png").read_bytes() == png_bytes
        assert not (ss_dir / "step_1.png").exists()


# ---------------------------------------------------------------------------
# Tests: run metadata
# ---------------------------------------------------------------------------


class TestRunMetadata:
    async def test_metadata_saved_on_success(self, tmp_path: Path) -> None:
        steps = [_make_history_step()]
        result = _make_result(
            steps=steps, action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="test-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        meta_path = tmp_path / "runs" / "test-meta.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["task_id"] == "test-meta"
        assert data["status"] == "completed"
        assert data["step_count"] == 1

    async def test_metadata_saved_on_failure(self, tmp_path: Path) -> None:
        agent = MockAgent(error=_make_permanent_error("bad key"))
        wrapped = wrap(
            agent,
            task_id="test-fail",
            max_retries=0,
            adaptive_retry=False,
            output_dir=str(tmp_path),
        )
        with pytest.raises(Exception):
            await wrapped.run()

        meta_path = tmp_path / "runs" / "test-fail.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["status"] == "failed"
        assert data["error"] is not None


# ---------------------------------------------------------------------------
# Tests: stuck detection (post-execution)
# ---------------------------------------------------------------------------


class TestStuckDetection:
    async def test_post_execution_analysis_runs(self) -> None:
        """Stuck detector analyzes full history after run completes."""
        steps = [_make_history_step() for _ in range(3)]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"] * 3,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            stuck_failure_threshold=2,
            output_dir="/tmp/pokant_test_stuck",
        )
        # Should complete without error (3 steps is below default thresholds)
        await wrapped.run()
        assert len(wrapped.steps) == 3

    async def test_disabled_stuck_detection(self) -> None:
        """When disabled, no stuck detector is created."""
        agent = MockAgent(result=_make_result())
        wrapped = wrap(
            agent,
            enable_stuck_detection=False,
            output_dir="/tmp/pokant_test_nostuck",
        )
        assert wrapped._stuck_detector is None
        await wrapped.run()


# ---------------------------------------------------------------------------
# Tests: replay generation
# ---------------------------------------------------------------------------


class TestReplayGeneration:
    async def test_replay_generated(self, tmp_path: Path) -> None:
        steps = [
            _make_history_step(
                screenshot=b"\x89PNG" + b"\x00" * 50,
            ),
        ]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
            screenshots=[b"\x89PNG" + b"\x00" * 50],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="test-replay",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        assert wrapped.replay_path is not None
        assert Path(wrapped.replay_path).exists()

    async def test_no_replay_when_disabled(self, tmp_path: Path) -> None:
        steps = [_make_history_step()]
        result = _make_result(
            steps=steps, action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            generate_replay=False,
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        assert wrapped.replay_path is None


# ===================================================================
# EDGE CASE TESTS
# ===================================================================


# ---------------------------------------------------------------------------
# Edge: wrap() factory validation
# ---------------------------------------------------------------------------


class TestWrapFactoryEdgeCases:
    def test_rejects_none(self) -> None:
        with pytest.raises(TypeError, match="callable 'run' method"):
            wrap(None)

    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError, match="callable 'run' method"):
            wrap(42)

    def test_rejects_dict(self) -> None:
        with pytest.raises(TypeError, match="callable 'run' method"):
            wrap({"run": "nope"})

    def test_error_message_includes_type_name(self) -> None:
        with pytest.raises(TypeError, match="NoneType"):
            wrap(None)

    def test_config_takes_precedence_over_kwargs(self) -> None:
        """When both config and kwargs are given, config wins."""
        cfg = WrapConfig(max_retries=7)
        wrapped = wrap(MockAgent(), config=cfg, max_retries=1)
        assert wrapped._config.max_retries == 7

    def test_accepts_object_with_bound_method_run(self) -> None:
        """Any object with a callable run attribute works."""

        class CustomRunner:
            async def run(self, **kw: Any) -> str:
                return "done"

        wrapped = wrap(CustomRunner())
        assert isinstance(wrapped, WrappedAgent)

    def test_accepts_object_with_lambda_run(self) -> None:
        obj = SimpleNamespace(run=lambda: None)
        wrapped = wrap(obj)
        assert isinstance(wrapped, WrappedAgent)


# ---------------------------------------------------------------------------
# Edge: WrapConfig
# ---------------------------------------------------------------------------


class TestWrapConfigEdgeCases:
    def test_equality(self) -> None:
        a = WrapConfig(max_retries=2, output_dir="/x")
        b = WrapConfig(max_retries=2, output_dir="/x")
        assert a == b

    def test_inequality(self) -> None:
        a = WrapConfig(max_retries=1)
        b = WrapConfig(max_retries=2)
        assert a != b

    def test_all_fields_overridden(self) -> None:
        cfg = WrapConfig(
            max_retries=0,
            enable_stuck_detection=False,
            stuck_screenshot_threshold=10,
            stuck_action_threshold=10,
            stuck_failure_threshold=10,
            track_cost=False,
            session_key="example.com",
            save_screenshots=False,
            output_dir="/custom",
            generate_replay=False,
            task_id="custom-id",
        )
        assert cfg.max_retries == 0
        assert cfg.enable_stuck_detection is False
        assert cfg.session_key == "example.com"
        assert cfg.task_id == "custom-id"


# ---------------------------------------------------------------------------
# Edge: Properties before and after run()
# ---------------------------------------------------------------------------


class TestPropertiesLifecycle:
    def test_steps_empty_before_run(self) -> None:
        wrapped = wrap(MockAgent())
        assert wrapped.steps == []

    def test_cost_zero_before_run(self) -> None:
        wrapped = wrap(MockAgent())
        assert wrapped.cost_cents == 0.0

    def test_replay_path_none_before_run(self) -> None:
        wrapped = wrap(MockAgent())
        assert wrapped.replay_path is None

    def test_steps_returns_copy(self) -> None:
        """Mutating the returned list doesn't affect internal state."""
        agent = MockAgent(result=_make_result(
            steps=[_make_history_step()],
            action_names=["ClickElementAction"],
        ))
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_copy")

        async def _run() -> None:
            await wrapped.run()

        asyncio.run(_run())

        external = wrapped.steps
        external.clear()
        assert len(wrapped.steps) == 1  # internal list unaffected


# ---------------------------------------------------------------------------
# Edge: Retry behaviour
# ---------------------------------------------------------------------------


class TestRetryEdgeCases:
    async def test_max_retries_zero_no_retry(self) -> None:
        """With max_retries=0, transient errors raise immediately."""
        agent = MockAgent(error=_make_transient_error())
        wrapped = wrap(
            agent, max_retries=0, adaptive_retry=False,
            output_dir="/tmp/pokant_test_zero",
        )
        with pytest.raises(Exception, match="overloaded"):
            await wrapped.run()
        assert agent._call_count == 1

    async def test_steps_reset_between_retries(self) -> None:
        """Steps list is cleared before each retry attempt."""
        step = _make_history_step()
        good_result = _make_result(
            steps=[step], action_names=["ClickElementAction"]
        )
        agent = MockAgent(errors=[_make_transient_error(), None])
        agent._result = good_result
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_reset",
        )
        await wrapped.run()
        # Should only have steps from the successful attempt
        assert len(wrapped.steps) == 1

    async def test_stuck_detector_reset_between_retries(self) -> None:
        """Stuck detector state is cleared before each retry."""
        good_result = _make_result()
        agent = MockAgent(errors=[_make_transient_error(), None])
        agent._result = good_result
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_stuck_reset",
        )
        await wrapped.run()
        # The stuck detector should be in a clean state
        assert wrapped._stuck_detector is not None
        assert wrapped._stuck_detector._step_count == 0

    async def test_error_category_set_on_failure(self, tmp_path: Path) -> None:
        """error_category is populated in metadata when run fails."""
        agent = MockAgent(error=_make_permanent_error("bad"))
        wrapped = wrap(
            agent,
            max_retries=0,
            adaptive_retry=False,
            task_id="cat-test",
            output_dir=str(tmp_path),
        )
        with pytest.raises(Exception):
            await wrapped.run()

        meta = json.loads(
            (tmp_path / "runs" / "cat-test.json").read_text()
        )
        assert meta["error_category"] == "permanent_llm"

    async def test_error_category_none_on_success(
        self, tmp_path: Path
    ) -> None:
        agent = MockAgent(result=_make_result())
        wrapped = wrap(
            agent, task_id="ok-test", output_dir=str(tmp_path)
        )
        await wrapped.run()
        meta = json.loads(
            (tmp_path / "runs" / "ok-test.json").read_text()
        )
        assert meta["error_category"] is None

    async def test_rate_limit_error_retry(self) -> None:
        """Rate-limited errors (429) are retriable."""

        class _RateLimitExc(Exception):
            __module__ = "anthropic._exceptions"

        _RateLimitExc.__name__ = "RateLimitError"
        exc = _RateLimitExc("rate limited")
        exc.status_code = 429

        agent = MockAgent(errors=[exc, None])
        agent._result = _make_result()
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_429",
        )
        result = await wrapped.run()
        assert result is not None
        assert agent._call_count == 2

    async def test_unknown_error_not_retried(self) -> None:
        """A generic ValueError is classified as unknown and not retried
        under the dumb retry policy (adaptive retry retries unknown errors).
        """
        agent = MockAgent(error=ValueError("something broke"))
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_unknown",
        )
        with pytest.raises(ValueError, match="something broke"):
            await wrapped.run()
        assert agent._call_count == 1

    async def test_network_error_retried(self) -> None:
        """ConnectionError (network) is transient and retried."""
        agent = MockAgent(errors=[ConnectionError("refused"), None])
        agent._result = _make_result()
        wrapped = wrap(
            agent, max_retries=3, adaptive_retry=False,
            output_dir="/tmp/pokant_test_conn",
        )
        result = await wrapped.run()
        assert result is not None
        assert agent._call_count == 2


# ---------------------------------------------------------------------------
# Edge: on_step_end callback
# ---------------------------------------------------------------------------


class TestOnStepEndEdgeCases:
    async def test_on_step_end_handles_agent_without_history(self) -> None:
        """Callback is resilient when agent has no history attribute."""
        wrapped = wrap(
            MockAgent(result=_make_result()),
            output_dir="/tmp/pokant_test_nohist",
        )
        # Simulate calling the callback with an agent that has no history
        agent_no_history = SimpleNamespace()
        await wrapped._on_step_end(agent_no_history)
        # Should not raise

    async def test_on_step_end_handles_empty_history(self) -> None:
        wrapped = wrap(
            MockAgent(result=_make_result()),
            output_dir="/tmp/pokant_test_emptyhist",
        )
        agent_empty = SimpleNamespace(history=[])
        await wrapped._on_step_end(agent_empty)
        # Should not raise

    async def test_on_step_end_handles_non_list_history(self) -> None:
        """If history is not a list, callback degrades gracefully."""
        wrapped = wrap(
            MockAgent(result=_make_result()),
            output_dir="/tmp/pokant_test_nonlist",
        )
        agent_str_hist = SimpleNamespace(history="not a list")
        await wrapped._on_step_end(agent_str_hist)
        # Should not raise

    async def test_on_step_end_no_stop_method(self) -> None:
        """If agent lacks stop(), stuck detection logs but doesn't crash."""

        class AgentNoStop:
            history: list[Any] = []

            async def run(self, max_steps: int = 100, **kw: Any) -> Any:
                return _make_result()

        wrapped = wrap(
            AgentNoStop(),
            output_dir="/tmp/pokant_test_nostop",
        )
        # Manually inject a stuck detector that always fires
        from computeruse.stuck_detector import StuckSignal

        class AlwaysStuck:
            def check_agent_step(self, step: Any) -> StuckSignal:
                return StuckSignal(
                    detected=True,
                    reason="test",
                    details="forced",
                    step_number=1,
                )

        wrapped._stuck_detector = AlwaysStuck()  # type: ignore[assignment]
        agent_with_history = SimpleNamespace(history=[SimpleNamespace()])
        await wrapped._on_step_end(agent_with_history)
        # No crash — stop() is absent but that's ok

    async def test_on_step_end_noop_when_detection_disabled(self) -> None:
        wrapped = wrap(
            MockAgent(result=_make_result()),
            enable_stuck_detection=False,
            output_dir="/tmp/pokant_test_noop",
        )
        agent = SimpleNamespace(history=[SimpleNamespace()])
        await wrapped._on_step_end(agent)
        # No error, just returns


# ---------------------------------------------------------------------------
# Edge: Step enrichment
# ---------------------------------------------------------------------------


class TestStepEnrichmentEdgeCases:
    async def test_empty_history(self) -> None:
        """Result with empty history produces zero steps."""
        result = _make_result(steps=[], action_names=[], screenshots=[])
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_emptyhist2")
        await wrapped.run()
        assert wrapped.steps == []

    async def test_result_without_history_attribute(self) -> None:
        """Result missing .history entirely — enrichment degrades."""
        result = SimpleNamespace()  # no history, no screenshots, etc.
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_nohist2")
        await wrapped.run()
        assert wrapped.steps == []

    async def test_action_names_shorter_than_history(self) -> None:
        """Fewer action_names than history steps — extras get UNKNOWN."""
        steps = [_make_history_step(), _make_history_step()]
        result = _make_result(
            steps=steps, action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_short_names")
        await wrapped.run()
        assert len(wrapped.steps) == 2
        assert wrapped.steps[0].action_type == "click"
        assert wrapped.steps[1].action_type == "unknown"  # default

    async def test_screenshots_shorter_than_history(self) -> None:
        """Fewer screenshots than history steps — extras have no screenshot."""
        png = b"\x89PNG" + b"\x00" * 10
        steps = [_make_history_step(), _make_history_step()]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction", "WaitAction"],
            screenshots=[png],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_short_ss")
        await wrapped.run()
        assert wrapped.steps[0].screenshot_bytes == png
        assert wrapped.steps[1].screenshot_bytes is None

    async def test_screenshots_method_raises(self) -> None:
        """If result.screenshots() throws, enrichment still works."""
        steps = [_make_history_step()]

        def _bad_screenshots() -> list[Any]:
            raise RuntimeError("screenshots broken")

        result = SimpleNamespace(
            history=steps,
            screenshots=_bad_screenshots,
            action_names=lambda: ["ClickElementAction"],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_bad_ss")
        await wrapped.run()
        assert len(wrapped.steps) == 1

    async def test_action_names_method_raises(self) -> None:
        """If result.action_names() throws, enrichment still works."""
        steps = [_make_history_step()]

        def _bad_names() -> list[str]:
            raise RuntimeError("action_names broken")

        result = SimpleNamespace(
            history=steps,
            screenshots=lambda: [],
            action_names=_bad_names,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_bad_names")
        await wrapped.run()
        assert len(wrapped.steps) == 1
        assert wrapped.steps[0].action_type == "unknown"

    async def test_step_missing_model_output(self) -> None:
        """Step without model_output — description stays default."""
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=None,
            metadata=SimpleNamespace(
                input_tokens=10, output_tokens=5, step_duration=0.1
            ),
        )
        result = _make_result(
            steps=[step], action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_no_mo")
        await wrapped.run()
        assert wrapped.steps[0].description == ""  # default

    async def test_step_missing_metadata(self) -> None:
        """Step without metadata — tokens default to 0."""
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=SimpleNamespace(
                next_goal="test", evaluation_previous_goal=None
            ),
            metadata=None,
        )
        result = _make_result(
            steps=[step], action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_no_meta")
        await wrapped.run()
        assert wrapped.steps[0].tokens_in == 0
        assert wrapped.steps[0].tokens_out == 0
        assert wrapped.steps[0].duration_ms == 0

    async def test_step_result_not_a_list(self) -> None:
        """Step result that is not a list — skip error extraction."""
        step = SimpleNamespace(
            result="not a list",
            model_output=None,
            metadata=None,
        )
        result = _make_result(steps=[step], action_names=["ClickElementAction"])
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_result_str")
        await wrapped.run()
        assert wrapped.steps[0].success is True  # default

    async def test_step_multiple_errors_truncated(self) -> None:
        """Only first 3 errors are joined in step.error."""
        errors = [
            SimpleNamespace(error=f"err{i}") for i in range(5)
        ]
        step = SimpleNamespace(
            result=errors,
            model_output=None,
            metadata=None,
        )
        result = _make_result(steps=[step], action_names=["ClickElementAction"])
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_trunc_err")
        await wrapped.run()
        assert wrapped.steps[0].success is False
        assert "err0" in wrapped.steps[0].error
        assert "err2" in wrapped.steps[0].error
        assert "err4" not in wrapped.steps[0].error

    async def test_description_includes_eval_previous_goal(self) -> None:
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=SimpleNamespace(
                next_goal="Click submit",
                evaluation_previous_goal="Successfully typed email",
            ),
            metadata=None,
        )
        result = _make_result(steps=[step], action_names=["ClickElementAction"])
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_eval")
        await wrapped.run()
        desc = wrapped.steps[0].description
        assert "Click submit" in desc
        assert "[eval: Successfully typed email]" in desc

    async def test_description_truncated_at_500_chars(self) -> None:
        long_goal = "x" * 600
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=SimpleNamespace(
                next_goal=long_goal,
                evaluation_previous_goal=None,
            ),
            metadata=None,
        )
        result = _make_result(steps=[step], action_names=["ClickElementAction"])
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_trunc_desc")
        await wrapped.run()
        assert len(wrapped.steps[0].description) == 500

    async def test_unknown_action_name_maps_to_unknown(self) -> None:
        steps = [_make_history_step()]
        result = _make_result(
            steps=steps, action_names=["SomeFutureAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_unk_action")
        await wrapped.run()
        assert wrapped.steps[0].action_type == "unknown"

    async def test_step_number_is_one_based(self) -> None:
        steps = [_make_history_step(), _make_history_step()]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction", "ScrollAction"],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_stepnum")
        await wrapped.run()
        assert wrapped.steps[0].step_number == 1
        assert wrapped.steps[1].step_number == 2

    async def test_metadata_token_none_treated_as_zero(self) -> None:
        """getattr returns None for tokens — should become 0."""
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=None,
            metadata=SimpleNamespace(
                input_tokens=None,
                output_tokens=None,
                step_duration=None,
            ),
        )
        result = _make_result(steps=[step], action_names=["ClickElementAction"])
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_none_tokens")
        await wrapped.run()
        assert wrapped.steps[0].tokens_in == 0
        assert wrapped.steps[0].tokens_out == 0
        assert wrapped.steps[0].duration_ms == 0  # None → skip


# ---------------------------------------------------------------------------
# Edge: Action name mapping completeness
# ---------------------------------------------------------------------------


class TestActionMapping:
    def test_all_class_name_mappings(self) -> None:
        """Verify every class-name key in _ACTION_MAP resolves correctly."""
        assert _ACTION_MAP["GoToUrlAction"] == ActionType.NAVIGATE
        assert _ACTION_MAP["ClickElementAction"] == ActionType.CLICK
        assert _ACTION_MAP["InputTextAction"] == ActionType.TYPE
        assert _ACTION_MAP["ScrollAction"] == ActionType.SCROLL
        assert _ACTION_MAP["ExtractPageContentAction"] == ActionType.EXTRACT
        assert _ACTION_MAP["WaitAction"] == ActionType.WAIT
        assert _ACTION_MAP["DoneAction"] == ActionType.EXTRACT

    def test_all_snake_case_mappings(self) -> None:
        assert _ACTION_MAP["go_to_url"] == ActionType.NAVIGATE
        assert _ACTION_MAP["click_element"] == ActionType.CLICK
        assert _ACTION_MAP["input_text"] == ActionType.TYPE
        assert _ACTION_MAP["scroll"] == ActionType.SCROLL
        assert _ACTION_MAP["extract_content"] == ActionType.EXTRACT
        assert _ACTION_MAP["wait"] == ActionType.WAIT
        assert _ACTION_MAP["done"] == ActionType.EXTRACT

    def test_missing_key_returns_none(self) -> None:
        assert _ACTION_MAP.get("NonExistentAction") is None


# ---------------------------------------------------------------------------
# Edge: Cost calculation
# ---------------------------------------------------------------------------


class TestCostEdgeCases:
    async def test_cost_zero_when_no_tokens_and_no_total_cost(self) -> None:
        """No cost data at all — cost stays 0."""
        result = _make_result(total_cost_dollars=None)
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent, output_dir="/tmp/pokant_test_zero_cost"
        )
        await wrapped.run()
        assert wrapped.cost_cents == 0.0

    async def test_cost_zero_when_total_cost_returns_zero(self) -> None:
        result = _make_result(total_cost_dollars=0.0)
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent, output_dir="/tmp/pokant_test_cost_zero"
        )
        await wrapped.run()
        assert wrapped.cost_cents == 0.0

    async def test_cost_from_usage_attribute(self) -> None:
        """Falls back to result.usage.total_cost when total_cost() is None."""
        result = SimpleNamespace(
            history=[],
            screenshots=lambda: [],
            action_names=lambda: [],
            total_cost=lambda: None,
            usage=SimpleNamespace(total_cost=0.02),
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent, output_dir="/tmp/pokant_test_usage_cost"
        )
        await wrapped.run()
        assert wrapped.cost_cents == pytest.approx(2.0)

    async def test_cost_total_cost_raises(self) -> None:
        """If result.total_cost() throws, fall back to token-based calc."""
        steps = [_make_history_step(tokens_in=500, tokens_out=200)]

        def _boom() -> float:
            raise RuntimeError("cost broken")

        result = SimpleNamespace(
            history=steps,
            screenshots=lambda: [],
            action_names=lambda: ["ClickElementAction"],
            total_cost=_boom,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent, output_dir="/tmp/pokant_test_cost_exc"
        )
        await wrapped.run()
        assert wrapped.cost_cents > 0

    async def test_cost_tracking_disabled(self) -> None:
        """When track_cost=False, cost stays at 0."""
        result = _make_result(total_cost_dollars=0.10)
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            track_cost=False,
            output_dir="/tmp/pokant_test_no_cost",
        )
        await wrapped.run()
        assert wrapped.cost_cents == 0.0


# ---------------------------------------------------------------------------
# Edge: Screenshot saving
# ---------------------------------------------------------------------------


class TestScreenshotEdgeCases:
    async def test_no_screenshots_no_directory(self, tmp_path: Path) -> None:
        """When no step has screenshot_bytes, screenshot dir is still created."""
        steps = [_make_history_step(screenshot=None)]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
            screenshots=[None],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="no-ss",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        ss_dir = tmp_path / "screenshots" / "no-ss"
        assert ss_dir.exists()
        assert list(ss_dir.iterdir()) == []

    async def test_save_screenshots_disabled(self, tmp_path: Path) -> None:
        png = b"\x89PNG" + b"\x00" * 10
        steps = [_make_history_step(screenshot=png)]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
            screenshots=[png],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="ss-off",
            save_screenshots=False,
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        assert not (tmp_path / "screenshots").exists()

    async def test_multiple_screenshots_saved(self, tmp_path: Path) -> None:
        pngs = [b"\x89PNG" + bytes([i]) * 20 for i in range(3)]
        steps = [_make_history_step(screenshot=p) for p in pngs]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"] * 3,
            screenshots=pngs,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="multi-ss",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        ss_dir = tmp_path / "screenshots" / "multi-ss"
        for i, png in enumerate(pngs):
            assert (ss_dir / f"step_{i}.png").read_bytes() == png

    async def test_screenshot_path_set_on_step(self, tmp_path: Path) -> None:
        """After saving, step.screenshot_path points to the written file."""
        png = b"\x89PNG" + b"\x00" * 10
        steps = [_make_history_step(screenshot=png)]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
            screenshots=[png],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="path-test",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        # Access internal _steps (not copy) to check mutation
        assert wrapped._steps[0].screenshot_path != ""
        assert Path(wrapped._steps[0].screenshot_path).exists()


# ---------------------------------------------------------------------------
# Edge: Run metadata
# ---------------------------------------------------------------------------


class TestRunMetadataEdgeCases:
    async def test_metadata_has_all_expected_keys(
        self, tmp_path: Path
    ) -> None:
        steps = [_make_history_step(tokens_in=100, tokens_out=50)]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
            total_cost_dollars=0.01,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="full-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        data = json.loads(
            (tmp_path / "runs" / "full-meta.json").read_text()
        )
        expected_keys = {
            "task_id",
            "status",
            "step_count",
            "cost_cents",
            "error_category",
            "error",
            "created_at",
            "completed_at",
            "duration_ms",
            "steps",
            "analysis",
            # AR3: adaptive retry metadata
            "attempts",
            "total_attempts",
            "adaptive_retry_used",
        }
        assert set(data.keys()) == expected_keys

    async def test_metadata_step_fields(self, tmp_path: Path) -> None:
        steps = [
            _make_history_step(
                tokens_in=200,
                tokens_out=80,
                duration=1.2,
                next_goal="Click button",
            )
        ]
        result = _make_result(
            steps=steps, action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="step-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        data = json.loads(
            (tmp_path / "runs" / "step-meta.json").read_text()
        )
        step = data["steps"][0]
        assert step["action_type"] == "click"
        assert step["tokens_in"] == 200
        assert step["tokens_out"] == 80
        assert step["duration_ms"] == 1200
        assert step["success"] is True
        assert "Click button" in step["description"]

    async def test_metadata_duration_positive(self, tmp_path: Path) -> None:
        agent = MockAgent(result=_make_result())
        wrapped = wrap(
            agent,
            task_id="dur-test",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        data = json.loads(
            (tmp_path / "runs" / "dur-test.json").read_text()
        )
        assert data["duration_ms"] >= 0

    async def test_metadata_created_at_is_iso(self, tmp_path: Path) -> None:
        agent = MockAgent(result=_make_result())
        wrapped = wrap(
            agent,
            task_id="iso-test",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        data = json.loads(
            (tmp_path / "runs" / "iso-test.json").read_text()
        )
        # Should be parseable as ISO datetime
        datetime.fromisoformat(data["created_at"])
        datetime.fromisoformat(data["completed_at"])

    async def test_metadata_cost_reflects_calculation(
        self, tmp_path: Path
    ) -> None:
        result = _make_result(total_cost_dollars=0.03)
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="cost-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        data = json.loads(
            (tmp_path / "runs" / "cost-meta.json").read_text()
        )
        assert data["cost_cents"] == pytest.approx(3.0)

    async def test_metadata_on_retry_then_success(
        self, tmp_path: Path
    ) -> None:
        """After a retry, metadata reflects the successful attempt."""
        result = _make_result(
            steps=[_make_history_step()],
            action_names=["ClickElementAction"],
        )
        agent = MockAgent(errors=[_make_transient_error(), None])
        agent._result = result
        wrapped = wrap(
            agent,
            max_retries=3,
            adaptive_retry=False,
            task_id="retry-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        data = json.loads(
            (tmp_path / "runs" / "retry-meta.json").read_text()
        )
        assert data["status"] == "completed"
        assert data["step_count"] == 1

    async def test_metadata_zero_steps_on_empty_run(
        self, tmp_path: Path
    ) -> None:
        result = _make_result()
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="empty-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        data = json.loads(
            (tmp_path / "runs" / "empty-meta.json").read_text()
        )
        assert data["step_count"] == 0
        assert data["steps"] == []


# ---------------------------------------------------------------------------
# Edge: Replay generation
# ---------------------------------------------------------------------------


class TestReplayEdgeCases:
    async def test_no_replay_for_empty_steps(self, tmp_path: Path) -> None:
        """No replay generated when there are zero steps."""
        result = _make_result()
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="empty-replay",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        assert wrapped.replay_path is None
        assert not (tmp_path / "replays" / "empty-replay.html").exists()

    async def test_replay_contains_html(self, tmp_path: Path) -> None:
        steps = [_make_history_step()]
        result = _make_result(
            steps=steps, action_names=["ClickElementAction"]
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="html-check",
            output_dir=str(tmp_path),
        )
        await wrapped.run()
        content = Path(wrapped.replay_path).read_text()
        assert "<html" in content.lower()


# ---------------------------------------------------------------------------
# Edge: inspect.signature fallback
# ---------------------------------------------------------------------------


class TestInspectSignatureEdgeCases:
    async def test_uninspectable_run_still_works(self) -> None:
        """If inspect.signature fails, run() still proceeds without hooks."""

        class WeirdAgent:
            # Use __call__ trick to make signature inspection difficult
            pass

        agent = WeirdAgent()
        # Attach a run that works but can't be inspected normally
        call_count = 0

        async def custom_run(max_steps: int = 100, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return _make_result()

        agent.run = custom_run  # type: ignore[attr-defined]
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_weird")
        result = await wrapped.run()
        assert result is not None
        assert call_count == 1

    async def test_run_kwargs_forwarded(self) -> None:
        """Extra kwargs passed to run() are forwarded to the agent."""
        received: dict[str, Any] = {}

        class ForwardingAgent:
            async def run(self, max_steps: int = 100, **kw: Any) -> Any:
                received.update(kw)
                return _make_result()

        wrapped = wrap(
            ForwardingAgent(),
            output_dir="/tmp/pokant_test_fwd",
        )
        await wrapped.run(max_steps=50, custom_param="hello")
        assert received["custom_param"] == "hello"

    async def test_max_steps_forwarded(self) -> None:
        """max_steps is forwarded to agent.run()."""
        received_max_steps = None

        class CapturingAgent:
            async def run(
                self, max_steps: int = 100, on_step_end: Any = None
            ) -> Any:
                nonlocal received_max_steps
                received_max_steps = max_steps
                return _make_result()

        wrapped = wrap(
            CapturingAgent(),
            output_dir="/tmp/pokant_test_maxsteps",
        )
        await wrapped.run(max_steps=42)
        assert received_max_steps == 42


# ---------------------------------------------------------------------------
# Edge: Session management (best-effort)
# ---------------------------------------------------------------------------


class TestSessionEdgeCases:
    async def test_session_key_none_skips_session_ops(self) -> None:
        """No session_key means no session restore/save attempted."""
        agent = MockAgent(result=_make_result())
        wrapped = wrap(
            agent,
            session_key=None,
            output_dir="/tmp/pokant_test_no_sess",
        )
        await wrapped.run()
        # No error — session ops were skipped

    async def test_get_agent_page_returns_none_for_mock(self) -> None:
        """MockAgent has no page attribute — _get_agent_page returns None."""
        agent = MockAgent(result=_make_result())
        wrapped = wrap(agent)
        assert wrapped._get_agent_page() is None

    async def test_get_agent_page_finds_nested_page(self) -> None:
        """_get_agent_page traverses browser_session.page path."""
        page_obj = SimpleNamespace(url="https://example.com")
        agent = MockAgent(result=_make_result())
        agent.browser_session = SimpleNamespace(page=page_obj)  # type: ignore[attr-defined]
        wrapped = wrap(agent)
        assert wrapped._get_agent_page() is page_obj

    async def test_get_agent_page_finds_direct_page(self) -> None:
        page_obj = SimpleNamespace(url="https://example.com")
        agent = MockAgent(result=_make_result())
        agent.page = page_obj  # type: ignore[attr-defined]
        wrapped = wrap(agent)
        assert wrapped._get_agent_page() is page_obj

    async def test_get_agent_page_finds_browser_page(self) -> None:
        page_obj = SimpleNamespace(url="https://example.com")
        agent = MockAgent(result=_make_result())
        agent.browser = SimpleNamespace(page=page_obj)  # type: ignore[attr-defined]
        wrapped = wrap(agent)
        assert wrapped._get_agent_page() is page_obj


# ---------------------------------------------------------------------------
# Edge: Stuck detection thresholds
# ---------------------------------------------------------------------------


class TestStuckDetectorConfig:
    def test_custom_thresholds_passed_to_detector(self) -> None:
        wrapped = wrap(
            MockAgent(),
            stuck_screenshot_threshold=10,
            stuck_action_threshold=8,
            stuck_failure_threshold=6,
        )
        det = wrapped._stuck_detector
        assert det is not None
        assert det.screenshot_threshold == 10
        assert det.action_threshold == 8
        assert det.failure_threshold == 6


# ---------------------------------------------------------------------------
# Edge: Large-scale / multi-step
# ---------------------------------------------------------------------------


class TestLargeRuns:
    async def test_many_steps_enriched(self, tmp_path: Path) -> None:
        """50 steps are all correctly enriched."""
        n = 50
        steps = [
            _make_history_step(
                tokens_in=10 * i,
                tokens_out=5 * i,
                duration=0.1 * i,
            )
            for i in range(1, n + 1)
        ]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"] * n,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="big-run",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        assert len(wrapped.steps) == n
        assert wrapped.steps[0].step_number == 1
        assert wrapped.steps[-1].step_number == n
        assert wrapped.steps[-1].tokens_in == 10 * n

    async def test_all_action_types_in_single_run(self) -> None:
        """All known action types can appear in a single run."""
        names = [
            "GoToUrlAction",
            "ClickElementAction",
            "InputTextAction",
            "ScrollAction",
            "ExtractPageContentAction",
            "WaitAction",
            "DoneAction",
        ]
        steps = [_make_history_step() for _ in names]
        result = _make_result(steps=steps, action_names=names)
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent, output_dir="/tmp/pokant_test_all_actions"
        )
        await wrapped.run()

        types = [s.action_type for s in wrapped.steps]
        assert types == [
            "navigate",
            "click",
            "type",
            "scroll",
            "extract",
            "wait",
            "extract",  # DoneAction maps to extract
        ]


# ---------------------------------------------------------------------------
# API reporting
# ---------------------------------------------------------------------------


class TestApiReporting:
    """Tests for optional API reporting in wrap()."""

    async def test_reports_on_success(self) -> None:
        result = _make_result()
        agent = MockAgent(result=result)

        with patch(
            "computeruse._reporting.report_to_api", return_value=True
        ) as mock_report:
            wrapped = wrap(
                agent,
                api_url="http://localhost:3000",
                api_key="test-key",
                output_dir="/tmp/pokant_test_report_ok",
            )
            await wrapped.run()

        mock_report.assert_awaited_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["api_url"] == "http://localhost:3000"
        assert call_kwargs["api_key"] == "test-key"
        assert call_kwargs["error_category"] is None
        assert call_kwargs["error_message"] is None

    async def test_reports_on_failure(self) -> None:
        agent = MockAgent(error=ValueError("task broke"))

        with patch(
            "computeruse._reporting.report_to_api", return_value=True
        ) as mock_report:
            wrapped = wrap(
                agent,
                api_url="http://localhost:3000",
                api_key="test-key",
                max_retries=0,
                adaptive_retry=False,
                output_dir="/tmp/pokant_test_report_fail",
            )
            with pytest.raises(ValueError, match="task broke"):
                await wrapped.run()

        mock_report.assert_awaited_once()
        call_kwargs = mock_report.call_args[1]
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["error_message"] == "task broke"

    async def test_no_report_without_config(self) -> None:
        result = _make_result()
        agent = MockAgent(result=result)

        with patch(
            "computeruse._reporting.report_to_api"
        ) as mock_report:
            wrapped = wrap(
                agent,
                output_dir="/tmp/pokant_test_no_report",
            )
            await wrapped.run()

        mock_report.assert_not_called()

    async def test_continues_if_reporting_fails(self) -> None:
        result = _make_result()
        agent = MockAgent(result=result)

        with patch(
            "computeruse._reporting.report_to_api", return_value=False
        ):
            wrapped = wrap(
                agent,
                api_url="http://localhost:3000",
                api_key="test-key",
                output_dir="/tmp/pokant_test_report_false",
            )
            run_result = await wrapped.run()

        assert run_result is result


# ---------------------------------------------------------------------------
# Tests: _extract_step_tokens multi-path extraction
# ---------------------------------------------------------------------------


class TestExtractStepTokens:
    def test_primary_path_input_output_tokens(self) -> None:
        """Standard path: metadata.input_tokens / metadata.output_tokens."""
        step = SimpleNamespace(
            metadata=SimpleNamespace(input_tokens=100, output_tokens=50),
        )
        assert _extract_step_tokens(step) == (100, 50)

    def test_alternate_path_tokens_in_out(self) -> None:
        """Alternate path: metadata.tokens_in / metadata.tokens_out."""
        step = SimpleNamespace(
            metadata=SimpleNamespace(tokens_in=200, tokens_out=80),
        )
        assert _extract_step_tokens(step) == (200, 80)

    def test_direct_step_attributes(self) -> None:
        """Direct path: step.input_tokens / step.output_tokens."""
        step = SimpleNamespace(input_tokens=300, output_tokens=120)
        assert _extract_step_tokens(step) == (300, 120)

    def test_no_tokens_returns_zero(self) -> None:
        """No token attributes anywhere returns (0, 0)."""
        step = SimpleNamespace()
        assert _extract_step_tokens(step) == (0, 0)

    def test_none_metadata_falls_through(self) -> None:
        """metadata=None falls through to direct attributes."""
        step = SimpleNamespace(
            metadata=None, input_tokens=150, output_tokens=60,
        )
        assert _extract_step_tokens(step) == (150, 60)


# ---------------------------------------------------------------------------
# Tests: cost from alternate token paths (Bug 1 verification)
# ---------------------------------------------------------------------------


class TestCostAlternateTokenPaths:
    async def test_cost_from_alternate_metadata_tokens(self) -> None:
        """tokens_in/tokens_out on metadata still produces cost > 0."""
        meta = SimpleNamespace(tokens_in=1000, tokens_out=500)
        history_step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=SimpleNamespace(
                action=[SimpleNamespace()],
                next_goal="Do something",
                evaluation_previous_goal=None,
            ),
            metadata=meta,
            state=SimpleNamespace(screenshot=None),
        )
        result = _make_result(
            steps=[history_step],
            action_names=["ClickElementAction"],
            total_cost_dollars=None,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_alt_tokens")
        await wrapped.run()
        assert wrapped.cost_cents > 0
        assert wrapped.steps[0].tokens_in == 1000
        assert wrapped.steps[0].tokens_out == 500

    async def test_cost_from_direct_step_tokens(self) -> None:
        """Tokens directly on step object still produce cost > 0."""
        history_step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=SimpleNamespace(
                action=[SimpleNamespace()],
                next_goal="Do something",
                evaluation_previous_goal=None,
            ),
            metadata=None,
            state=SimpleNamespace(screenshot=None),
            input_tokens=2000,
            output_tokens=800,
        )
        result = _make_result(
            steps=[history_step],
            action_names=["ClickElementAction"],
            total_cost_dollars=None,
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_direct_tokens")
        await wrapped.run()
        assert wrapped.cost_cents > 0
        assert wrapped.steps[0].tokens_in == 2000
        assert wrapped.steps[0].tokens_out == 800


# ---------------------------------------------------------------------------
# Tests: max_cost_cents budget enforcement (Bug 2)
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    def test_max_cost_cents_default_none(self) -> None:
        cfg = WrapConfig()
        assert cfg.max_cost_cents is None

    def test_max_cost_cents_set(self) -> None:
        cfg = WrapConfig(max_cost_cents=5.0)
        assert cfg.max_cost_cents == 5.0

    async def test_budget_exceeded_stops_agent(self) -> None:
        """When accumulated cost > max_cost_cents, agent.stop() is called."""
        agent = MockAgent(result=_make_result())
        wrapped = wrap(agent, max_cost_cents=0.001, output_dir="/tmp/pokant_test_budget")

        # Simulate a step with enough tokens to exceed the tiny budget
        expensive_step = SimpleNamespace(
            metadata=SimpleNamespace(input_tokens=100000, output_tokens=50000),
        )
        mock_agent = SimpleNamespace(
            history=[expensive_step],
            stop=MagicMock(),
        )
        await wrapped._on_step_end(mock_agent)
        mock_agent.stop.assert_called_once()

    async def test_budget_not_exceeded_no_stop(self) -> None:
        """When accumulated cost < max_cost_cents, agent continues."""
        agent = MockAgent(result=_make_result())
        wrapped = wrap(agent, max_cost_cents=100.0, output_dir="/tmp/pokant_test_budget_ok")

        cheap_step = SimpleNamespace(
            metadata=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
        mock_agent = SimpleNamespace(
            history=[cheap_step],
            stop=MagicMock(),
        )
        await wrapped._on_step_end(mock_agent)
        mock_agent.stop.assert_not_called()

    async def test_budget_none_no_enforcement(self) -> None:
        """When max_cost_cents is None, no budget check happens."""
        agent = MockAgent(result=_make_result())
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_no_budget")

        expensive_step = SimpleNamespace(
            metadata=SimpleNamespace(input_tokens=999999, output_tokens=999999),
        )
        mock_agent = SimpleNamespace(
            history=[expensive_step],
            stop=MagicMock(),
        )
        await wrapped._on_step_end(mock_agent)
        mock_agent.stop.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: interrupt safety (Bug 3)
# ---------------------------------------------------------------------------


class TestInterruptSafety:
    async def test_finally_saves_metadata_on_error(
        self, tmp_path: Path,
    ) -> None:
        """On unhandled exception, finally block saves partial metadata."""
        agent = MockAgent(error=_make_permanent_error("crash"))
        wrapped = wrap(
            agent,
            task_id="test-interrupt",
            max_retries=0,
            adaptive_retry=False,
            output_dir=str(tmp_path),
        )
        with pytest.raises(Exception):
            await wrapped.run()

        meta_path = tmp_path / "runs" / "test-interrupt.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["status"] == "failed"

    async def test_interrupted_flag_initially_false(self) -> None:
        agent = MockAgent(result=_make_result())
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_flag")
        assert wrapped._interrupted is False

    async def test_budget_resets_on_retry(self) -> None:
        """BudgetMonitor and inline fallback reset between retry attempts."""
        result = _make_result()
        agent = MockAgent(errors=[_make_transient_error(), None])
        agent._result = result
        wrapped = wrap(
            agent,
            max_retries=3,
            adaptive_retry=False,
            max_cost_cents=100.0,
            output_dir="/tmp/pokant_test_cost_reset",
        )
        # Manually record cost to prove budget gets reset on retry
        wrapped._budget.record_cost_direct(50.0)
        wrapped._accumulated_cost = 50.0
        await wrapped.run()
        # After a retry, both paths should have been reset
        assert wrapped._budget.total_cost_cents == 0.0
        assert wrapped._accumulated_cost == 0.0


# ---------------------------------------------------------------------------
# Tests: Enrichment second pass (intent + selectors from model_output)
# ---------------------------------------------------------------------------


class TestEnrichmentSecondPass:
    async def test_intent_from_next_goal(self) -> None:
        """_enrich_steps should set intent from model_output.next_goal."""
        steps = [
            _make_history_step(
                action_name="ClickElementAction",
                next_goal="Click the login button",
            ),
        ]
        result = _make_result(
            steps=steps,
            action_names=["ClickElementAction"],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_intent")
        await wrapped.run()

        assert wrapped.steps[0].intent == "Click the login button"

    async def test_selectors_from_action_objects(self) -> None:
        """_enrich_steps should extract selector from action objects."""
        mo = SimpleNamespace(
            action=[SimpleNamespace(selector="#login-btn")],
            next_goal="Click login",
            evaluation_previous_goal=None,
        )
        meta = SimpleNamespace(
            input_tokens=100, output_tokens=50, step_duration=0.5,
        )
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=mo,
            metadata=meta,
            state=SimpleNamespace(screenshot=None),
        )
        result = _make_result(
            steps=[step],
            action_names=["ClickElementAction"],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(agent, output_dir="/tmp/pokant_test_selectors")
        await wrapped.run()

        assert wrapped.steps[0].selectors is not None
        assert wrapped.steps[0].selectors[0]["value"] == "#login-btn"
        assert wrapped.steps[0].selectors[0]["confidence"] == 0.8

    async def test_enrichment_fields_in_metadata_json(
        self, tmp_path: Path,
    ) -> None:
        """Enrichment fields should appear in saved run metadata JSON."""
        mo = SimpleNamespace(
            action=[SimpleNamespace(selector="#btn")],
            next_goal="Click button",
            evaluation_previous_goal=None,
        )
        meta = SimpleNamespace(
            input_tokens=100, output_tokens=50, step_duration=0.5,
        )
        step = SimpleNamespace(
            result=[SimpleNamespace(error=None)],
            model_output=mo,
            metadata=meta,
            state=SimpleNamespace(screenshot=None),
        )
        result = _make_result(
            steps=[step],
            action_names=["ClickElementAction"],
        )
        agent = MockAgent(result=result)
        wrapped = wrap(
            agent,
            task_id="test-enrichment-meta",
            output_dir=str(tmp_path),
        )
        await wrapped.run()

        meta_path = tmp_path / "runs" / "test-enrichment-meta.json"
        data = json.loads(meta_path.read_text())
        step_data = data["steps"][0]
        assert step_data["intent"] == "Click button"
        assert step_data["selectors"][0]["value"] == "#btn"


# ---------------------------------------------------------------------------
# Tests: BudgetMonitor integration
# ---------------------------------------------------------------------------


class TestBudgetMonitorIntegration:
    def test_budget_monitor_created_with_limit(self) -> None:
        agent = MockAgent()
        wrapped = wrap(agent, max_cost_cents=50.0)
        assert wrapped._budget.max_cost_cents == 50.0

    def test_budget_monitor_created_without_limit(self) -> None:
        agent = MockAgent()
        wrapped = wrap(agent)
        assert wrapped._budget.max_cost_cents is None
