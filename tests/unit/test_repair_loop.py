"""Tests for workers.reliability.repair_loop — three-phase repair logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.shared_types import (
    FailureClass,
    Observation,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.playbooks import REPAIR_PLAYBOOK, RepairAction
from workers.reliability.repair_loop import run_repair


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(
    url: str = "https://example.com",
    title: str = "Example",
    dom_hash: str | None = None,
) -> Observation:
    return Observation(url=url, title=title, dom_hash=dom_hash)


def _result(
    success: bool = False,
    obs: Observation | None = None,
    error_code: str | None = None,
    error: str | None = None,
) -> StepResult:
    return StepResult(
        success=success,
        observation=obs or _obs(),
        error_code=error_code,
        error=error,
    )


def _outcome(
    verdict: ValidatorVerdict = ValidatorVerdict.FAIL_UI,
    failure_class: str | None = None,
    evidence: dict[str, Any] | None = None,
    message: str = "",
) -> ValidatorOutcome:
    return ValidatorOutcome(
        verdict=verdict,
        failure_class=failure_class,
        evidence=evidence or {},
        message=message,
    )


@dataclass
class _SubGoal:
    """Satisfies the SubGoal protocol."""
    description: str = "Click the submit button"
    attempts: int = 0


def _mock_backend(
    supports_single_step: bool = True,
    observation: Observation | None = None,
) -> MagicMock:
    backend = MagicMock()
    backend.capabilities = MagicMock()
    backend.capabilities.supports_single_step = supports_single_step
    backend.get_observation = AsyncMock(return_value=observation or _obs())
    backend.execute_step = AsyncMock(return_value=_result(success=True))
    return backend


def _mock_planner() -> MagicMock:
    planner = MagicMock()
    planner.replan = AsyncMock(return_value=None)
    return planner


def _mock_validator(outcomes: list[ValidatorOutcome] | None = None) -> MagicMock:
    validator = MagicMock()
    if outcomes:
        validator.validate = AsyncMock(side_effect=outcomes)
    else:
        validator.validate = AsyncMock(
            return_value=_outcome(ValidatorVerdict.PASS),
        )
    return validator


# ---------------------------------------------------------------------------
# TestDeterministicPatches
# ---------------------------------------------------------------------------


class TestDeterministicPatches:
    """Phase 2 — deterministic playbook patches."""

    async def test_patches_tried_in_playbook_order(self) -> None:
        """ELEMENT_NOT_FOUND: scroll_search → wait_stability → broaden → vision, in order.

        ELEMENT_NOT_FOUND produces: scroll, wait(2000), wait(500+broaden), wait(500+vision)
        — three of four are "wait" actions so we compare (action, value, metadata) tuples,
        not just action strings.
        """
        expected_actions = REPAIR_PLAYBOOK[FailureClass.ELEMENT_NOT_FOUND]
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        # Fail on all patches so we observe every execute_step call
        validator = _mock_validator([
            _outcome(ValidatorVerdict.FAIL_UI) for _ in expected_actions
        ])
        planner = _mock_planner()
        backend = _mock_backend()

        await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert backend.execute_step.call_count == len(expected_actions)

        # Build expected (action, value, metadata) tuples from the playbook using the
        # same helper run_repair uses, so the assertion is exact, not approximate.
        from workers.reliability.playbooks import repair_action_to_intent as _to_intent
        context = {"current_url": "https://example.com", "original_target": {}, "login_url": ""}
        for i, action in enumerate(expected_actions):
            expected_intent = _to_intent(action, context)
            call_intent: StepIntent = backend.execute_step.call_args_list[i][0][0]
            assert (call_intent.action, call_intent.value, call_intent.metadata) == (
                expected_intent.action,
                expected_intent.value,
                expected_intent.metadata,
            ), f"Patch {i} ({action!r}) produced wrong intent"

    async def test_first_passing_patch_returns_immediately(self) -> None:
        """When the first patch succeeds, stop and return PASS."""
        pass_outcome = _outcome(ValidatorVerdict.PASS)
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        validator = _mock_validator([pass_outcome])
        backend = _mock_backend()
        planner = _mock_planner()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert result is not None
        assert result.verdict == ValidatorVerdict.PASS
        assert backend.execute_step.call_count == 1
        planner.replan.assert_not_called()

    async def test_all_patches_fail_falls_through_to_cognitive(self) -> None:
        """When all deterministic patches fail, cognitive patch (replan) is tried."""
        patches = REPAIR_PLAYBOOK[FailureClass.STUCK]
        fail = _outcome(ValidatorVerdict.FAIL_STUCK, evidence={
            "observation": _obs(dom_hash="same"),
        })
        validator = _mock_validator([
            _outcome(ValidatorVerdict.FAIL_UI) for _ in patches
        ])
        planner = _mock_planner()
        backend = _mock_backend()

        result = await run_repair(
            fail, _SubGoal(), backend, planner, validator,
            previous_dom_hash="same",
        )

        assert backend.execute_step.call_count == len(patches)
        planner.replan.assert_called_once()
        assert result is not None
        assert result.verdict == ValidatorVerdict.UNCERTAIN

    async def test_execute_step_exception_continues_to_next(self) -> None:
        """If execute_step raises, skip to next patch."""
        patches = REPAIR_PLAYBOOK[FailureClass.ELEMENT_NOT_FOUND]
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        # First call raises, rest succeed with PASS on second
        backend = _mock_backend()
        backend.execute_step = AsyncMock(
            side_effect=[RuntimeError("boom")] + [_result(success=True)] * (len(patches) - 1),
        )
        validator = _mock_validator([_outcome(ValidatorVerdict.PASS)])
        planner = _mock_planner()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert result is not None
        assert result.verdict == ValidatorVerdict.PASS
        # First call raised, second call succeeded
        assert backend.execute_step.call_count == 2


# ---------------------------------------------------------------------------
# TestEscalateHuman
# ---------------------------------------------------------------------------


class TestEscalateHuman:
    """ESCALATE_HUMAN returns FAIL_POLICY without executing on backend."""

    async def test_captcha_returns_fail_policy(self) -> None:
        """CAPTCHA_CHALLENGE playbook is [ESCALATE_HUMAN] — must not call backend."""
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "observation": _obs(url="https://example.com/captcha"),
        })
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert result is not None
        assert result.verdict == ValidatorVerdict.FAIL_POLICY
        assert result.evidence.get("escalation") is True
        assert "captcha_challenge" in result.message.lower()
        backend.execute_step.assert_not_called()
        planner.replan.assert_not_called()

    async def test_anti_bot_escalates_after_refresh_fails(self) -> None:
        """ANTI_BOT_BLOCKED: [REFRESH_PAGE, ESCALATE_HUMAN] — tries refresh first."""
        fail = _outcome(ValidatorVerdict.FAIL_NETWORK, evidence={
            "observation": _obs(url="https://example.com/blocked"),
        })
        validator = _mock_validator([_outcome(ValidatorVerdict.FAIL_UI)])
        backend = _mock_backend()
        planner = _mock_planner()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        # refresh was tried once
        assert backend.execute_step.call_count == 1
        # then escalation returned
        assert result is not None
        assert result.verdict == ValidatorVerdict.FAIL_POLICY
        assert result.evidence.get("escalation") is True


# ---------------------------------------------------------------------------
# TestCognitivePatch
# ---------------------------------------------------------------------------


class TestCognitivePatch:
    """Phase 3 — cognitive patch via planner.replan()."""

    async def test_cognitive_success_returns_uncertain(self) -> None:
        """After deterministic patches exhausted, replan succeeds → UNCERTAIN."""
        fail = _outcome(ValidatorVerdict.FAIL_GOAL, evidence={
            "result": _result(),
        })
        planner = _mock_planner()
        backend = _mock_backend()
        validator = _mock_validator()

        # GOAL_NOT_MET has empty playbook → goes straight to cognitive
        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        planner.replan.assert_called_once()
        assert result is not None
        assert result.verdict == ValidatorVerdict.UNCERTAIN
        assert "goal_not_met" in result.message

    async def test_replan_exception_returns_none(self) -> None:
        """If planner.replan() raises, repair is exhausted → None."""
        fail = _outcome(ValidatorVerdict.FAIL_GOAL, evidence={
            "result": _result(),
        })
        planner = _mock_planner()
        planner.replan = AsyncMock(side_effect=RuntimeError("LLM down"))
        backend = _mock_backend()
        validator = _mock_validator()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert result is None

    async def test_replan_receives_correct_args(self) -> None:
        """planner.replan called with (subgoal, outcome, failure_class.value)."""
        fail = _outcome(ValidatorVerdict.FAIL_GOAL, evidence={
            "result": _result(),
        })
        subgoal = _SubGoal(description="Fill in the form")
        planner = _mock_planner()
        backend = _mock_backend()
        validator = _mock_validator()

        await run_repair(fail, subgoal, backend, planner, validator)

        args = planner.replan.call_args[0]
        assert args[0] is subgoal
        assert args[1] is fail
        assert isinstance(args[2], str)  # failure_class value


# ---------------------------------------------------------------------------
# TestCircuitBreakerIntegration
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    """Circuit breaker stops repair early when thresholds exceeded."""

    async def test_tripped_breaker_returns_none(self) -> None:
        """Pre-loaded breaker at threshold → returns None immediately."""
        cb = CircuitBreaker(max_same_class=2)
        cb.record_failure(FailureClass.ELEMENT_NOT_FOUND)
        # Next record_failure in run_repair will hit 2 → should_stop

        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator()

        result = await run_repair(
            fail, _SubGoal(), backend, planner, validator,
            circuit_breaker=cb,
        )

        assert result is None
        backend.execute_step.assert_not_called()
        planner.replan.assert_not_called()

    async def test_failure_recorded_in_breaker(self) -> None:
        """run_repair records the detected failure class in the circuit breaker."""
        cb = CircuitBreaker(max_same_class=10, max_total_failures=10)
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator([_outcome(ValidatorVerdict.PASS)])

        await run_repair(
            fail, _SubGoal(), backend, planner, validator,
            circuit_breaker=cb,
        )

        assert cb.dominant_failure() == FailureClass.ELEMENT_NOT_FOUND.value


# ---------------------------------------------------------------------------
# TestDelegationOnlyBackend
# ---------------------------------------------------------------------------


class TestDelegationOnlyBackend:
    """supports_single_step=False skips deterministic, goes to cognitive."""

    async def test_skips_deterministic_goes_to_cognitive(self) -> None:
        """Backend without single-step skips all playbook patches."""
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        backend = _mock_backend(supports_single_step=False)
        planner = _mock_planner()
        validator = _mock_validator()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        backend.execute_step.assert_not_called()
        planner.replan.assert_called_once()
        assert result is not None
        assert result.verdict == ValidatorVerdict.UNCERTAIN

    async def test_delegation_cognitive_failure_returns_none(self) -> None:
        """Delegation backend + replan failure → None."""
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "result": _result(error_code="element_not_found"),
        })
        backend = _mock_backend(supports_single_step=False)
        planner = _mock_planner()
        planner.replan = AsyncMock(side_effect=RuntimeError("nope"))
        validator = _mock_validator()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert result is None

    async def test_delegation_backend_still_escalates_human(self) -> None:
        """ESCALATE_HUMAN is checked before supports_single_step — fires even on delegation backends.

        CAPTCHA_CHALLENGE playbook is [ESCALATE_HUMAN]. A delegation backend must still
        return FAIL_POLICY rather than falling through to cognitive replan.
        """
        # URL triggers detect_from_url → CAPTCHA_CHALLENGE → [ESCALATE_HUMAN] playbook
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "observation": _obs(url="https://example.com/captcha"),
        })
        backend = _mock_backend(supports_single_step=False)
        planner = _mock_planner()
        validator = _mock_validator()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        assert result is not None
        assert result.verdict == ValidatorVerdict.FAIL_POLICY
        assert result.evidence.get("escalation") is True
        backend.execute_step.assert_not_called()
        planner.replan.assert_not_called()


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions and optional parameters."""

    async def test_empty_playbook_goes_to_cognitive(self) -> None:
        """GOAL_NOT_MET has empty playbook → straight to cognitive."""
        assert REPAIR_PLAYBOOK[FailureClass.GOAL_NOT_MET] == []
        fail = _outcome(ValidatorVerdict.FAIL_GOAL, evidence={
            "result": _result(),
        })
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator()

        result = await run_repair(fail, _SubGoal(), backend, planner, validator)

        backend.execute_step.assert_not_called()
        planner.replan.assert_called_once()
        assert result is not None
        assert result.verdict == ValidatorVerdict.UNCERTAIN

    async def test_no_circuit_breaker_still_works(self) -> None:
        """circuit_breaker=None should not raise."""
        fail = _outcome(ValidatorVerdict.FAIL_GOAL, evidence={
            "result": _result(),
        })
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator()

        result = await run_repair(
            fail, _SubGoal(), backend, planner, validator,
            circuit_breaker=None,
        )

        assert result is not None

    async def test_observation_fetched_from_backend_when_not_in_evidence(self) -> None:
        """If outcome.evidence has no observation, get_observation() is called."""
        fail = _outcome(ValidatorVerdict.FAIL_GOAL)  # no evidence
        obs = _obs(url="https://fetched.com")
        backend = _mock_backend(observation=obs)
        planner = _mock_planner()
        validator = _mock_validator()

        await run_repair(fail, _SubGoal(), backend, planner, validator)

        backend.get_observation.assert_called_once()

    async def test_observation_from_evidence_skips_backend_fetch(self) -> None:
        """If outcome.evidence has an observation, skip backend.get_observation()."""
        obs = _obs(url="https://evidence.com")
        fail = _outcome(ValidatorVerdict.FAIL_UI, evidence={
            "observation": obs,
            "result": _result(error_code="element_not_found", obs=obs),
        })
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator([_outcome(ValidatorVerdict.PASS)])

        await run_repair(fail, _SubGoal(), backend, planner, validator)

        backend.get_observation.assert_not_called()

    async def test_memory_store_exception_does_not_abort_repair(self) -> None:
        """If memory_store.get_known_fixes raises, repair continues normally."""
        fail = _outcome(ValidatorVerdict.FAIL_GOAL, evidence={
            "result": _result(),
        })
        memory_store = MagicMock()
        memory_store.get_known_fixes = AsyncMock(side_effect=ConnectionError("store down"))
        backend = _mock_backend()
        planner = _mock_planner()
        validator = _mock_validator()

        # Should not propagate the exception — cognitive patch still runs
        result = await run_repair(
            fail, _SubGoal(), backend, planner, validator,
            memory_store=memory_store,
        )

        memory_store.get_known_fixes.assert_called_once()
        assert result is not None
        assert result.verdict == ValidatorVerdict.UNCERTAIN
