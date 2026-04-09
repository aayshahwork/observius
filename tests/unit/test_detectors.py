"""Tests for workers.reliability.detectors — failure classification."""

from __future__ import annotations

import pytest

from workers.shared_types import (
    FailureClass,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.reliability.detectors import (
    classify_outcome,
    detect_failure,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _outcome(
    check_name: str = "",
    message: str = "",
    actual: str = "",
    verdict: ValidatorVerdict = ValidatorVerdict.FAIL,
) -> ValidatorOutcome:
    return ValidatorOutcome(
        verdict=verdict,
        check_name=check_name,
        message=message,
        actual=actual,
    )


# ---------------------------------------------------------------------------
# classify_outcome — check_name fast path
# ---------------------------------------------------------------------------


class TestClassifyOutcomeCheckName:
    def test_auth_redirect(self) -> None:
        assert classify_outcome(_outcome(check_name="auth_redirect")) == FailureClass.AUTH_REQUIRED

    def test_error_page_url(self) -> None:
        assert classify_outcome(_outcome(check_name="error_page_url")) == FailureClass.BROWSER_NAVIGATION

    def test_error_page_title(self) -> None:
        assert classify_outcome(_outcome(check_name="error_page_title")) == FailureClass.BROWSER_NAVIGATION


# ---------------------------------------------------------------------------
# classify_outcome — message heuristics
# ---------------------------------------------------------------------------


class TestClassifyOutcomeMessage:
    @pytest.mark.parametrize("message,expected", [
        ("Server overloaded, try again", FailureClass.LLM_OVERLOADED),
        ("Rate limit exceeded (429)", FailureClass.LLM_RATE_LIMITED),
        ("API key invalid 401 Anthropic", FailureClass.LLM_AUTH_FAILED),
        ("Context length exceeded", FailureClass.LLM_CONTEXT_OVERFLOW),
        ("Bad request 400 Anthropic", FailureClass.LLM_BAD_REQUEST),
        ("Browser crashed unexpectedly", FailureClass.BROWSER_CRASH),
        ("Navigation timeout after 30s", FailureClass.BROWSER_TIMEOUT),
        ("Element not found on page", FailureClass.BROWSER_ELEMENT_MISSING),
        ("Click intercepted by overlay", FailureClass.BROWSER_CLICK_INTERCEPTED),
        ("Element blocked by overlay", FailureClass.BROWSER_ELEMENT_BLOCKED),
        ("dns resolving failed for host", FailureClass.NETWORK_DNS),
        ("Connection refused ECONNREFUSED", FailureClass.NETWORK_CONNECTION),
        ("Captcha detected on page", FailureClass.ANTI_BOT_CAPTCHA),
        ("Access denied - bot blocked", FailureClass.ANTI_BOT_BLOCKED),
        ("Stuck loop detected", FailureClass.AGENT_LOOP),
        ("Max steps exhausted", FailureClass.AGENT_EXHAUSTED_STEPS),
        ("Session expired, re-auth needed", FailureClass.AUTH_SESSION_EXPIRED),
        ("Login required to continue", FailureClass.AUTH_REQUIRED),
    ])
    def test_message_patterns(self, message: str, expected: FailureClass) -> None:
        assert classify_outcome(_outcome(message=message)) == expected

    def test_actual_field_also_checked(self) -> None:
        """Message heuristics should also check the 'actual' field."""
        assert classify_outcome(_outcome(actual="browser crashed")) == FailureClass.BROWSER_CRASH

    def test_unknown_fallback(self) -> None:
        assert classify_outcome(_outcome(message="something unrecognised")) == FailureClass.UNKNOWN


# ---------------------------------------------------------------------------
# classify_outcome — step_error refinement
# ---------------------------------------------------------------------------


class TestClassifyOutcomeStepError:
    def test_step_error_unknown_refined_by_message(self) -> None:
        """check_name='step_error' maps to UNKNOWN, then message refines it."""
        o = _outcome(check_name="step_error", message="browser crashed")
        assert classify_outcome(o) == FailureClass.BROWSER_CRASH

    def test_step_error_unrecognised_message_stays_unknown(self) -> None:
        o = _outcome(check_name="step_error", message="something weird")
        assert classify_outcome(o) == FailureClass.UNKNOWN


# ---------------------------------------------------------------------------
# detect_failure — async priority composer
# ---------------------------------------------------------------------------


class TestDetectFailure:
    async def test_check_name_takes_priority(self) -> None:
        o = _outcome(check_name="auth_redirect", message="timeout")
        assert await detect_failure(o) == FailureClass.AUTH_REQUIRED

    async def test_message_heuristic_used_when_no_check_name(self) -> None:
        o = _outcome(message="captcha detected")
        assert await detect_failure(o) == FailureClass.ANTI_BOT_CAPTCHA

    async def test_extra_error_message_combined(self) -> None:
        o = _outcome()
        assert await detect_failure(o, error_message="browser crashed") == FailureClass.BROWSER_CRASH

    async def test_llm_fallback_called_when_no_match(self) -> None:
        o = _outcome(message="something weird")

        async def fake_llm(text: str) -> FailureClass:
            return FailureClass.AGENT_LOOP

        assert await detect_failure(o, llm_fallback=fake_llm) == FailureClass.AGENT_LOOP

    async def test_llm_fallback_error_returns_unknown(self) -> None:
        o = _outcome(message="something weird")

        async def broken_llm(text: str) -> FailureClass:
            raise RuntimeError("LLM down")

        assert await detect_failure(o, llm_fallback=broken_llm) == FailureClass.UNKNOWN

    async def test_llm_fallback_not_called_when_match_found(self) -> None:
        o = _outcome(message="browser crashed")
        called = False

        async def spy_llm(text: str) -> FailureClass:
            nonlocal called
            called = True
            return FailureClass.UNKNOWN

        result = await detect_failure(o, llm_fallback=spy_llm)
        assert result == FailureClass.BROWSER_CRASH
        assert not called

    async def test_fallback_to_unknown(self) -> None:
        o = _outcome()
        assert await detect_failure(o) == FailureClass.UNKNOWN
