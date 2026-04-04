"""
computeruse/recovery_router.py — Map failure diagnoses to recovery plans.

Routes a FailureDiagnosis (from failure_analyzer.py) to a concrete
RecoveryPlan that wrap.py executes before the next retry attempt.

Three responsibilities:
1. Environment changes (fresh browser, stealth, timeouts)
2. Task rewriting (inject failure context + memory into the prompt)
3. Give-up decisions (stop retrying when recovery is impossible)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from computeruse.failure_analyzer import FailureCategory, FailureDiagnosis
from computeruse.retry_memory import RetryMemory


# ---------------------------------------------------------------------------
# Category → environment defaults
# ---------------------------------------------------------------------------

_CATEGORY_DEFAULTS: dict[str, dict[str, Any]] = {
    "element_interaction": {
        "fresh_browser": False,
        "increase_timeout": True,
    },
    "navigation": {
        "fresh_browser": False,
        "wait_seconds": 5,
    },
    "anti_bot": {
        "fresh_browser": True,
        "stealth_mode": True,
        "wait_seconds": 30,
    },
    "authentication": {
        "fresh_browser": True,
        "clear_cookies": True,
    },
    "content_mismatch": {
        "fresh_browser": False,
        "wait_seconds": 2,
    },
    "agent_loop": {
        "fresh_browser": False,
        "reduce_max_actions": True,
    },
    "agent_reasoning": {
        "fresh_browser": False,
        "reduce_max_actions": True,
    },
    "infrastructure": {
        "fresh_browser": True,
        "wait_seconds": 5,
    },
    "unknown": {
        "fresh_browser": False,
        "wait_seconds": 3,
    },
}


# ---------------------------------------------------------------------------
# Category → system message overrides
# ---------------------------------------------------------------------------

_SYSTEM_MESSAGE_OVERRIDES: dict[str, str] = {
    "agent_loop": (
        "CRITICAL: You are stuck in a loop. Try a COMPLETELY DIFFERENT approach. "
        "Do NOT repeat any action you have already tried. If a selector failed, "
        "use a totally different selector strategy."
    ),
    "element_interaction": (
        "CRITICAL: Use text-based selectors (e.g., 'Submit', 'Log In') instead of "
        "CSS selectors or element indices. Always dismiss popups and overlays before "
        "clicking any element. Scroll elements into view before interacting."
    ),
    "agent_reasoning": (
        "CRITICAL: Re-read the task carefully. Break it into smaller steps. "
        "Do one thing at a time. Verify each step succeeded before proceeding."
    ),
    "content_mismatch": (
        "CRITICAL: After each navigation, verify you are on the correct page by "
        "checking the URL and page title. If the page is wrong, navigate directly "
        "to the correct URL instead of clicking links."
    ),
}


# ---------------------------------------------------------------------------
# RecoveryPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryPlan:
    """Concrete actions to take before the next retry attempt."""

    # Task modification
    modified_task: str = ""
    extend_system_message: str = ""

    # Environment changes
    fresh_browser: bool = False
    clear_cookies: bool = False
    stealth_mode: bool = False
    increase_timeout: bool = False

    # Timing
    wait_seconds: int = 0

    # Strategy
    should_retry: bool = True
    reduce_max_actions: bool = False
    diagnosis_category: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage and dashboard display."""
        return {
            "modified_task": self.modified_task,
            "extend_system_message": self.extend_system_message,
            "fresh_browser": self.fresh_browser,
            "clear_cookies": self.clear_cookies,
            "stealth_mode": self.stealth_mode,
            "increase_timeout": self.increase_timeout,
            "wait_seconds": self.wait_seconds,
            "should_retry": self.should_retry,
            "reduce_max_actions": self.reduce_max_actions,
            "diagnosis_category": self.diagnosis_category,
        }


# ---------------------------------------------------------------------------
# RecoveryRouter
# ---------------------------------------------------------------------------


class RecoveryRouter:
    """Routes failure diagnoses to concrete recovery plans.

    Usage::

        router = RecoveryRouter()
        plan = router.plan_recovery(
            original_task="Book a flight",
            diagnosis=diagnosis,
            attempt_number=2,
            max_attempts=4,
            memory=retry_memory,
        )
    """

    def plan_recovery(
        self,
        original_task: str,
        diagnosis: FailureDiagnosis,
        attempt_number: int,
        max_attempts: int,
        memory: RetryMemory | None = None,
    ) -> RecoveryPlan:
        """Generate a recovery plan from a diagnosis + retry history."""
        category = diagnosis.category

        # ---- Give-up checks ----
        if not diagnosis.is_retryable:
            return RecoveryPlan(
                should_retry=False,
                diagnosis_category=category.value,
            )

        if attempt_number >= max_attempts:
            return RecoveryPlan(
                should_retry=False,
                diagnosis_category=category.value,
            )

        if (
            category == FailureCategory.ANTI_BOT
            and diagnosis.subcategory == "captcha"
        ):
            return RecoveryPlan(
                should_retry=False,
                diagnosis_category=category.value,
            )

        if memory and memory.same_category_count(category.value) >= 3:
            return RecoveryPlan(
                should_retry=False,
                diagnosis_category=category.value,
            )

        # ---- Environment flags from category defaults ----
        defaults = _CATEGORY_DEFAULTS.get(category.value, {})
        fresh_browser = defaults.get("fresh_browser", False)
        clear_cookies = defaults.get("clear_cookies", False)
        stealth_mode = defaults.get("stealth_mode", False)
        increase_timeout = defaults.get("increase_timeout", False)
        reduce_max_actions = defaults.get("reduce_max_actions", False)
        wait_seconds = defaults.get("wait_seconds", 0)

        # Override with diagnosis-specific environment changes
        for change in diagnosis.environment_changes:
            if change == "fresh_browser":
                fresh_browser = True
            elif change == "clear_cookies":
                clear_cookies = True
            elif change == "stealth_mode":
                stealth_mode = True
            elif change == "increase_timeout":
                increase_timeout = True

        # Use the larger wait time
        wait_seconds = max(wait_seconds, diagnosis.wait_seconds)

        # ---- System message ----
        extend_system_message = _SYSTEM_MESSAGE_OVERRIDES.get(category.value, "")

        # ---- Modified task ----
        modified_task = self._build_modified_task(
            original_task, diagnosis, attempt_number, max_attempts, memory,
        )

        return RecoveryPlan(
            modified_task=modified_task,
            extend_system_message=extend_system_message,
            fresh_browser=fresh_browser,
            clear_cookies=clear_cookies,
            stealth_mode=stealth_mode,
            increase_timeout=increase_timeout,
            wait_seconds=wait_seconds,
            should_retry=True,
            reduce_max_actions=reduce_max_actions,
            diagnosis_category=category.value,
        )

    # ------------------------------------------------------------------
    # Task rewriting
    # ------------------------------------------------------------------

    def _build_modified_task(
        self,
        original_task: str,
        diagnosis: FailureDiagnosis,
        attempt_number: int,
        max_attempts: int,
        memory: RetryMemory | None,
    ) -> str:
        """Construct enriched task description with failure context.

        Target: < 500 tokens total injection above the original task.
        """
        sections: list[str] = [original_task]

        sections.append(
            f"\n## PREVIOUS ATTEMPT CONTEXT (Attempt {attempt_number} of {max_attempts})"
        )
        sections.append(f"WHAT HAPPENED: {diagnosis.root_cause}")
        if diagnosis.progress_achieved:
            sections.append(f"WHAT WORKED: {diagnosis.progress_achieved}")

        sections.append(
            f"\nIMPORTANT — CRITICAL INSTRUCTION FOR THIS ATTEMPT:\n{diagnosis.retry_hint}"
        )

        # Memory section from earlier attempts
        if memory and len(memory) > 0:
            sections.append(memory.get_context_for_prompt())

        # Explicit "do not repeat" list
        all_failed: set[str] = set()
        if memory:
            all_failed = memory.all_failed_actions()
        if diagnosis.failed_action:
            all_failed.add(diagnosis.failed_action)
        if all_failed:
            actions_str = ", ".join(sorted(all_failed)[:5])
            sections.append(f"\nDo NOT repeat these failed actions: {actions_str}")

        return "\n".join(sections)
