"""
tests/unit/test_step_enrichment.py — Unit tests for browser_use step enrichment.

Tests:
- _map_browser_use_action mapping
- _enrich_steps_from_history backfilling
- _calculate_cost_from_result extraction
- End-to-end execute() with enriched history
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.executor import TaskExecutor
from workers.models import ActionType, StepData, TaskConfig, TaskResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(max_steps: int = 10) -> TaskExecutor:
    config = TaskConfig(url="https://example.com", task="Test task", max_steps=max_steps)
    return TaskExecutor(
        config=config,
        browser_manager=AsyncMock(),
        llm_client=MagicMock(),
    )


def _make_history_entry(
    action_name: str = "ClickElementAction",
    next_goal: str = "Click the button",
    eval_prev: str | None = None,
    input_tokens: int = 500,
    output_tokens: int = 100,
    step_duration: float = 1.5,
    error: str | None = None,
) -> SimpleNamespace:
    """Build a mock browser_use AgentHistory entry."""
    model_output = SimpleNamespace(
        action=[SimpleNamespace(type=action_name)],
        next_goal=next_goal,
        evaluation_previous_goal=eval_prev,
    )
    metadata = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        step_duration=step_duration,
    )
    results = []
    if error:
        results.append(SimpleNamespace(error=error, success=False))
    else:
        results.append(SimpleNamespace(error=None, success=True))

    return SimpleNamespace(
        model_output=model_output,
        metadata=metadata,
        result=results,
    )


def _make_agent_result(
    history: list | None = None,
    screenshots: list[bytes] | None = None,
    action_names: list[str] | None = None,
    total_cost: float | None = None,
    is_done: bool = True,
) -> MagicMock:
    """Build a mock AgentHistoryList result."""
    result = MagicMock()
    result.history = history or []
    result.screenshots.return_value = screenshots or []
    result.action_names.return_value = action_names or []
    result.final_result.return_value = {"data": "test"}
    result.is_done.return_value = is_done

    if total_cost is not None:
        result.total_cost.return_value = total_cost
    else:
        result.total_cost.return_value = 0.0

    result.usage = None
    return result


# ---------------------------------------------------------------------------
# Test: _map_browser_use_action
# ---------------------------------------------------------------------------


class TestMapBrowserUseAction:
    def test_class_name_click(self):
        assert TaskExecutor._map_browser_use_action("ClickElementAction") == ActionType.CLICK

    def test_class_name_type(self):
        assert TaskExecutor._map_browser_use_action("InputTextAction") == ActionType.TYPE

    def test_class_name_navigate(self):
        assert TaskExecutor._map_browser_use_action("GoToUrlAction") == ActionType.NAVIGATE

    def test_class_name_scroll(self):
        assert TaskExecutor._map_browser_use_action("ScrollAction") == ActionType.SCROLL

    def test_class_name_extract(self):
        assert TaskExecutor._map_browser_use_action("ExtractPageContentAction") == ActionType.EXTRACT

    def test_class_name_wait(self):
        assert TaskExecutor._map_browser_use_action("WaitAction") == ActionType.WAIT

    def test_class_name_done(self):
        assert TaskExecutor._map_browser_use_action("DoneAction") == ActionType.EXTRACT

    def test_snake_case_click(self):
        assert TaskExecutor._map_browser_use_action("click_element") == ActionType.CLICK

    def test_snake_case_input(self):
        assert TaskExecutor._map_browser_use_action("input_text") == ActionType.TYPE

    def test_unknown_action(self):
        assert TaskExecutor._map_browser_use_action("SomeFutureAction") == ActionType.UNKNOWN

    def test_empty_string(self):
        assert TaskExecutor._map_browser_use_action("") == ActionType.UNKNOWN


# ---------------------------------------------------------------------------
# Test: _enrich_steps_from_history
# ---------------------------------------------------------------------------


class TestEnrichStepsFromHistory:
    def test_enriches_token_data(self):
        executor = _make_executor()
        # Simulate: step 0 = navigate, step 1 = agent step
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                     action_type=ActionType.NAVIGATE, description="Nav"),
            StepData(step_number=2, timestamp=datetime.now(timezone.utc)),
        ]
        history_entry = _make_history_entry(input_tokens=1200, output_tokens=300, step_duration=2.0)
        result = _make_agent_result(
            history=[history_entry],
            action_names=["ClickElementAction"],
        )

        executor._enrich_steps_from_history(result)

        step = executor.steps[1]
        assert step.tokens_in == 1200
        assert step.tokens_out == 300
        assert step.duration_ms == 2000
        assert step.action_type == ActionType.CLICK

    def test_enriches_screenshots(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc)),
            StepData(step_number=2, timestamp=datetime.now(timezone.utc)),
        ]
        screenshot = b"\xff\xd8fake-screenshot"
        result = _make_agent_result(
            history=[_make_history_entry()],
            screenshots=[screenshot],
        )

        executor._enrich_steps_from_history(result)

        assert executor.steps[1].screenshot_bytes == screenshot

    def test_marks_failed_steps(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc)),
            StepData(step_number=2, timestamp=datetime.now(timezone.utc)),
        ]
        result = _make_agent_result(
            history=[_make_history_entry(error="Element not found")],
            action_names=["ClickElementAction"],
        )

        executor._enrich_steps_from_history(result)

        assert executor.steps[1].success is False
        assert "Element not found" in executor.steps[1].error

    def test_enriches_description_from_model_output(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc)),
            StepData(step_number=2, timestamp=datetime.now(timezone.utc)),
        ]
        result = _make_agent_result(
            history=[_make_history_entry(next_goal="Fill in the email field", eval_prev="Success")],
        )

        executor._enrich_steps_from_history(result)

        assert "Fill in the email field" in executor.steps[1].description
        assert "[eval: Success]" in executor.steps[1].description

    def test_appends_missing_steps(self):
        executor = _make_executor()
        # Only navigate step, but history has 2 entries
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                     action_type=ActionType.NAVIGATE),
        ]
        result = _make_agent_result(
            history=[_make_history_entry(), _make_history_entry()],
            action_names=["ClickElementAction", "InputTextAction"],
        )

        executor._enrich_steps_from_history(result)

        assert len(executor.steps) == 3
        assert executor.steps[1].action_type == ActionType.CLICK
        assert executor.steps[2].action_type == ActionType.TYPE

    def test_handles_empty_history(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                     action_type=ActionType.NAVIGATE),
        ]
        result = _make_agent_result(history=[])

        executor._enrich_steps_from_history(result)

        assert len(executor.steps) == 1
        assert executor.steps[0].action_type == ActionType.NAVIGATE

    def test_handles_missing_attributes_gracefully(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc)),
            StepData(step_number=2, timestamp=datetime.now(timezone.utc)),
        ]
        # Minimal entry with no model_output, metadata, or result
        bare_entry = SimpleNamespace()
        result = _make_agent_result(history=[bare_entry])

        # Should not raise
        executor._enrich_steps_from_history(result)
        assert executor.steps[1].tokens_in == 0

    def test_navigate_step_untouched(self):
        executor = _make_executor()
        nav_step = StepData(
            step_number=1, timestamp=datetime.now(timezone.utc),
            action_type=ActionType.NAVIGATE, description="Navigated to https://example.com",
            screenshot_bytes=b"nav-screenshot",
        )
        executor.steps = [nav_step]
        result = _make_agent_result(history=[])

        executor._enrich_steps_from_history(result)

        assert executor.steps[0].action_type == ActionType.NAVIGATE
        assert executor.steps[0].screenshot_bytes == b"nav-screenshot"

    def test_multiple_steps_enriched(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                     action_type=ActionType.NAVIGATE),
            StepData(step_number=2, timestamp=datetime.now(timezone.utc)),
            StepData(step_number=3, timestamp=datetime.now(timezone.utc)),
            StepData(step_number=4, timestamp=datetime.now(timezone.utc)),
        ]
        result = _make_agent_result(
            history=[
                _make_history_entry(input_tokens=100, output_tokens=50),
                _make_history_entry(input_tokens=200, output_tokens=75),
                _make_history_entry(input_tokens=300, output_tokens=100),
            ],
            action_names=["ClickElementAction", "InputTextAction", "DoneAction"],
            screenshots=[b"ss1", b"ss2", b"ss3"],
        )

        executor._enrich_steps_from_history(result)

        assert executor.steps[1].tokens_in == 100
        assert executor.steps[2].tokens_in == 200
        assert executor.steps[3].tokens_in == 300
        assert executor.steps[1].action_type == ActionType.CLICK
        assert executor.steps[2].action_type == ActionType.TYPE
        assert executor.steps[3].action_type == ActionType.EXTRACT
        assert executor.steps[1].screenshot_bytes == b"ss1"
        assert executor.steps[2].screenshot_bytes == b"ss2"


# ---------------------------------------------------------------------------
# Test: _calculate_cost_from_result
# ---------------------------------------------------------------------------


class TestCalculateCostFromResult:
    def test_uses_total_cost_when_available(self):
        executor = _make_executor()
        executor.steps = []
        result = _make_agent_result(total_cost=0.05)  # $0.05

        cost_cents = executor._calculate_cost_from_result(result)

        assert cost_cents == pytest.approx(5.0)  # 5 cents

    def test_uses_usage_summary_fallback(self):
        executor = _make_executor()
        executor.steps = []
        result = _make_agent_result()
        result.total_cost.return_value = 0.0  # no total_cost
        result.usage = SimpleNamespace(total_cost=0.03)

        cost_cents = executor._calculate_cost_from_result(result)

        assert cost_cents == pytest.approx(3.0)

    def test_falls_back_to_token_sum(self):
        executor = _make_executor()
        executor.steps = [
            StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                     tokens_in=10_000, tokens_out=2_000),
        ]
        result = _make_agent_result()
        result.total_cost.return_value = 0.0
        result.usage = None

        cost_cents = executor._calculate_cost_from_result(result)

        # (10000 * 3.0 + 2000 * 15.0) / 1_000_000 * 100
        # = (30000 + 30000) / 1_000_000 * 100 = 0.06 * 100 = 6.0 cents... wait
        # = (30000 + 30000) / 1_000_000 = 0.06 dollars = 6.0 cents
        expected = (10_000 * 3.0 + 2_000 * 15.0) / 1_000_000 * 100
        assert cost_cents == pytest.approx(expected)

    def test_returns_zero_for_empty_result(self):
        executor = _make_executor()
        executor.steps = []
        result = _make_agent_result()
        result.total_cost.return_value = 0.0
        result.usage = None

        assert executor._calculate_cost_from_result(result) == 0.0


# ---------------------------------------------------------------------------
# Test: TaskResult token totals
# ---------------------------------------------------------------------------


class TestTaskResultTokenTotals:
    def test_fields_exist_with_defaults(self):
        r = TaskResult(task_id="t", status="completed", success=True)
        assert r.total_tokens_in == 0
        assert r.total_tokens_out == 0

    def test_fields_populated(self):
        r = TaskResult(
            task_id="t", status="completed", success=True,
            total_tokens_in=5000, total_tokens_out=1200,
        )
        assert r.total_tokens_in == 5000
        assert r.total_tokens_out == 1200


# ---------------------------------------------------------------------------
# Test: end-to-end execute() with enriched history
# ---------------------------------------------------------------------------


class TestExecuteWithEnrichment:
    """After PAV rewire, execute() delegates to run_pav_loop which handles enrichment."""

    async def test_execute_populates_tokens_and_cost(self):
        expected = TaskResult(
            task_id="enrich-1",
            status="completed",
            success=True,
            total_tokens_in=1800,
            total_tokens_out=350,
            cost_cents=1.2,
            step_data=[
                StepData(step_number=1, timestamp=datetime.now(timezone.utc),
                         action_type=ActionType.NAVIGATE, description="Nav", success=True),
                StepData(step_number=2, timestamp=datetime.now(timezone.utc),
                         action_type=ActionType.CLICK, description="Click",
                         tokens_in=1000, tokens_out=200, success=True),
                StepData(step_number=3, timestamp=datetime.now(timezone.utc),
                         action_type=ActionType.EXTRACT, description="Done",
                         tokens_in=800, tokens_out=150, success=True),
            ],
            steps=3,
        )

        config = TaskConfig(url="https://example.com", task="Test", max_steps=10)
        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            executor = TaskExecutor(
                config=config,
                browser_manager=AsyncMock(),
                llm_client=MagicMock(),
            )
            result = await executor.execute()

        assert result.success is True
        assert result.total_tokens_in == 1800
        assert result.total_tokens_out == 350
        assert result.cost_cents == pytest.approx(1.2)
        assert result.step_data[1].action_type == ActionType.CLICK
        assert result.step_data[2].action_type == ActionType.EXTRACT
        assert result.step_data[1].tokens_in == 1000
        assert result.step_data[2].tokens_in == 800

    async def test_execute_graceful_with_empty_history(self):
        expected = TaskResult(
            task_id="enrich-2",
            status="completed",
            success=True,
            total_tokens_in=0,
            total_tokens_out=0,
            steps=1,
        )

        config = TaskConfig(url="https://example.com", task="Test", max_steps=5)
        with patch("workers.pav.loop.run_pav_loop", new_callable=AsyncMock, return_value=expected):
            executor = TaskExecutor(
                config=config,
                browser_manager=AsyncMock(),
                llm_client=MagicMock(),
            )
            result = await executor.execute()

        assert result.success is True
        assert result.total_tokens_in == 0
        assert result.total_tokens_out == 0
