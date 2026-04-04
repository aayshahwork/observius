"""Integration tests for the adaptive retry system (AR3).

Tests the full pipeline: wrap.py → FailureAnalyzer → RecoveryRouter → RetryMemory.
All tests use mock agents — no real browser or API calls.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from computeruse.failure_analyzer import FailureAnalyzer, FailureCategory, FailureDiagnosis
from computeruse.recovery_router import RecoveryRouter
from computeruse.retry_memory import RetryMemory
from computeruse.wrap import WrapConfig, wrap


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class MockResult:
    """Minimal successful agent result."""

    def __init__(self) -> None:
        self.history: list[Any] = []

    def action_names(self) -> list[str]:
        return []

    def screenshots(self) -> list[Any]:
        return []

    def total_cost(self) -> Optional[float]:
        return 0.0


def _make_history_step(
    action_name: str = "ClickElementAction",
    error: Optional[str] = None,
    next_goal: str = "Do action",
    tokens_in: int = 10,
    tokens_out: int = 5,
) -> SimpleNamespace:
    """Build a browser_use-style history step for _enrich_steps_partial."""
    result_list = [SimpleNamespace(error=error)]
    mo = SimpleNamespace(
        action=[SimpleNamespace()],
        next_goal=next_goal,
        evaluation_previous_goal=None,
    )
    meta = SimpleNamespace(
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        step_duration=0.1,
    )
    state = SimpleNamespace(screenshot=None)
    return SimpleNamespace(
        result=result_list,
        model_output=mo,
        metadata=meta,
        state=state,
    )


def _make_transient_error(message: str = "overloaded") -> Exception:
    """Error classified as transient_llm (retriable by dumb retry)."""

    class _Exc(Exception):
        __module__ = "anthropic._exceptions"

    _Exc.__name__ = "InternalServerError"
    exc = _Exc(message)
    exc.status_code = 529  # type: ignore[attr-defined]
    return exc


def _base_config(tmp_path: Path, **overrides: Any) -> WrapConfig:
    """WrapConfig with sane test defaults."""
    defaults = dict(
        adaptive_retry=True,
        diagnostic_api_key=None,
        generate_replay=False,
        save_screenshots=False,
        output_dir=str(tmp_path),
        max_retries=3,
    )
    defaults.update(overrides)
    return WrapConfig(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_sleep():
    """Eliminate all asyncio.sleep calls during tests."""
    with patch("asyncio.sleep", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# Test 1: Adaptive retry diagnoses and modifies task
# ---------------------------------------------------------------------------


async def test_adaptive_retry_modifies_task_on_failure(tmp_path: Path):
    """When agent fails, the retry should inject failure context into the task."""
    call_count = 0
    received_tasks: list[str] = []

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Click the submit button on example.com"
            self.history: list[Any] = []

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            received_tasks.append(self.task)

            if call_count == 1:
                raise Exception("Element #submit not found")

            return MockResult()

        def add_new_task(self, new_task: str) -> None:
            self.task = new_task

        def stop(self) -> None:
            pass

    agent = MockAgent()
    wrapped = wrap(agent, _base_config(tmp_path, max_retries=2))

    await wrapped.run()

    assert call_count == 2
    assert len(received_tasks) == 2
    # First attempt: original task
    assert received_tasks[0] == "Click the submit button on example.com"
    # Second attempt: should contain failure context
    assert "PREVIOUS ATTEMPT" in received_tasks[1] or "failed" in received_tasks[1].lower()
    assert "submit" in received_tasks[1].lower()


# ---------------------------------------------------------------------------
# Test 2: Dumb retry when adaptive is disabled
# ---------------------------------------------------------------------------


async def test_dumb_retry_when_adaptive_disabled(tmp_path: Path):
    """With adaptive_retry=False, task should NOT be modified between retries."""
    call_count = 0
    received_tasks: list[str] = []

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Click the submit button on example.com"
            self.history: list[Any] = []

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            received_tasks.append(self.task)

            if call_count == 1:
                raise _make_transient_error("server overloaded")

            return MockResult()

        def stop(self) -> None:
            pass

    agent = MockAgent()
    wrapped = wrap(agent, _base_config(tmp_path, adaptive_retry=False, max_retries=2))

    await wrapped.run()

    assert call_count == 2
    assert len(received_tasks) == 2
    # Task should be identical — no modification
    assert received_tasks[0] == received_tasks[1]


# ---------------------------------------------------------------------------
# Test 3: Give up on non-retryable failure (CAPTCHA)
# ---------------------------------------------------------------------------


async def test_gives_up_on_captcha(tmp_path: Path):
    """CAPTCHA diagnosis should cause immediate give-up, no retry."""
    call_count = 0

    class MockAgent:
        task = "Fill form"
        history: list[Any] = []

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise Exception("Cloudflare CAPTCHA challenge detected")

        def stop(self) -> None:
            pass

    wrapped = wrap(MockAgent(), _base_config(tmp_path, max_retries=3))

    with pytest.raises(Exception, match="CAPTCHA"):
        await wrapped.run()

    # Should not have retried — only 1 attempt
    assert call_count == 1
    assert len(wrapped.attempt_history) == 1
    diag = wrapped.attempt_history[0].get("diagnosis")
    assert diag is not None
    assert diag["category"] == "anti_bot"


# ---------------------------------------------------------------------------
# Test 4: Attempt history is saved
# ---------------------------------------------------------------------------


async def test_attempt_history_recorded(tmp_path: Path):
    """Each attempt should record diagnosis and recovery plan."""
    call_count = 0

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Navigate to page"
            self.history: list[Any] = []

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1

            if call_count <= 2:
                raise Exception("Element not found on page")

            return MockResult()

        def add_new_task(self, new_task: str) -> None:
            self.task = new_task

        def stop(self) -> None:
            pass

    wrapped = wrap(MockAgent(), _base_config(tmp_path, max_retries=3))
    await wrapped.run()

    assert call_count == 3
    history = wrapped.attempt_history
    assert len(history) == 3

    # First 2 attempts: failed, should have diagnosis + recovery_plan
    for entry in history[:2]:
        assert entry["status"] == "failed"
        assert entry["diagnosis"] is not None
        assert entry["recovery_plan"] is not None

    # Last attempt: succeeded, diagnosis should be None
    assert history[2]["status"] == "completed"
    assert history[2]["diagnosis"] is None
    assert history[2]["recovery_plan"] is None


# ---------------------------------------------------------------------------
# Test 5: Memory prevents repeating same approach
# ---------------------------------------------------------------------------


async def test_memory_accumulates_across_retries(tmp_path: Path):
    """Failure context from attempt 1 should appear in attempt 3's task."""
    call_count = 0
    received_tasks: list[str] = []

    # Use different error types so same_category_count never hits 3
    _errors = [
        "Element #submit not found",              # element_interaction
        "Page navigation timed out",               # navigation
        "Expected content not found on page",      # content_mismatch
    ]

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Fill in the registration form"
            self.history: list[Any] = []

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            received_tasks.append(self.task)

            if call_count <= 3:
                raise Exception(_errors[call_count - 1])

            return MockResult()

        def add_new_task(self, new_task: str) -> None:
            self.task = new_task

        def stop(self) -> None:
            pass

    wrapped = wrap(MockAgent(), _base_config(tmp_path, max_retries=4))
    await wrapped.run()

    assert call_count == 4
    assert len(received_tasks) == 4

    # 3rd attempt (index 2) should reference earlier attempts via memory
    third_task = received_tasks[2]
    assert "EARLIER ATTEMPTS" in third_task or "PREVIOUS ATTEMPT" in third_task

    # 4th attempt (index 3) should contain context from multiple earlier failures
    fourth_task = received_tasks[3]
    assert "EARLIER ATTEMPTS" in fourth_task
    # Memory records should reference prior attempts
    assert "Attempt" in fourth_task


# ---------------------------------------------------------------------------
# Test 6: Rule-based vs LLM diagnosis
# ---------------------------------------------------------------------------


def test_rule_based_is_free():
    """Rule-based diagnosis should have zero cost."""
    analyzer = FailureAnalyzer(enable_llm=False)
    result = asyncio.run(analyzer.analyze(
        task_description="Test",
        steps=[],
        error="Element not found",
    ))
    assert result.analysis_cost_cents == 0.0
    assert result.analysis_method == "rule_based"


# ---------------------------------------------------------------------------
# Test 7: Run metadata includes retry data
# ---------------------------------------------------------------------------


async def test_run_json_includes_attempts(tmp_path: Path):
    """The saved run JSON should include attempt history."""
    call_count = 0

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Click button"
            self.history: list[Any] = []

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                raise Exception("Element not found")

            return MockResult()

        def add_new_task(self, new_task: str) -> None:
            self.task = new_task

        def stop(self) -> None:
            pass

    config = _base_config(tmp_path, max_retries=2)
    wrapped = wrap(MockAgent(), config)
    await wrapped.run()

    # Find the saved JSON file
    runs_dir = tmp_path / "runs"
    json_files = list(runs_dir.glob("*.json"))
    assert len(json_files) == 1

    data = json.loads(json_files[0].read_text())
    assert "attempts" in data
    assert data["adaptive_retry_used"] is True
    # JSON is saved before the success entry is appended, so only
    # the failed attempt appears in the file.
    assert data["total_attempts"] >= 1
    assert data["attempts"][0]["diagnosis"] is not None
    assert data["attempts"][0]["status"] == "failed"

    # But the in-memory property includes the success entry too
    assert len(wrapped.attempt_history) == 2
    assert wrapped.attempt_history[1]["status"] == "completed"
    assert wrapped.attempt_history[1]["diagnosis"] is None


# ---------------------------------------------------------------------------
# Test 8: Mid-run intervention injects hints
# ---------------------------------------------------------------------------


async def test_mid_run_hint_injection(tmp_path: Path):
    """After 2 consecutive step failures within one run, a hint should be injected."""
    hints_injected: list[str] = []

    class MockMessageManager:
        def add_plan(self, msg: str) -> None:
            hints_injected.append(msg)

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Navigate and click"
            self.history: list[Any] = []
            self._message_manager = MockMessageManager()

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            # Simulate 3 steps: success, fail, fail
            steps = [
                _make_history_step(next_goal="Navigate to page", error=None),
                _make_history_step(next_goal="Click element", error="Element not found"),
                _make_history_step(next_goal="Click element", error="Element not found"),
            ]

            for step in steps:
                self.history.append(step)
                if on_step_end:
                    await on_step_end(self)

            return MockResult()

        def stop(self) -> None:
            pass

    agent = MockAgent()
    # max_retries=0 so we only run once (testing mid-run, not between-retry)
    wrapped = wrap(agent, _base_config(tmp_path, max_retries=0))

    await wrapped.run()

    # After 2 consecutive failures, a hint should have been injected
    assert len(hints_injected) >= 1
    hint_text = hints_injected[0].lower()
    assert "different" in hint_text or "selector" in hint_text


# ---------------------------------------------------------------------------
# Test 9: Environment changes are applied
# ---------------------------------------------------------------------------


def test_fresh_browser_on_anti_bot():
    """Anti-bot diagnosis should trigger fresh_browser in recovery plan."""
    analyzer = FailureAnalyzer(enable_llm=False)
    # Use "403 Forbidden" without "cloudflare"/"captcha"/"challenge" to match
    # the access_blocked rule (is_retryable=True) instead of the captcha rule.
    diagnosis = asyncio.run(analyzer.analyze(
        task_description="Fill form",
        steps=[],
        error="403 Forbidden - automated bot access blocked",
    ))

    router = RecoveryRouter()
    plan = router.plan_recovery(
        original_task="Fill form",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert plan.fresh_browser is True or plan.stealth_mode is True
    assert plan.wait_seconds > 0


# ---------------------------------------------------------------------------
# Test 10: extend_system_message populated for agent_loop
# ---------------------------------------------------------------------------


def test_system_message_override_for_loop():
    """Agent loop diagnosis should produce an extend_system_message."""
    diagnosis = FailureDiagnosis(
        category=FailureCategory.AGENT_LOOP,
        root_cause="Agent repeated click(#next) 5 times",
        retry_hint="Try a different navigation approach",
    )

    router = RecoveryRouter()
    plan = router.plan_recovery("Do task", diagnosis, attempt_number=1, max_attempts=3)

    assert plan.extend_system_message != ""
    msg_lower = plan.extend_system_message.lower()
    assert "loop" in msg_lower or "different" in msg_lower
    assert plan.reduce_max_actions is True


# ---------------------------------------------------------------------------
# Test 11: Partial enrichment on failure
# ---------------------------------------------------------------------------


async def test_steps_available_for_diagnosis_after_failure(tmp_path: Path):
    """When agent fails, _enrich_steps_partial should populate steps for diagnosis."""

    class MockAgent:
        def __init__(self) -> None:
            self.task = "Click button"
            # Pre-populate history with a failed step in browser_use format
            self.history = [
                _make_history_step(
                    action_name="ClickElementAction",
                    next_goal="Click the submit button",
                    error="Element not found",
                ),
            ]

        async def run(self, max_steps: int = 100, on_step_end: Any = None, **kw: Any) -> Any:
            raise Exception("Element not found")

        def stop(self) -> None:
            pass

    wrapped = wrap(MockAgent(), _base_config(tmp_path, max_retries=0))

    with pytest.raises(Exception, match="Element not found"):
        await wrapped.run()

    # Diagnosis should have had access to step data
    assert len(wrapped.attempt_history) >= 1
    diag = wrapped.attempt_history[0].get("diagnosis")
    assert diag is not None
    # With step data available, diagnosis should be specific
    assert diag["category"] == "element_interaction"
    assert diag["confidence"] > 0
