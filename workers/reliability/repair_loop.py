"""
workers/reliability/repair_loop.py — Self-healing repair loop.

Classifies the failure, checks the circuit breaker, executes a repair
playbook action, and returns whether the subgoal should be re-attempted.

Integrates with episodic memory to prioritize known-good fixes and record outcomes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from workers.memory.episodic import EpisodicMemory
from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.detectors import classify_outcome
from workers.reliability.playbooks import RepairAction, RepairStrategy, get_playbook
from workers.shared_types import ValidatorOutcome

if TYPE_CHECKING:
    from workers.backends.protocol import CUABackend
    from workers.pav.planner import Planner
    from workers.pav.types import SubGoal
    from workers.pav.validator import Validator

logger = logging.getLogger(__name__)


async def run_repair(
    outcome: ValidatorOutcome,
    subgoal: "SubGoal",
    backend: "CUABackend",
    planner: "Planner",
    validator: "Validator",
    *,
    circuit_breaker: CircuitBreaker | None = None,
    episodic_memory: EpisodicMemory,
    domain: str = "",
) -> bool:
    """Attempt to repair a failed subgoal outcome.

    Returns True if the repair succeeded and the subgoal should be
    re-attempted, False to fall through to replan.

    Side effects:
    - Stamps ``outcome.failure_class`` with the classified failure.
    - Stamps ``outcome.patch_applied`` with the strategy used (if any).
    """
    # -- Classify --
    fc = classify_outcome(outcome)
    outcome.failure_class = fc.value

    group = fc.group

    logger.info(
        "repair_loop: subgoal=%s failure_class=%s group=%s",
        subgoal.id,
        fc.value,
        group,
    )

    # -- Circuit breaker --
    if circuit_breaker is not None and not circuit_breaker.allow_attempt(group):
        logger.warning(
            "repair_loop: circuit breaker tripped for group=%s, falling through",
            group,
        )
        return False

    # -- Get playbook prioritised by episodic memory --
    playbook = list(get_playbook(fc))

    try:
        known = await episodic_memory.get_known_fixes(fc.value, domain)
        known_strategies: list[RepairAction] = []
        for fix in known:
            try:
                strategy = RepairStrategy(fix["repair_strategy"])
                known_strategies.append(RepairAction(strategy, description=f"memory: {fix.get('description', '')}"))
            except (ValueError, KeyError):
                pass
        if known_strategies:
            seen_strategies = {a.strategy for a in known_strategies}
            playbook = known_strategies + [a for a in playbook if a.strategy not in seen_strategies]
    except Exception:
        logger.warning("repair_loop: episodic memory query failed; using static playbook")

    if not playbook:
        return False

    # Execute the first action in the playbook
    action = playbook[0]

    try:
        success = await _execute_repair_action(action, backend)
    except Exception as exc:
        logger.debug("repair_loop: repair action failed: %s", exc)
        success = False

    # -- Record on circuit breaker --
    if circuit_breaker is not None:
        if success:
            circuit_breaker.record_success(group)
        else:
            circuit_breaker.record_failure(group)

    # -- Record outcome in episodic memory --
    try:
        await episodic_memory.record_failure_fix(
            fc.value,
            action.strategy.value,
            success=success,
            domain=domain,
        )
    except Exception:
        logger.warning("repair_loop: episodic memory write failed")

    if success:
        outcome.patch_applied = action.strategy.value
        logger.info(
            "repair_loop: applied %s for %s",
            action.strategy.value,
            fc.value,
        )

    return success


async def _execute_repair_action(action: RepairAction, backend: "CUABackend") -> bool:
    """Execute a single repair action. Returns True if the subgoal should be retried."""
    strategy = action.strategy

    if strategy in (RepairStrategy.ABORT, RepairStrategy.REPLAN):
        return False

    if strategy == RepairStrategy.WAIT_AND_RETRY:
        if action.wait_seconds > 0:
            await asyncio.sleep(action.wait_seconds)
        return True

    if strategy == RepairStrategy.REFRESH_PAGE:
        from workers.shared_types import StepIntent
        try:
            await backend.execute_step(StepIntent(
                action_type="navigate",
                description="Refresh current page (repair)",
            ))
        except Exception:
            try:
                await backend.execute_goal("Refresh the current page", max_steps=2)
            except Exception:
                return False
        return True

    if strategy == RepairStrategy.RE_NAVIGATE:
        from workers.shared_types import StepIntent
        try:
            await backend.execute_step(StepIntent(
                action_type="navigate",
                description="Re-navigate to current URL (repair)",
            ))
        except Exception:
            try:
                await backend.execute_goal("Navigate back to the current page", max_steps=2)
            except Exception:
                return False
        return True

    if strategy == RepairStrategy.SCROLL_AND_RETRY:
        from workers.shared_types import StepIntent
        try:
            await backend.execute_step(StepIntent(
                action_type="scroll",
                description="Scroll down to reveal element (repair)",
            ))
        except Exception:
            try:
                await backend.execute_goal("Scroll down the page", max_steps=2)
            except Exception:
                return False
        return True

    if strategy == RepairStrategy.DISMISS_OVERLAY:
        try:
            await backend.execute_goal(
                "Dismiss any popup, overlay, cookie banner, or modal that is blocking the page",
                max_steps=3,
            )
        except Exception:
            return False
        return True

    # Unknown strategy — don't retry
    return False
