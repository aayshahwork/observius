"""Tests for workers.reliability.repair_loop — self-healing repair logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.shared_types import (
    FailureClass,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.playbooks import RepairStrategy
from workers.reliability.repair_loop import run_repair


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outcome(
    check_name: str = "",
    message: str = "",
    verdict: ValidatorVerdict = ValidatorVerdict.FAIL,
) -> ValidatorOutcome:
    return ValidatorOutcome(
        verdict=verdict,
        check_name=check_name,
        message=message,
    )


@dataclass
class _SubGoal:
    """Satisfies the SubGoal protocol."""
    id: str = "sg-test"
    description: str = "Click the submit button"
    success_criteria: str = "Button clicked"
    status: str = "active"


def _mock_backend() -> MagicMock:
    backend = MagicMock()
    backend.execute_step = AsyncMock(return_value=None)
    backend.execute_goal = AsyncMock(return_value=None)
    return backend


def _mock_planner() -> MagicMock:
    planner = MagicMock()
    return planner


def _mock_validator() -> MagicMock:
    validator = MagicMock()
    return validator


def _mock_episodic_memory(known_fixes: list[dict] | None = None) -> MagicMock:
    mem = MagicMock()
    mem.get_known_fixes = AsyncMock(return_value=known_fixes or [])
    mem.record_failure_fix = AsyncMock()
    return mem


# ---------------------------------------------------------------------------
# Basic classification and stamping
# ---------------------------------------------------------------------------


class TestClassificationAndStamping:
    async def test_outcome_failure_class_is_stamped(self) -> None:
        """run_repair should stamp outcome.failure_class."""
        outcome = _outcome(message="browser crashed")
        mem = _mock_episodic_memory()

        await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert outcome.failure_class == FailureClass.BROWSER_CRASH.value

    async def test_patch_applied_stamped_on_success(self) -> None:
        """When repair succeeds, outcome.patch_applied is set."""
        # LLM_OVERLOADED → WAIT_AND_RETRY (always succeeds — just waits)
        outcome = _outcome(message="server overloaded")
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is True
        assert outcome.patch_applied == RepairStrategy.WAIT_AND_RETRY.value

    async def test_patch_applied_not_stamped_on_failure(self) -> None:
        """When repair fails (e.g. abort), outcome.patch_applied stays None."""
        # BROWSER_CRASH → ABORT (returns False)
        outcome = _outcome(message="browser crashed")
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is False
        assert outcome.patch_applied is None


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    async def test_tripped_breaker_returns_false(self) -> None:
        """Pre-loaded breaker → returns False immediately."""
        cb = CircuitBreaker(max_consecutive=1)
        cb.record_failure("browser")  # trips the breaker for "browser" group

        outcome = _outcome(message="browser crashed")  # → BROWSER_CRASH, group="browser"
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            circuit_breaker=cb,
            episodic_memory=mem,
        )

        assert result is False

    async def test_success_resets_breaker(self) -> None:
        """Successful repair resets the breaker for that group."""
        cb = CircuitBreaker(max_consecutive=3)
        outcome = _outcome(message="server overloaded")  # → LLM_OVERLOADED, group="llm"
        mem = _mock_episodic_memory()

        await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            circuit_breaker=cb,
            episodic_memory=mem,
        )

        # Breaker should have recorded success, not failure
        assert cb.allow_attempt("llm")

    async def test_failure_recorded_in_breaker(self) -> None:
        """Failed repair records failure in breaker."""
        cb = CircuitBreaker(max_consecutive=10, max_total_failures=10)
        outcome = _outcome(message="browser crashed")  # → ABORT → success=False
        mem = _mock_episodic_memory()

        await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            circuit_breaker=cb,
            episodic_memory=mem,
        )

        # One failure recorded for the "browser" group
        assert cb._group_counts.get("browser", 0) == 1


# ---------------------------------------------------------------------------
# Episodic memory integration
# ---------------------------------------------------------------------------


class TestEpisodicMemoryIntegration:
    async def test_outcome_recorded_in_memory(self) -> None:
        """run_repair should call episodic_memory.record_failure_fix."""
        outcome = _outcome(message="server overloaded")
        mem = _mock_episodic_memory()

        await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem, domain="example.com",
        )

        mem.record_failure_fix.assert_called_once()
        call_kwargs = mem.record_failure_fix.call_args
        assert call_kwargs[0][0] == FailureClass.LLM_OVERLOADED.value
        assert call_kwargs[1]["domain"] == "example.com"

    async def test_known_fixes_prioritised(self) -> None:
        """Known successful fixes from memory should be tried first."""
        outcome = _outcome(message="element not found")  # → BROWSER_ELEMENT_MISSING
        # Memory says scroll_and_retry worked before
        mem = _mock_episodic_memory(known_fixes=[{
            "repair_strategy": "scroll_and_retry",
            "successes": 3,
            "attempts": 4,
            "description": "worked before",
        }])

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is True  # scroll_and_retry succeeds

    async def test_memory_failure_falls_back_to_static_playbook(self) -> None:
        """If episodic memory query fails, static playbook is used."""
        outcome = _outcome(message="server overloaded")
        mem = _mock_episodic_memory()
        mem.get_known_fixes = AsyncMock(side_effect=ConnectionError("store down"))

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        # Still succeeds — WAIT_AND_RETRY from static playbook
        assert result is True


# ---------------------------------------------------------------------------
# Repair action execution
# ---------------------------------------------------------------------------


class TestRepairActions:
    async def test_wait_and_retry_returns_true(self) -> None:
        outcome = _outcome(message="rate limit exceeded 429")  # → LLM_RATE_LIMITED
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is True

    async def test_abort_returns_false(self) -> None:
        outcome = _outcome(message="API key invalid 401 Anthropic")  # → LLM_AUTH_FAILED → ABORT
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is False

    async def test_replan_returns_false(self) -> None:
        outcome = _outcome(message="bad request 400 Anthropic")  # → LLM_BAD_REQUEST → REPLAN
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is False

    async def test_scroll_and_retry_calls_backend(self) -> None:
        outcome = _outcome(message="element not found")  # → BROWSER_ELEMENT_MISSING → SCROLL_AND_RETRY
        backend = _mock_backend()
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), backend, _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is True
        backend.execute_step.assert_called_once()

    async def test_dismiss_overlay_calls_execute_goal(self) -> None:
        outcome = _outcome(message="element blocked by overlay")  # → BROWSER_ELEMENT_BLOCKED → DISMISS_OVERLAY
        backend = _mock_backend()
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), backend, _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is True
        backend.execute_goal.assert_called_once()

    async def test_backend_exception_returns_false(self) -> None:
        """If backend.execute_step raises, repair returns False."""
        outcome = _outcome(message="element not found")  # → SCROLL_AND_RETRY
        backend = _mock_backend()
        backend.execute_step = AsyncMock(side_effect=RuntimeError("crash"))
        backend.execute_goal = AsyncMock(side_effect=RuntimeError("crash"))
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), backend, _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_no_circuit_breaker_works(self) -> None:
        """circuit_breaker=None should not raise."""
        outcome = _outcome(message="server overloaded")
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            circuit_breaker=None,
            episodic_memory=mem,
        )

        assert result is True

    async def test_unknown_failure_replans(self) -> None:
        """Unrecognised failure → UNKNOWN → REPLAN → False."""
        outcome = _outcome(message="something unrecognised")
        mem = _mock_episodic_memory()

        result = await run_repair(
            outcome, _SubGoal(), _mock_backend(), _mock_planner(), _mock_validator(),
            episodic_memory=mem,
        )

        assert result is False
        assert outcome.failure_class == FailureClass.UNKNOWN.value
