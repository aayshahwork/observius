"""Repair loop — three-phase recovery when validation fails.

Phase 1: Detect + classify the failure
Phase 2: Try deterministic patches from the playbook (in order)
Phase 3: If all deterministic patches fail, try cognitive patch (LLM replan)

Called as ``repair_fn`` from the PAV loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import urlparse

if TYPE_CHECKING:
    from workers.memory.episodic import EpisodicMemory

from workers.shared_types import (
    FailureClass,
    Observation,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.reliability.circuit_breaker import CircuitBreaker
from workers.reliability.detectors import detect_failure
from workers.reliability.playbooks import (
    REPAIR_PLAYBOOK,
    RepairAction,
    repair_action_to_intent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Placeholder — replaced with real import when SubGoal is defined
# ---------------------------------------------------------------------------

@runtime_checkable
class SubGoal(Protocol):
    """Minimal shape the repair loop needs from a subgoal."""

    description: str
    attempts: int


# ---------------------------------------------------------------------------
# Protocols for collaborators not yet concretely typed
# ---------------------------------------------------------------------------

@runtime_checkable
class BackendCapabilities(Protocol):
    supports_single_step: bool


@runtime_checkable
class CUABackend(Protocol):
    capabilities: BackendCapabilities

    async def execute_step(self, intent: StepIntent) -> StepResult: ...
    async def get_observation(self) -> Observation: ...


@runtime_checkable
class Planner(Protocol):
    async def replan(
        self,
        subgoal: SubGoal,
        outcome: ValidatorOutcome,
        failure_class_value: str,
    ) -> None: ...


@runtime_checkable
class Validator(Protocol):
    async def validate(
        self,
        subgoal: SubGoal,
        intent: StepIntent,
        result: StepResult,
    ) -> ValidatorOutcome: ...


# ---------------------------------------------------------------------------
# run_repair
# ---------------------------------------------------------------------------

async def run_repair(
    outcome: ValidatorOutcome,
    subgoal: SubGoal,
    backend: CUABackend,
    planner: Planner,
    validator: Validator,
    circuit_breaker: CircuitBreaker | None = None,
    previous_dom_hash: str | None = None,
    episodic_memory: "EpisodicMemory | None" = None,
) -> ValidatorOutcome | None:
    """Three-phase repair attempted by the PAV loop on validation failure.

    Returns a :class:`ValidatorOutcome` when repair succeeds (verdict ``PASS``
    or ``UNCERTAIN`` after replanning), or ``None`` when all options are
    exhausted.
    """

    # ------------------------------------------------------------------
    # Phase 1 — Classify
    # ------------------------------------------------------------------
    observation: Observation = outcome.evidence.get("observation") or await backend.get_observation()
    result: StepResult = outcome.evidence.get("result") or StepResult(
        success=False, observation=observation,
    )
    failure_class: FailureClass = await detect_failure(
        outcome, result, observation, previous_dom_hash,
    )

    if circuit_breaker is not None:
        circuit_breaker.record_failure(failure_class)
        if circuit_breaker.should_stop():
            logger.warning("Circuit breaker tripped — dominant: %s", circuit_breaker.dominant_failure())
            return None

    # ------------------------------------------------------------------
    # Phase 2 — Deterministic patches (with memory-backed prioritisation)
    # ------------------------------------------------------------------
    patches: list[RepairAction] = list(REPAIR_PLAYBOOK.get(failure_class, []))

    if episodic_memory is not None:
        try:
            domain = _extract_domain(observation.url or "")
            known = await episodic_memory.get_known_fixes(failure_class.value, domain)
            known_actions: list[RepairAction] = []
            for fix in known:
                try:
                    known_actions.append(RepairAction(fix["repair_action"]))
                except ValueError:
                    pass
            # Prepend known-good actions; keep remaining playbook actions deduplicated
            seen = set(known_actions)
            patches = known_actions + [a for a in patches if a not in seen]
        except Exception:
            logger.warning("Episodic memory unavailable; skipping known-fix lookup")

    context: dict[str, Any] = {
        "current_url": observation.url,
        "original_target": outcome.evidence.get("target", {}),
        "login_url": outcome.evidence.get("login_url", ""),
    }

    for patch_action in patches:
        # ESCALATE_HUMAN — signal without executing
        if patch_action == RepairAction.ESCALATE_HUMAN:
            return ValidatorOutcome(
                verdict=ValidatorVerdict.FAIL_POLICY,
                failure_class=failure_class.value,
                message=f"Escalation needed: {failure_class.value}",
                evidence={"escalation": True},
            )

        # Skip fine-grained patches on delegation-only backends
        if not backend.capabilities.supports_single_step:
            continue

        intent = repair_action_to_intent(patch_action, context)
        try:
            repair_result = await backend.execute_step(intent)
            repair_outcome = await validator.validate(subgoal, intent, repair_result)

            if repair_outcome.verdict == ValidatorVerdict.PASS:
                if episodic_memory is not None:
                    try:
                        domain = _extract_domain(observation.url or "")
                        await episodic_memory.record_failure_fix(
                            failure_class.value,
                            patch_action.value,
                            success=True,
                            domain=domain,
                        )
                    except Exception:
                        pass
                return repair_outcome
        except Exception:
            continue  # patch failed, try next

    # ------------------------------------------------------------------
    # Phase 3 — Cognitive patch (LLM replan)
    # ------------------------------------------------------------------
    try:
        await planner.replan(subgoal, outcome, failure_class.value)
        return ValidatorOutcome(
            verdict=ValidatorVerdict.UNCERTAIN,
            message=f"Replanned after {failure_class.value}",
            failure_class=failure_class.value,
        )
    except Exception:
        return None  # exhausted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    """Return the hostname from a URL, or '' if unparseable."""
    return urlparse(url).hostname or ""
