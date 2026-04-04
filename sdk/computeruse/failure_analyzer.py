"""
computeruse/failure_analyzer.py — Diagnose browser automation failures.

Two-tier analysis:
1. Rule-based heuristics (free, instant) — handles common patterns (~40%)
2. Haiku LLM diagnostic (~$0.004) — handles complex/ambiguous failures

Used by wrap.py between retries to decide WHAT to do differently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class FailureCategory(StrEnum):
    """Categories of browser automation failure for retry decisions."""

    ELEMENT_INTERACTION = "element_interaction"
    NAVIGATION = "navigation"
    ANTI_BOT = "anti_bot"
    AUTHENTICATION = "authentication"
    CONTENT_MISMATCH = "content_mismatch"
    AGENT_LOOP = "agent_loop"
    AGENT_REASONING = "agent_reasoning"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


@dataclass
class FailureDiagnosis:
    """Output of failure analysis — tells the retry system what to do."""

    category: FailureCategory
    subcategory: str = ""
    is_retryable: bool = True
    root_cause: str = ""
    progress_achieved: str = ""
    retry_hint: str = ""
    should_change_approach: bool = False
    wait_seconds: int = 0
    environment_changes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    analysis_cost_cents: float = 0.0
    analysis_method: str = "none"
    failed_step_index: int = -1
    failed_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage and dashboard display."""
        return {
            "category": self.category.value,
            "subcategory": self.subcategory,
            "is_retryable": self.is_retryable,
            "root_cause": self.root_cause,
            "progress_achieved": self.progress_achieved,
            "retry_hint": self.retry_hint,
            "should_change_approach": self.should_change_approach,
            "wait_seconds": self.wait_seconds,
            "environment_changes": self.environment_changes,
            "confidence": self.confidence,
            "analysis_cost_cents": self.analysis_cost_cents,
            "analysis_method": self.analysis_method,
            "failed_step_index": self.failed_step_index,
            "failed_action": self.failed_action,
        }


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

# Each rule: (compiled_regex, category, subcategory, retry_hint, confidence,
#             wait_seconds, environment_changes, is_retryable, should_change_approach)

_Rule = tuple[re.Pattern[str], FailureCategory, str, str, float, int, list[str], bool, bool]

_RULES: list[_Rule] = [
    # -- Element interaction ---------------------------------------------------
    (
        re.compile(r"element.*(not found|not visible|not interactable)", re.IGNORECASE),
        FailureCategory.ELEMENT_INTERACTION,
        "element_missing",
        "The target element was not found. Try using text-based selectors instead of CSS selectors, or wait for the page to fully load before interacting.",
        0.85, 2, [], True, False,
    ),
    (
        re.compile(r"(overlay|modal|popup|banner|consent|cookie).*(block|cover|obscur)", re.IGNORECASE),
        FailureCategory.ELEMENT_INTERACTION,
        "overlay_blocking",
        "A popup or overlay is blocking the target element. Dismiss any cookie consent banners, modals, or popups before attempting the action.",
        0.9, 0, [], True, False,
    ),
    (
        re.compile(r"(click|fill|type).*intercept", re.IGNORECASE),
        FailureCategory.ELEMENT_INTERACTION,
        "click_intercepted",
        "Another element intercepted the click. Scroll the target element into view, dismiss any overlays, then retry with a more specific selector.",
        0.85, 1, [], True, False,
    ),
    # -- Navigation ------------------------------------------------------------
    (
        re.compile(r"(timeout|timed out|navigation timeout)", re.IGNORECASE),
        FailureCategory.NAVIGATION,
        "timeout",
        "Page load timed out. Increase the timeout or try waiting for 'domcontentloaded' instead of full page load.",
        0.8, 5, ["increase_timeout"], True, False,
    ),
    (
        re.compile(r"(ERR_CONNECTION_REFUSED|ERR_NAME_NOT_RESOLVED|net::ERR_)", re.IGNORECASE),
        FailureCategory.NAVIGATION,
        "network_error",
        "Network error \u2014 the site may be down or the URL is wrong. Verify the URL and try again.",
        0.9, 10, [], True, False,
    ),
    (
        re.compile(r"(redirect loop|too many redirects|ERR_TOO_MANY_REDIRECTS)", re.IGNORECASE),
        FailureCategory.NAVIGATION,
        "redirect_loop",
        "The site is stuck in a redirect loop. Try clearing cookies and starting with a fresh browser session.",
        0.85, 5, ["fresh_browser", "clear_cookies"], True, False,
    ),
    # -- Anti-bot --------------------------------------------------------------
    (
        re.compile(r"(captcha|recaptcha|hcaptcha|cloudflare|challenge|verify.*human)", re.IGNORECASE),
        FailureCategory.ANTI_BOT,
        "captcha",
        "Anti-bot protection detected. This site has CAPTCHA or bot detection that cannot be bypassed automatically.",
        0.95, 30, ["stealth_mode"], False, False,
    ),
    (
        re.compile(r"(429|rate limit|too many requests|slow down)", re.IGNORECASE),
        FailureCategory.ANTI_BOT,
        "rate_limited",
        "Rate limited by the server. Wait before retrying and reduce request frequency.",
        0.9, 60, [], True, False,
    ),
    (
        re.compile(r"(403|forbidden|access denied|bot.*blocked|ip.*blocked)", re.IGNORECASE),
        FailureCategory.ANTI_BOT,
        "access_blocked",
        "Access was denied. The site may be blocking automated access. Try a fresh browser with stealth settings.",
        0.75, 15, ["fresh_browser", "stealth_mode"], True, False,
    ),
    # -- Authentication --------------------------------------------------------
    (
        re.compile(r"(login|sign.in|authenticate|unauthorized|401)", re.IGNORECASE),
        FailureCategory.AUTHENTICATION,
        "auth_required",
        "Authentication is required. Ensure login credentials are provided and the login step completes before proceeding.",
        0.7, 0, ["fresh_browser"], True, False,
    ),
    (
        re.compile(r"(session.*(expired|invalid|timeout)|logged.out)", re.IGNORECASE),
        FailureCategory.AUTHENTICATION,
        "session_expired",
        "Session expired during the task. Start with a fresh browser and complete authentication before the main task.",
        0.85, 0, ["fresh_browser", "clear_cookies"], True, True,
    ),
    # -- Content mismatch ------------------------------------------------------
    (
        re.compile(r"(expected.*not found|content.*missing|page.*different|wrong page)", re.IGNORECASE),
        FailureCategory.CONTENT_MISMATCH,
        "content_missing",
        "The expected content was not found on the page. The site layout may have changed. Try navigating there through a different path.",
        0.7, 2, [], True, True,
    ),
    # -- Infrastructure --------------------------------------------------------
    (
        re.compile(r"(browser.*crash|process.*exit|target.*closed|context.*destroyed)", re.IGNORECASE),
        FailureCategory.INFRASTRUCTURE,
        "browser_crash",
        "The browser process crashed. Start with a fresh browser instance.",
        0.95, 2, ["fresh_browser"], True, False,
    ),
    (
        re.compile(r"(out of memory|OOM|memory.*exceeded)", re.IGNORECASE),
        FailureCategory.INFRASTRUCTURE,
        "oom",
        "The browser ran out of memory. Use a fresh browser and reduce page complexity if possible.",
        0.9, 5, ["fresh_browser"], True, False,
    ),
    (
        re.compile(r"(ECONNRESET|ECONNREFUSED|socket hang up)", re.IGNORECASE),
        FailureCategory.INFRASTRUCTURE,
        "connection_reset",
        "The connection was reset. This is usually transient \u2014 retry with a fresh browser.",
        0.85, 5, ["fresh_browser"], True, False,
    ),
]


# ---------------------------------------------------------------------------
# FailureAnalyzer
# ---------------------------------------------------------------------------


class FailureAnalyzer:
    """Diagnoses browser automation failures and suggests recovery strategies.

    Two-tier analysis:
    1. Rule-based heuristics (free, instant) \u2014 handles common patterns
    2. Haiku LLM diagnostic (~$0.004) \u2014 handles complex/ambiguous failures

    Usage by wrap.py::

        analyzer = FailureAnalyzer(api_key="sk-...")
        diagnosis = await analyzer.analyze(
            task_description="Book a flight on Delta",
            steps=wrapped.steps,
            error=str(exception),
            error_category=classified.category,
        )
    """

    def __init__(
        self,
        api_key: str | None = None,
        enable_llm: bool = True,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._api_key = api_key
        self._enable_llm = enable_llm and api_key is not None
        self._model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        task_description: str,
        steps: list[Any],
        error: str,
        error_category: str = "",
        last_url: str = "",
        last_page_title: str = "",
        max_steps: int | None = None,
    ) -> FailureDiagnosis:
        """Analyze a failed run and produce a diagnosis.

        First tries rule-based heuristics.  If confidence < 0.7,
        escalates to Haiku LLM for deeper analysis.

        Args:
            max_steps: The step budget for this task (e.g. 50). Used by the
                agent_reasoning heuristic to detect when >80% of the budget
                was consumed without completing the task.
        """
        # Tier 1: rule-based
        diagnosis = self._analyze_rules(steps, error, max_steps=max_steps)

        # Auto-populate progress
        if not diagnosis.progress_achieved:
            diagnosis.progress_achieved = self._summarize_progress(steps)

        # Populate failed step info
        self._populate_failed_step(diagnosis, steps)

        # Tier 2: LLM escalation if confidence too low
        if diagnosis.confidence < 0.7 and self._enable_llm:
            try:
                llm_diagnosis = await self._analyze_llm(
                    task_description, steps, error,
                    error_category, last_url, last_page_title,
                )
                if llm_diagnosis is not None:
                    # Preserve progress from Tier 1 if LLM didn't set it
                    if not llm_diagnosis.progress_achieved:
                        llm_diagnosis.progress_achieved = diagnosis.progress_achieved
                    self._populate_failed_step(llm_diagnosis, steps)
                    return llm_diagnosis
            except Exception:
                logger.debug("LLM analysis failed, using rule-based result", exc_info=True)

        return diagnosis

    # ------------------------------------------------------------------
    # Tier 1: Rule-based heuristics
    # ------------------------------------------------------------------

    def _analyze_rules(
        self, steps: list[Any], error: str, *, max_steps: int | None = None,
    ) -> FailureDiagnosis:
        """Pattern-match on error message and step history."""
        # Check step-history heuristics first (higher signal)
        history_diagnosis = self._check_step_history(steps, max_steps=max_steps)
        if history_diagnosis is not None and history_diagnosis.confidence >= 0.7:
            history_diagnosis.analysis_method = "rule_based"
            return history_diagnosis

        # Check regex rules against the error message
        best: FailureDiagnosis | None = None
        for rule in _RULES:
            pattern, category, subcat, hint, conf, wait, env, retryable, change = rule
            if pattern.search(error):
                candidate = FailureDiagnosis(
                    category=category,
                    subcategory=subcat,
                    is_retryable=retryable,
                    root_cause=error[:200],
                    retry_hint=hint,
                    should_change_approach=change,
                    wait_seconds=wait,
                    environment_changes=list(env),
                    confidence=conf,
                    analysis_method="rule_based",
                )
                if best is None or candidate.confidence > best.confidence:
                    best = candidate

        # If step history gave a result but low confidence, prefer regex if better
        if history_diagnosis is not None:
            if best is None or history_diagnosis.confidence > best.confidence:
                history_diagnosis.analysis_method = "rule_based"
                return history_diagnosis

        if best is not None:
            return best

        # No rules matched — default to retryable so the caller can decide
        # based on retry count rather than suppressing retries on unknown errors
        return FailureDiagnosis(
            category=FailureCategory.UNKNOWN,
            is_retryable=True,
            root_cause=error[:200],
            confidence=0.2,
            analysis_method="rule_based",
        )

    def _check_step_history(
        self, steps: list[Any], *, max_steps: int | None = None,
    ) -> FailureDiagnosis | None:
        """Detect failure patterns from step history."""
        if not steps:
            return None

        # Agent loop: last 3+ consecutive steps have identical action_type AND description.
        # Both must be non-empty to avoid false positives on steps with missing fields.
        if len(steps) >= 3:
            tail = steps[-3:]
            action_types = [getattr(s, "action_type", "") for s in tail]
            descriptions = [getattr(s, "description", "") for s in tail]
            if (
                len(set(action_types)) == 1
                and len(set(descriptions)) == 1
                and action_types[0]
                and descriptions[0]
            ):
                return FailureDiagnosis(
                    category=FailureCategory.AGENT_LOOP,
                    subcategory="repeated_action",
                    is_retryable=True,
                    root_cause=f"Agent repeated '{action_types[0]}' action {len(tail)} times in a row.",
                    retry_hint="The agent is stuck in a loop repeating the same action. Change the approach entirely \u2014 try different selectors, navigate to the page differently, or break the task into smaller steps.",
                    should_change_approach=True,
                    confidence=0.9,
                )

        # Navigation failure on first step
        first = steps[0]
        if (
            not getattr(first, "success", True)
            and getattr(first, "action_type", "").lower() in ("navigate", "goto", "navigation")
        ):
            return FailureDiagnosis(
                category=FailureCategory.NAVIGATION,
                subcategory="initial_navigation_failed",
                is_retryable=True,
                root_cause="The initial page navigation failed before any actions could be performed.",
                retry_hint="Verify the URL is correct and accessible. Try increasing the navigation timeout.",
                wait_seconds=5,
                environment_changes=["increase_timeout"],
                confidence=0.85,
            )

        # Agent reasoning: used >80% of step budget without completing.
        # When max_steps is known, use it. Otherwise fall back to a minimum of 40 steps
        # so we don't false-positive on short runs.
        threshold = int(max_steps * 0.8) if max_steps is not None else 40
        if len(steps) >= threshold:
            last = steps[-1]
            all_success = all(getattr(s, "success", True) for s in steps)
            last_is_extract = getattr(last, "action_type", "").lower() in ("extract", "state_snapshot")
            if all_success and not last_is_extract:
                return FailureDiagnosis(
                    category=FailureCategory.AGENT_REASONING,
                    subcategory="exhausted_steps",
                    is_retryable=True,
                    root_cause=f"Agent used {len(steps)} of {max_steps or '?'} steps without completing the task.",
                    retry_hint="The agent may have misunderstood the task or taken an inefficient path. Simplify the task description or break it into smaller subtasks.",
                    should_change_approach=True,
                    confidence=0.75,
                )

        return None

    # ------------------------------------------------------------------
    # Tier 2: Haiku LLM diagnostic
    # ------------------------------------------------------------------

    async def _analyze_llm(
        self,
        task_description: str,
        steps: list[Any],
        error: str,
        error_category: str,
        last_url: str,
        last_page_title: str,
    ) -> FailureDiagnosis | None:
        """Call Haiku for complex failure analysis. ~$0.004 per call."""
        prompt = self._build_prompt(
            task_description, steps, error, error_category, last_url, last_page_title,
        )
        response_data = await asyncio.to_thread(self._call_haiku_sync, prompt)
        if response_data is None:
            return None

        return self._parse_llm_response(response_data)

    def _build_prompt(
        self,
        task_description: str,
        steps: list[Any],
        error: str,
        error_category: str,
        last_url: str,
        last_page_title: str,
    ) -> str:
        step_count = min(len(steps), 8)
        formatted = self._format_steps_for_prompt(steps[-8:]) if steps else "  (no steps recorded)"
        action_header = f"ACTION LOG (last {step_count} steps):" if steps else "ACTION LOG:"
        return (
            "You are a browser automation failure diagnostician. "
            "Analyze this failed run and suggest how to retry differently.\n\n"
            f"TASK: {task_description}\n\n"
            f"{action_header}\n{formatted}\n\n"
            f"ERROR: {error}\n"
            f"ERROR CATEGORY: {error_category}\n"
            f"LAST URL: {last_url}\n"
            f"LAST PAGE TITLE: {last_page_title}\n\n"
            "Classify into exactly one category: element_interaction, navigation, "
            "anti_bot, authentication, content_mismatch, agent_loop, agent_reasoning, "
            "infrastructure, unknown\n\n"
            "Respond ONLY with this JSON (no markdown, no explanation):\n"
            "{\n"
            '  "category": "<category>",\n'
            '  "subcategory": "<specific_type>",\n'
            '  "is_retryable": true/false,\n'
            '  "root_cause": "<what went wrong, max 2 sentences>",\n'
            '  "progress_achieved": "<what succeeded before failure, max 1 sentence>",\n'
            '  "retry_hint": "<specific instruction for next attempt, max 2 sentences>",\n'
            '  "should_change_approach": true/false,\n'
            '  "wait_seconds": 0,\n'
            '  "environment_changes": []\n'
            "}"
        )

    def _call_haiku_sync(self, prompt: str) -> dict[str, Any] | None:
        """Synchronous urllib POST to Anthropic Messages API."""
        api_key = self._api_key
        if api_key is None:
            return None
        payload = {
            "model": self._model,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            logger.debug("Haiku API call failed: %s", exc)
            return None

    def _parse_llm_response(self, resp_data: dict[str, Any]) -> FailureDiagnosis | None:
        """Parse Anthropic Messages API response into FailureDiagnosis."""
        # Extract text from content blocks
        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        if not text.strip():
            return None

        # Strip markdown fences if present (matches analyzer.py pattern)
        cleaned = (
            text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.debug("Failed to parse LLM JSON: %s", cleaned[:200])
            return None

        # Validate category
        raw_category = parsed.get("category", "unknown")
        try:
            category = FailureCategory(raw_category)
        except ValueError:
            category = FailureCategory.UNKNOWN

        # Calculate cost from usage
        usage = resp_data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost_cents = round((input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000 * 100, 4)

        env_changes = parsed.get("environment_changes", [])
        if not isinstance(env_changes, list):
            env_changes = []

        return FailureDiagnosis(
            category=category,
            subcategory=parsed.get("subcategory", ""),
            is_retryable=bool(parsed.get("is_retryable", True)),
            root_cause=str(parsed.get("root_cause", ""))[:200],
            progress_achieved=str(parsed.get("progress_achieved", "")),
            retry_hint=str(parsed.get("retry_hint", "")),
            should_change_approach=bool(parsed.get("should_change_approach", False)),
            wait_seconds=int(parsed.get("wait_seconds", 0)),
            environment_changes=env_changes,
            confidence=0.85,
            analysis_cost_cents=cost_cents,
            analysis_method="llm_haiku",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarize_progress(self, steps: list[Any]) -> str:
        """Summarize what succeeded before the failure."""
        successful = [s for s in steps if getattr(s, "success", False)]
        if not successful:
            return "No actions completed successfully"
        last_success = successful[-1]
        desc = getattr(last_success, "description", "")[:60]
        return f"Completed {len(successful)}/{len(steps)} steps. Last success: {desc}"

    def _format_steps_for_prompt(self, steps: list[Any]) -> str:
        """Format step history for the diagnostic prompt. Compact, ~50 tokens per step."""
        lines: list[str] = []
        for step in steps:
            success = getattr(step, "success", True)
            status = "\u2713" if success else "\u2717"
            step_num = getattr(step, "step_number", "?")
            action = getattr(step, "action_type", "unknown")
            desc = getattr(step, "description", "")[:60]
            error_msg = getattr(step, "error", None)
            error_part = f" ERROR: {str(error_msg)[:80]}" if error_msg else ""
            intent = getattr(step, "intent", "")
            intent_part = f" [{intent[:40]}]" if intent else ""
            lines.append(f"  {status} Step {step_num}: {action} \u2014 {desc}{intent_part}{error_part}")
        return "\n".join(lines)

    def _populate_failed_step(self, diagnosis: FailureDiagnosis, steps: list[Any]) -> None:
        """Set failed_step_index and failed_action from step history."""
        if diagnosis.failed_step_index >= 0:
            return
        for i, step in enumerate(steps):
            if not getattr(step, "success", True):
                diagnosis.failed_step_index = i
                diagnosis.failed_action = getattr(step, "action_type", "")
                return
