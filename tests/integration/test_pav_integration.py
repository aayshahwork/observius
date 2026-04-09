"""
tests/integration/test_pav_integration.py — Integration tests for the PAV loop.

Covers:
1. Mock BrowserUseBackend in delegation mode
2. Mock NativeAnthropicBackend in fine-grained mode
3. Repair loop triggers on simulated element_not_found failure
4. Circuit breaker stops after 3 repeated same-class failures
5. Budget enforcement stops loop when steps exhausted
6. PlanState advances through subgoals correctly
"""

from __future__ import annotations

import json
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.backends.protocol import BackendCapabilities
from workers.models import TaskConfig
from workers.pav.loop import run_pav_loop
from workers.pav.planner import Planner
from workers.pav.types import PlanState, SubGoal
from workers.pav.validator import Validator
from workers.shared_types import (
    Budget,
    Observation,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.detectors import classify_outcome
from workers.reliability.repair_loop import run_repair
from workers.reliability.playbooks import get_playbook, RepairStrategy
from workers.shared_types.taxonomy import FailureClass


# ---------------------------------------------------------------------------
# Mock backends
# ---------------------------------------------------------------------------


class MockDelegatingBackend:
    """Simulates BrowserUseBackend (goal-delegation only)."""

    capabilities = BackendCapabilities(
        supports_single_step=False,
        supports_goal_delegation=True,
    )

    def __init__(self, step_results: list[StepResult] | None = None):
        self._step_results = step_results or [
            StepResult(
                success=True,
                observation=Observation(url="https://example.com/done", page_title="Done"),
            )
        ]
        self._initialized = False

    @property
    def name(self) -> str:
        return "mock_delegating"

    async def initialize(self, config: dict) -> None:
        self._initialized = True

    async def execute_step(self, intent: StepIntent) -> StepResult:
        raise NotImplementedError("Delegating backend does not support single-step")

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        return list(self._step_results)

    async def get_observation(self) -> Observation:
        return Observation(url="https://example.com/done", page_title="Done")

    async def teardown(self) -> None:
        pass


class MockFinegrainedBackend:
    """Simulates NativeAnthropicBackend (fine-grained, single-step)."""

    capabilities = BackendCapabilities(
        supports_single_step=True,
        supports_goal_delegation=True,
    )

    def __init__(self, step_results: list[StepResult] | None = None):
        self._step_results = step_results or [
            StepResult(
                success=True,
                observation=Observation(url="https://example.com/done", page_title="Done"),
            )
        ]
        self._call_index = 0

    @property
    def name(self) -> str:
        return "mock_finegrained"

    async def initialize(self, config: dict) -> None:
        pass

    async def execute_step(self, intent: StepIntent) -> StepResult:
        idx = min(self._call_index, len(self._step_results) - 1)
        self._call_index += 1
        return self._step_results[idx]

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        return list(self._step_results)

    async def get_observation(self) -> Observation:
        return Observation(url="https://example.com/page", page_title="Page")

    async def teardown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_planner(subgoals_json: list[dict]) -> Planner:
    """Build a Planner whose LLM always returns the given subgoals."""
    mock_llm = AsyncMock()
    mock_llm.create.return_value = _make_llm_response(json.dumps(subgoals_json))
    return Planner(llm_client=mock_llm)


def _make_passing_validator() -> Validator:
    """Validator with no LLM — deterministic checks only."""
    return Validator(llm_client=None)


def _make_task_config() -> TaskConfig:
    return TaskConfig(url="https://example.com", task="Test task")


def _make_repair_fn(backend, planner, validator, circuit_breaker=None):
    """Create a repair_fn closure compatible with run_pav_loop.

    PAV loop calls: repair_fn(outcome, subgoal, backend, planner, validator).
    We accept those positional args but use our closed-over circuit_breaker.
    """
    async def repair_fn(outcome, subgoal, _backend, _planner, _validator):
        return await run_repair(
            outcome=outcome,
            subgoal=subgoal,
            backend=_backend,
            planner=_planner,
            validator=_validator,
            circuit_breaker=circuit_breaker,
        )
    return repair_fn


# ---------------------------------------------------------------------------
# 1. Mock BrowserUseBackend in delegation mode
# ---------------------------------------------------------------------------


class TestDelegationMode:
    @pytest.mark.asyncio
    async def test_delegation_completes_successfully(self):
        """A delegating backend runs execute_goal and the PAV loop completes."""
        backend = MockDelegatingBackend()
        planner = _make_planner([
            {"id": "sg_1", "description": "Do the thing", "success_criteria": "Done", "delegation_mode": True},
        ])
        validator = _make_passing_validator()
        budget = Budget(max_steps=10)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
        )

        assert result.success is True
        assert result.status == "completed"
        assert result.steps >= 1
        assert backend._initialized is True

    @pytest.mark.asyncio
    async def test_delegation_with_multiple_step_results(self):
        """Backend returns multiple step results from goal delegation."""
        backend = MockDelegatingBackend(step_results=[
            StepResult(success=True, tokens_in=100, tokens_out=50,
                       observation=Observation(url="https://example.com/step1")),
            StepResult(success=True, tokens_in=200, tokens_out=100,
                       observation=Observation(url="https://example.com/done", page_title="Done")),
        ])
        planner = _make_planner([
            {"id": "sg_1", "description": "Multi-step goal", "success_criteria": "Done", "delegation_mode": True},
        ])
        validator = _make_passing_validator()
        budget = Budget(max_steps=20)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
        )

        assert result.success is True
        assert result.steps == 2
        assert result.total_tokens_in == 300
        assert result.total_tokens_out == 150


# ---------------------------------------------------------------------------
# 2. Mock NativeAnthropicBackend in fine-grained mode
# ---------------------------------------------------------------------------


class TestFinegrainedMode:
    @pytest.mark.asyncio
    async def test_finegrained_completes_successfully(self):
        """A fine-grained backend runs execute_step and the loop completes."""
        backend = MockFinegrainedBackend()
        planner = _make_planner([
            {"id": "sg_1", "description": "Click button", "success_criteria": "Button clicked", "delegation_mode": False},
        ])

        intent_json = json.dumps({
            "action": "click",
            "target": {"strategy": "css_selector", "value": "#btn"},
            "value": "",
            "description": "Click the button",
        })
        planner.llm.create.side_effect = [
            planner.llm.create.return_value,  # create_plan
            _make_llm_response(intent_json),   # next_intent
        ]

        validator = _make_passing_validator()
        budget = Budget(max_steps=10)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
        )

        assert result.success is True
        assert result.status == "completed"
        assert result.steps >= 1

    @pytest.mark.asyncio
    async def test_finegrained_uses_execute_step_not_goal(self):
        """Fine-grained mode calls execute_step, NOT execute_goal."""
        backend = MockFinegrainedBackend()
        backend.execute_goal = AsyncMock(side_effect=AssertionError("Should not call execute_goal"))

        planner = _make_planner([
            {"id": "sg_1", "description": "Type text", "success_criteria": "Text entered", "delegation_mode": False},
        ])
        intent_json = json.dumps({
            "action": "type",
            "target": {"strategy": "css_selector", "value": "#input"},
            "value": "hello",
            "description": "Type hello",
        })
        planner.llm.create.side_effect = [
            planner.llm.create.return_value,
            _make_llm_response(intent_json),
        ]
        validator = _make_passing_validator()
        budget = Budget(max_steps=10)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
        )

        assert result.success is True


# ---------------------------------------------------------------------------
# 3. Repair loop triggers on simulated element_not_found failure
# ---------------------------------------------------------------------------


class TestRepairLoop:
    @pytest.mark.asyncio
    async def test_classify_outcome_element_not_found(self):
        """classify_outcome returns BROWSER_ELEMENT_MISSING for element errors."""
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="Step failed with error: Element not found: #btn",
            is_critical=True,
        )
        fc = classify_outcome(outcome)
        assert fc == FailureClass.BROWSER_ELEMENT_MISSING

    @pytest.mark.asyncio
    async def test_repair_returns_true_for_scroll_strategy(self):
        """run_repair returns True for BROWSER_ELEMENT_MISSING (scroll_and_retry)."""
        backend = MockFinegrainedBackend()
        planner = _make_planner([])
        validator = _make_passing_validator()

        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="Element not found: #missing-btn",
            is_critical=True,
        )
        subgoal = SubGoal(id="sg_1", description="Click btn", success_criteria="Clicked")

        repaired = await run_repair(
            outcome=outcome,
            subgoal=subgoal,
            backend=backend,
            planner=planner,
            validator=validator,
        )

        # BROWSER_ELEMENT_MISSING playbook → SCROLL_AND_RETRY → True
        assert repaired is True

    @pytest.mark.asyncio
    async def test_repair_fn_integrates_with_pav_loop(self):
        """Repair fn wrapping run_repair works with the PAV loop."""
        call_count = 0

        class FailThenSucceedBackend(MockDelegatingBackend):
            async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return [StepResult(success=False, error="Element not found: #missing-btn")]
                return [StepResult(
                    success=True,
                    observation=Observation(url="https://example.com/done", page_title="Done"),
                )]

        backend = FailThenSucceedBackend()
        planner = _make_planner([
            {"id": "sg_1", "description": "Click button", "success_criteria": "Clicked", "delegation_mode": True},
        ])
        replan_response = _make_llm_response(json.dumps({"action": "retry", "reason": "try again"}))
        original = planner.llm.create.return_value
        planner.llm.create.side_effect = [original, replan_response]

        validator = _make_passing_validator()
        budget = Budget(max_steps=20)

        repair_fn = _make_repair_fn(backend, planner, validator)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
            repair_fn=repair_fn,
        )

        # call 1: PAV execute_goal → fails (element not found)
        # call 2: repair action (SCROLL_AND_RETRY) calls execute_goal to scroll
        # call 3: PAV retries execute_goal → succeeds
        assert call_count == 3


# ---------------------------------------------------------------------------
# 4. Circuit breaker stops after 3 repeated same-class failures
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_circuit_breaker_trips_after_threshold(self):
        """Circuit breaker trips after max_consecutive same-group failures."""
        cb = CircuitBreaker(max_consecutive=3)

        assert cb.allow_attempt("browser") is True
        cb.record_failure("browser")

        assert cb.allow_attempt("browser") is True
        cb.record_failure("browser")

        assert cb.allow_attempt("browser") is True
        cb.record_failure("browser")

        # Now tripped
        assert cb.allow_attempt("browser") is False

    def test_circuit_breaker_different_groups_independent(self):
        """Different failure groups have independent counters."""
        cb = CircuitBreaker(max_consecutive=2)
        cb.record_failure("browser")
        cb.record_failure("network")
        cb.record_failure("browser")

        assert cb.allow_attempt("browser") is False
        assert cb.allow_attempt("network") is True

    def test_circuit_breaker_success_resets(self):
        """Recording success resets the counter for that group."""
        cb = CircuitBreaker(max_consecutive=3)
        cb.record_failure("browser")
        cb.record_failure("browser")
        cb.record_success("browser")
        cb.record_failure("browser")

        assert cb.allow_attempt("browser") is True  # Only 1 consecutive now

    @pytest.mark.asyncio
    async def test_run_repair_respects_circuit_breaker(self):
        """run_repair returns False when circuit breaker blocks the group."""
        backend = MockFinegrainedBackend()
        planner = _make_planner([])
        validator = _make_passing_validator()

        cb = CircuitBreaker(max_consecutive=3)
        # Pre-trip the breaker for "browser" group
        cb.record_failure("browser")
        cb.record_failure("browser")
        cb.record_failure("browser")

        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="Element not found: #btn",
            is_critical=True,
        )
        subgoal = SubGoal(id="sg_1", description="Click", success_criteria="Clicked")

        repaired = await run_repair(
            outcome=outcome,
            subgoal=subgoal,
            backend=backend,
            planner=planner,
            validator=validator,
            circuit_breaker=cb,
        )

        # BROWSER_ELEMENT_MISSING → group="browser" → breaker tripped → False
        assert repaired is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_stops_pav_loop(self):
        """PAV loop stops retrying when circuit breaker trips.

        Uses a backend that raises on execute_step (PAV loop catches this
        and returns FAIL). execute_goal also raises, so the SCROLL_AND_RETRY
        repair action fails and record_failure is called on the breaker.
        """
        execute_step_count = 0

        class AlwaysRaiseBackend(MockFinegrainedBackend):
            """Backend whose execute_step raises and execute_goal also raises."""
            async def execute_step(self, intent: StepIntent) -> StepResult:
                nonlocal execute_step_count
                execute_step_count += 1
                raise RuntimeError("Element not found: #btn")

            async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
                raise RuntimeError("Backend goal execution also fails")

        backend = AlwaysRaiseBackend()
        planner = _make_planner([
            {"id": "sg_1", "description": "Click button", "success_criteria": "Clicked", "delegation_mode": False},
        ])
        # Planner responses: create_plan + (next_intent + replan) for each of 3 attempts
        intent_json = json.dumps({
            "action": "click", "target": {"strategy": "css_selector", "value": "#btn"},
            "value": "", "description": "Click",
        })
        replan_json = json.dumps({"action": "retry", "reason": "try"})
        original = planner.llm.create.return_value
        planner.llm.create.side_effect = [
            original,                         # create_plan
            _make_llm_response(intent_json),  # next_intent attempt 1
            _make_llm_response(replan_json),  # replan after failure 1
            _make_llm_response(intent_json),  # next_intent attempt 2
            _make_llm_response(replan_json),  # replan after failure 2
            _make_llm_response(intent_json),  # next_intent attempt 3
            _make_llm_response(replan_json),  # replan after failure 3
        ]

        validator = _make_passing_validator()
        budget = Budget(max_steps=50)

        # SubGoal max_attempts=3 means repair_fn is called at attempts 1 and 2
        # (attempt 3 triggers mark_failed directly). So use max_consecutive=2
        # to ensure the breaker trips within the available repair calls.
        cb = CircuitBreaker(max_consecutive=2)
        repair_fn = _make_repair_fn(backend, planner, validator, cb)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
            repair_fn=repair_fn,
        )

        # The breaker should have accumulated failures for the "browser" group
        assert not cb.allow_attempt("browser")


# ---------------------------------------------------------------------------
# 5. Budget enforcement stops loop when steps exhausted
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    @pytest.mark.asyncio
    async def test_budget_stops_loop_on_step_limit(self):
        """PAV loop stops when step budget is exhausted."""
        backend = MockDelegatingBackend(step_results=[
            StepResult(success=True, observation=Observation(url="https://example.com/page")),
        ])

        planner = _make_planner([
            {"id": "sg_1", "description": "Step 1", "success_criteria": "Done 1", "delegation_mode": True},
            {"id": "sg_2", "description": "Step 2", "success_criteria": "Done 2", "delegation_mode": True},
            {"id": "sg_3", "description": "Step 3", "success_criteria": "Done 3", "delegation_mode": True},
        ])
        validator = _make_passing_validator()
        budget = Budget(max_steps=2)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
        )

        assert budget.steps_used == 2
        assert not budget.has_remaining()

    @pytest.mark.asyncio
    async def test_budget_cost_limit(self):
        """Budget tracks cost and stops when cost limit exceeded."""
        budget = Budget(max_cost_cents=10, max_steps=100)

        budget.record_step(cost_cents=4.0)
        assert budget.has_remaining()
        budget.record_step(cost_cents=4.0)
        assert budget.has_remaining()
        budget.record_step(cost_cents=3.0)  # Total: 11 > 10
        assert not budget.has_remaining()

    @pytest.mark.asyncio
    async def test_budget_remaining_properties(self):
        """Budget remaining_steps and remaining_cost_cents are accurate."""
        budget = Budget(max_cost_cents=100, max_steps=10)
        budget.record_step(cost_cents=25.0)
        budget.record_step(cost_cents=25.0)

        assert budget.remaining_steps == 8
        assert budget.remaining_cost_cents == 50.0
        assert budget.step_utilization == 0.2
        assert budget.cost_utilization == 0.5


# ---------------------------------------------------------------------------
# 6. PlanState advances through subgoals correctly
# ---------------------------------------------------------------------------


class TestPlanStateAdvancement:
    @pytest.mark.asyncio
    async def test_plan_advances_through_all_subgoals(self):
        """PAV loop advances through each subgoal in order."""
        backend = MockDelegatingBackend()
        planner = _make_planner([
            {"id": "sg_1", "description": "Step 1", "success_criteria": "Done 1", "delegation_mode": True},
            {"id": "sg_2", "description": "Step 2", "success_criteria": "Done 2", "delegation_mode": True},
            {"id": "sg_3", "description": "Step 3", "success_criteria": "Done 3", "delegation_mode": True},
        ])
        validator = _make_passing_validator()
        budget = Budget(max_steps=20)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
        )

        assert result.success is True
        assert result.steps == 3

    def test_planstate_sequential_advance(self):
        """PlanState cursor advances correctly through subgoals."""
        plan = PlanState(
            task_goal="Test",
            subgoals=[
                SubGoal(id="sg_1", description="A", success_criteria="A done"),
                SubGoal(id="sg_2", description="B", success_criteria="B done"),
                SubGoal(id="sg_3", description="C", success_criteria="C done"),
            ],
        )

        assert plan.current_index == 0
        assert plan.current_subgoal().id == "sg_1"
        assert not plan.is_complete()

        plan.advance()
        assert plan.current_index == 1
        assert plan.subgoals[0].status == "done"
        assert plan.current_subgoal().id == "sg_2"

        plan.advance()
        assert plan.current_index == 2
        assert plan.current_subgoal().id == "sg_3"

        plan.advance()
        assert plan.current_index == 3
        assert plan.current_subgoal() is None
        assert plan.is_complete()

    def test_planstate_failed_subgoal_detected_by_status_check(self):
        """A failed subgoal is detected by checking subgoal statuses.

        is_complete() returns True when cursor is past end (plan fully
        traversed). The success determination checks for failed subgoals
        separately — see _build_task_result().
        """
        plan = PlanState(
            task_goal="Test",
            subgoals=[
                SubGoal(id="sg_1", description="A", success_criteria="A done"),
                SubGoal(id="sg_2", description="B", success_criteria="B done"),
            ],
        )

        plan.advance()  # sg_1 done
        plan.mark_failed(plan.subgoals[1])
        plan.current_index = 2

        assert plan.is_complete() is True  # Cursor past end
        has_failures = any(sg.status == "failed" for sg in plan.subgoals)
        assert has_failures is True

    def test_planstate_skipped_subgoal_counts_as_complete(self):
        """Skipped subgoals count toward completion."""
        plan = PlanState(
            task_goal="Test",
            subgoals=[
                SubGoal(id="sg_1", description="A", success_criteria="A done"),
                SubGoal(id="sg_2", description="B", success_criteria="B done"),
            ],
        )

        plan.advance()
        plan.subgoals[1].status = "skipped"

        assert plan.is_complete() is True

    @pytest.mark.asyncio
    async def test_on_step_callback_fires(self):
        """The on_step callback fires for each step in the PAV loop."""
        steps_received: list[StepResult] = []

        backend = MockDelegatingBackend()
        planner = _make_planner([
            {"id": "sg_1", "description": "Do it", "success_criteria": "Done", "delegation_mode": True},
        ])
        validator = _make_passing_validator()
        budget = Budget(max_steps=10)

        result = await run_pav_loop(
            task_config=_make_task_config(),
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
            on_step=lambda sr: steps_received.append(sr),
        )

        assert len(steps_received) >= 1
        assert result.success is True
