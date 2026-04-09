"""
workers/pav/validator.py — Two-phase step validation.

Phase 1 (deterministic): catches ~80% of failures with zero cost.
Phase 2 (LLM-based): handles ambiguous cases when LLM client is available.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from workers.shared_types import (
    Observation,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.pav.types import SubGoal

logger = logging.getLogger(__name__)

# Patterns that indicate an auth redirect.
_LOGIN_URL_PATTERNS = re.compile(
    r"/(login|signin|sign-in|auth|sso|oauth|accounts/login)",
    re.IGNORECASE,
)

# Patterns that indicate an error page.
_ERROR_URL_PATTERNS = re.compile(
    r"/(error|404|500|403|not-found|access-denied|unauthorized)",
    re.IGNORECASE,
)

# Page title patterns that indicate error states.
_ERROR_TITLE_PATTERNS = re.compile(
    r"(page not found|access denied|forbidden|error|something went wrong"
    r"|server error|internal server error|unauthorized)",
    re.IGNORECASE,
)

_LLM_VALIDATE_PROMPT = """\
You are a browser automation validator. Determine if an action succeeded.

Subgoal: {description}
Success criteria: {criteria}
Action taken: {action} on {target}
Current page URL: {url}
Current page title: {title}

Did the action move us closer to completing the subgoal? Respond with ONLY JSON:
{{
  "verdict": "pass" | "fail" | "warn",
  "evidence": "<what you observed that supports the verdict>",
  "message": "<brief explanation>"
}}
"""


class Validator:
    """Two-phase step validation: deterministic checks first, LLM fallback.

    Deterministic checks (Phase 1) catch ~80% of failures at zero cost:
    - Step execution errors
    - Auth redirects (URL matches login patterns)
    - Error page detection (URL or title matches error patterns)
    - Page error signals (Observation.error_text, console_errors)

    LLM checks (Phase 2) handle ambiguous cases where deterministic
    checks return UNCERTAIN.
    """

    def __init__(
        self,
        llm_client: Any = None,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self.llm = llm_client
        self._model = model

    async def validate(
        self,
        subgoal: SubGoal,
        intent: StepIntent,
        result: StepResult,
    ) -> ValidatorOutcome:
        """Two-phase validation.

        1. FAST deterministic checks (no LLM) — catches ~80% of failures.
        2. SLOW LLM-based judgment (only if deterministic is inconclusive).
        """
        outcome = self._check_deterministic(subgoal, intent, result)
        if outcome.verdict != ValidatorVerdict.UNCERTAIN:
            return outcome

        if self.llm is not None:
            return await self._check_with_llm(subgoal, intent, result)

        return ValidatorOutcome(
            verdict=ValidatorVerdict.UNCERTAIN,
            check_name="llm_fallback",
            message="No LLM available for uncertain verdict",
        )

    # ------------------------------------------------------------------
    # Phase 1: Deterministic checks
    # ------------------------------------------------------------------

    def _check_deterministic(
        self,
        subgoal: SubGoal,
        intent: StepIntent,
        result: StepResult,
    ) -> ValidatorOutcome:
        """Fast checks that need no LLM call.

        Returns UNCERTAIN when none of the deterministic checks are
        conclusive, signalling that Phase 2 should run.
        """
        # Check 1: Did the step fail with an explicit error?
        if not result.success and result.error:
            return ValidatorOutcome(
                verdict=ValidatorVerdict.FAIL,
                check_name="step_error",
                expected="success",
                actual=result.error[:200],
                message=f"Step failed with error: {result.error[:200]}",
                is_critical=True,
            )

        obs = result.observation

        # Remaining checks require an observation
        if obs is None:
            return ValidatorOutcome(
                verdict=ValidatorVerdict.UNCERTAIN,
                check_name="no_observation",
                message="No observation available for deterministic checks",
            )

        # Check 2: Did the page redirect to a login/auth page?
        if obs.url and _LOGIN_URL_PATTERNS.search(obs.url):
            # Only flag as failure if we weren't already on a login page
            # and the intent wasn't to navigate to login
            if intent.url_before and not _LOGIN_URL_PATTERNS.search(intent.url_before):
                return ValidatorOutcome(
                    verdict=ValidatorVerdict.FAIL,
                    check_name="auth_redirect",
                    expected="non-login page",
                    actual=obs.url,
                    message="Page redirected to login — session may have expired",
                    is_critical=True,
                )

        # Check 3: Did the page land on an error page?
        if obs.url and _ERROR_URL_PATTERNS.search(obs.url):
            return ValidatorOutcome(
                verdict=ValidatorVerdict.FAIL,
                check_name="error_page_url",
                expected="non-error page",
                actual=obs.url,
                message=f"Page landed on error URL: {obs.url}",
                is_critical=True,
            )

        # Check 4: Error page title detection
        if obs.page_title and _ERROR_TITLE_PATTERNS.search(obs.page_title):
            return ValidatorOutcome(
                verdict=ValidatorVerdict.WARN,
                check_name="error_page_title",
                expected="normal page title",
                actual=obs.page_title,
                message=f"Page title suggests error: {obs.page_title}",
            )

        # Check 5: Page error signals from observation
        if obs.has_error:
            error_detail = obs.error_text or "; ".join(obs.console_errors[:3])
            return ValidatorOutcome(
                verdict=ValidatorVerdict.WARN,
                check_name="page_errors",
                expected="no errors",
                actual=error_detail[:200],
                message=f"Page has error signals: {error_detail[:200]}",
            )

        # Check 6: URL didn't change after navigate action
        if (
            intent.action_type == "navigate"
            and intent.url_before
            and obs.url
            and obs.url == intent.url_before
        ):
            return ValidatorOutcome(
                verdict=ValidatorVerdict.WARN,
                check_name="url_unchanged",
                expected="URL change",
                actual=obs.url,
                message="URL did not change after navigate action",
            )

        # Check 7: If the step succeeded and no red flags, it's probably OK
        if result.success and not obs.has_error:
            return ValidatorOutcome(
                verdict=ValidatorVerdict.PASS,
                check_name="deterministic_pass",
                message="Step succeeded with no error signals detected",
            )

        # Inconclusive — let LLM decide
        return ValidatorOutcome(
            verdict=ValidatorVerdict.UNCERTAIN,
            check_name="deterministic_inconclusive",
            message="Deterministic checks inconclusive",
        )

    # ------------------------------------------------------------------
    # Phase 2: LLM-based validation
    # ------------------------------------------------------------------

    async def _check_with_llm(
        self,
        subgoal: SubGoal,
        intent: StepIntent,
        result: StepResult,
    ) -> ValidatorOutcome:
        """Ask the LLM to judge whether the action succeeded."""
        obs = result.observation or Observation()

        prompt = _LLM_VALIDATE_PROMPT.format(
            description=subgoal.description,
            criteria=subgoal.success_criteria,
            action=intent.action_type or "unknown",
            target=intent.target_selector or intent.target_text or "unknown",
            url=obs.url or "(unknown)",
            title=obs.page_title or "(unknown)",
        )

        try:
            response = await self.llm.create(
                model=self._model,
                max_tokens=256,
                system="You are a browser automation validator.",
                messages=[{"role": "user", "content": prompt}],
            )

            text = ""
            for block in getattr(response, "content", []):
                if getattr(block, "type", "") == "text":
                    text += getattr(block, "text", "")

            return self._parse_llm_verdict(text.strip())

        except Exception as exc:
            logger.debug("LLM validation failed: %s", exc)
            return ValidatorOutcome(
                verdict=ValidatorVerdict.UNCERTAIN,
                check_name="llm_error",
                message=f"LLM validation failed: {exc}",
            )

    def _parse_llm_verdict(self, raw: str) -> ValidatorOutcome:
        """Parse LLM JSON response into ValidatorOutcome."""
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.debug("Failed to parse LLM verdict: %s", cleaned[:200])
            return ValidatorOutcome(
                verdict=ValidatorVerdict.UNCERTAIN,
                check_name="llm_parse_error",
                message=f"Could not parse LLM response: {cleaned[:100]}",
            )

        if not isinstance(parsed, dict):
            return ValidatorOutcome(
                verdict=ValidatorVerdict.UNCERTAIN,
                check_name="llm_parse_error",
                message="LLM response was not a JSON object",
            )

        raw_verdict = parsed.get("verdict", "uncertain")
        verdict_map = {
            "pass": ValidatorVerdict.PASS,
            "fail": ValidatorVerdict.FAIL,
            "warn": ValidatorVerdict.WARN,
        }
        verdict = verdict_map.get(raw_verdict, ValidatorVerdict.UNCERTAIN)

        return ValidatorOutcome(
            verdict=verdict,
            check_name="llm_judgment",
            message=parsed.get("message", ""),
            metadata={
                "evidence": parsed.get("evidence", ""),
                "raw_verdict": raw_verdict,
            },
        )
