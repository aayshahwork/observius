"""
tests/unit/test_reliability.py — Tests for workers/reliability/.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.detectors import classify_outcome
from workers.reliability.playbooks import (
    RepairAction,
    RepairStrategy,
    get_playbook,
)
from workers.reliability.repair_loop import run_repair
from workers.shared_types import FailureClass, ValidatorOutcome, ValidatorVerdict


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_allow_attempt_initially(self):
        cb = CircuitBreaker(max_consecutive=3)
        assert cb.allow_attempt("browser") is True

    def test_allow_after_one_failure(self):
        cb = CircuitBreaker(max_consecutive=3)
        cb.record_failure("browser")
        assert cb.allow_attempt("browser") is True

    def test_trips_after_max_consecutive(self):
        cb = CircuitBreaker(max_consecutive=3)
        cb.record_failure("browser")
        cb.record_failure("browser")
        cb.record_failure("browser")
        assert cb.allow_attempt("browser") is False

    def test_groups_are_independent(self):
        cb = CircuitBreaker(max_consecutive=2)
        cb.record_failure("browser")
        cb.record_failure("browser")
        assert cb.allow_attempt("browser") is False
        assert cb.allow_attempt("llm") is True

    def test_record_success_resets(self):
        cb = CircuitBreaker(max_consecutive=3)
        cb.record_failure("browser")
        cb.record_failure("browser")
        cb.record_success("browser")
        assert cb.allow_attempt("browser") is True

    def test_reset_clears_all(self):
        cb = CircuitBreaker(max_consecutive=2)
        cb.record_failure("browser")
        cb.record_failure("browser")
        cb.record_failure("llm")
        cb.record_failure("llm")
        cb.reset()
        assert cb.allow_attempt("browser") is True
        assert cb.allow_attempt("llm") is True

    def test_default_max_consecutive(self):
        cb = CircuitBreaker()
        assert cb.max_consecutive == 3


# ---------------------------------------------------------------------------
# classify_outcome
# ---------------------------------------------------------------------------


class TestClassifyOutcome:
    def test_auth_redirect_check_name(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="auth_redirect",
            message="Redirected to login",
        )
        assert classify_outcome(outcome) == FailureClass.AUTH_REQUIRED

    def test_error_page_url_check_name(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="error_page_url",
            message="404 page",
        )
        assert classify_outcome(outcome) == FailureClass.BROWSER_NAVIGATION

    def test_timeout_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="Timeout waiting for selector",
        )
        assert classify_outcome(outcome) == FailureClass.BROWSER_TIMEOUT

    def test_element_not_found_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="Element not found: #submit",
        )
        assert classify_outcome(outcome) == FailureClass.BROWSER_ELEMENT_MISSING

    def test_rate_limit_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            message="429 too many requests",
        )
        assert classify_outcome(outcome) == FailureClass.LLM_RATE_LIMITED

    def test_captcha_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            message="recaptcha challenge detected",
        )
        assert classify_outcome(outcome) == FailureClass.ANTI_BOT_CAPTCHA

    def test_unknown_fallback(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            message="something completely unexpected",
        )
        assert classify_outcome(outcome) == FailureClass.UNKNOWN

    def test_click_intercepted_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            message="Element click intercepted by overlay",
        )
        assert classify_outcome(outcome) == FailureClass.BROWSER_CLICK_INTERCEPTED

    def test_overloaded_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            message="API is overloaded, 529",
        )
        assert classify_outcome(outcome) == FailureClass.LLM_OVERLOADED

    def test_stuck_loop_message(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            message="Stuck in repeated action loop",
        )
        assert classify_outcome(outcome) == FailureClass.AGENT_LOOP

    def test_actual_field_used(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="",
            actual="connection refused ECONNREFUSED",
        )
        assert classify_outcome(outcome) == FailureClass.NETWORK_CONNECTION


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------


class TestPlaybooks:
    def test_llm_overloaded_is_wait(self):
        playbook = get_playbook(FailureClass.LLM_OVERLOADED)
        assert len(playbook) >= 1
        assert playbook[0].strategy == RepairStrategy.WAIT_AND_RETRY
        assert playbook[0].wait_seconds > 0

    def test_browser_crash_is_abort(self):
        playbook = get_playbook(FailureClass.BROWSER_CRASH)
        assert playbook[0].strategy == RepairStrategy.ABORT

    def test_element_missing_is_scroll(self):
        playbook = get_playbook(FailureClass.BROWSER_ELEMENT_MISSING)
        assert playbook[0].strategy == RepairStrategy.SCROLL_AND_RETRY

    def test_element_blocked_is_dismiss(self):
        playbook = get_playbook(FailureClass.BROWSER_ELEMENT_BLOCKED)
        assert playbook[0].strategy == RepairStrategy.DISMISS_OVERLAY

    def test_agent_loop_is_refresh(self):
        playbook = get_playbook(FailureClass.AGENT_LOOP)
        assert playbook[0].strategy == RepairStrategy.REFRESH_PAGE

    def test_unknown_is_replan(self):
        playbook = get_playbook(FailureClass.UNKNOWN)
        assert playbook[0].strategy == RepairStrategy.REPLAN

    def test_anti_bot_blocked_is_abort(self):
        playbook = get_playbook(FailureClass.ANTI_BOT_BLOCKED)
        assert playbook[0].strategy == RepairStrategy.ABORT

    def test_captcha_is_replan(self):
        playbook = get_playbook(FailureClass.ANTI_BOT_CAPTCHA)
        assert playbook[0].strategy == RepairStrategy.REPLAN

    def test_all_failure_classes_have_playbook(self):
        for fc in FailureClass:
            playbook = get_playbook(fc)
            assert len(playbook) >= 1, f"No playbook for {fc}"

    def test_repair_action_frozen(self):
        action = RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=5.0)
        with pytest.raises(AttributeError):
            action.strategy = RepairStrategy.ABORT  # type: ignore[misc]

    def test_repair_strategy_values(self):
        expected = {
            "wait_and_retry", "refresh_page", "scroll_and_retry",
            "dismiss_overlay", "re_navigate", "replan", "abort",
        }
        assert {s.value for s in RepairStrategy} == expected


# ---------------------------------------------------------------------------
# run_repair
# ---------------------------------------------------------------------------


class TestRunRepair:
    def _make_outcome(self, message: str = "timeout", check_name: str = "step_error"):
        return ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name=check_name,
            message=message,
        )

    def _make_subgoal(self):
        from workers.pav.types import SubGoal
        return SubGoal(id="sg_1", description="Click button", success_criteria="Done")

    def _make_mocks(self):
        backend = AsyncMock()
        backend.execute_step = AsyncMock()
        backend.execute_goal = AsyncMock(return_value=[])
        planner = AsyncMock()
        validator = AsyncMock()
        return backend, planner, validator

    def _make_memory(self):
        mem = AsyncMock()
        mem.get_known_fixes = AsyncMock(return_value=[])
        mem.record_failure_fix = AsyncMock()
        return mem

    async def test_wait_and_retry_returns_true(self):
        outcome = self._make_outcome("timeout waiting for element")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is True
        assert outcome.failure_class == "browser_timeout"
        assert outcome.patch_applied == "wait_and_retry"

    async def test_abort_returns_false(self):
        outcome = self._make_outcome("browser crashed, target closed")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is False
        assert outcome.failure_class == "browser_crash"
        assert outcome.patch_applied is None

    async def test_replan_returns_false(self):
        outcome = self._make_outcome("something completely unexpected xyz")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is False
        assert outcome.failure_class == "unknown"

    async def test_circuit_breaker_blocks(self):
        cb = CircuitBreaker(max_consecutive=1)
        cb.record_failure("browser")
        outcome = self._make_outcome("timeout error")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(
            outcome, sg, backend, planner, validator,
            circuit_breaker=cb, episodic_memory=self._make_memory(),
        )
        assert result is False

    async def test_circuit_breaker_records_success(self):
        cb = CircuitBreaker(max_consecutive=3)
        outcome = self._make_outcome("timeout error")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        await run_repair(outcome, sg, backend, planner, validator, circuit_breaker=cb, episodic_memory=self._make_memory())
        # wait_and_retry succeeded, so CB should have reset browser group
        assert cb.allow_attempt("browser") is True

    async def test_circuit_breaker_records_failure_on_abort(self):
        cb = CircuitBreaker(max_consecutive=3)
        outcome = self._make_outcome("browser crashed, target closed")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        await run_repair(outcome, sg, backend, planner, validator, circuit_breaker=cb, episodic_memory=self._make_memory())
        # abort returns False, CB should record failure
        assert cb._group_counts.get("browser", 0) == 1

    async def test_dismiss_overlay_calls_execute_goal(self):
        outcome = self._make_outcome("element blocked by overlay, not clickable")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is True
        backend.execute_goal.assert_called_once()
        assert "overlay" in backend.execute_goal.call_args[0][0].lower()

    async def test_scroll_calls_execute_step(self):
        outcome = self._make_outcome("element not found on page")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is True
        backend.execute_step.assert_called_once()

    async def test_refresh_page_calls_execute_step(self):
        outcome = self._make_outcome("stuck in repeated action loop detected")
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is True
        backend.execute_step.assert_called_once()

    async def test_auth_redirect_returns_replan(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="auth_redirect",
            message="Redirected to login",
        )
        sg = self._make_subgoal()
        backend, planner, validator = self._make_mocks()

        result = await run_repair(outcome, sg, backend, planner, validator, episodic_memory=self._make_memory())
        assert result is False
        assert outcome.failure_class == "auth_required"
