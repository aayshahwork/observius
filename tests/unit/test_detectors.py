"""Tests for workers.reliability.detectors — composable failure detection."""

from __future__ import annotations

from typing import Any  # used by _obs / _result helpers

import pytest

from workers.shared_types import (
    FailureClass,
    Observation,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.reliability.detectors import (
    detect_failure,
    detect_from_error_code,
    detect_from_error_text,
    detect_from_network_signals,
    detect_from_outcome,
    detect_from_url,
    detect_stuck,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(
    url: str = "https://example.com",
    title: str = "Example",
    network_signals: list[dict[str, Any]] | None = None,
    dom_hash: str | None = None,
) -> Observation:
    return Observation(
        url=url,
        title=title,
        network_signals=network_signals or [],
        dom_hash=dom_hash,
    )


def _result(
    success: bool = False,
    error: str | None = None,
    error_code: str | None = None,
    obs: Observation | None = None,
) -> StepResult:
    return StepResult(
        success=success,
        observation=obs or _obs(),
        error=error,
        error_code=error_code,
    )


def _outcome(verdict: ValidatorVerdict = ValidatorVerdict.FAIL_UI) -> ValidatorOutcome:
    return ValidatorOutcome(verdict=verdict)


# ---------------------------------------------------------------------------
# detect_from_error_code
# ---------------------------------------------------------------------------

class TestDetectFromErrorCode:
    def test_known_codes(self) -> None:
        cases = {
            "element_not_found": FailureClass.ELEMENT_NOT_FOUND,
            "element_not_clickable": FailureClass.ELEMENT_NOT_CLICKABLE,
            "element_obscured": FailureClass.ELEMENT_OBSCURED,
            "timeout": FailureClass.NETWORK_TIMEOUT,
            "navigation_timeout": FailureClass.NETWORK_TIMEOUT,
            "stale_element": FailureClass.STALE_ELEMENT,
            "wrong_frame": FailureClass.WRONG_FRAME,
        }
        for code, expected in cases.items():
            assert detect_from_error_code(_result(error_code=code)) == expected

    def test_unknown_code_returns_none(self) -> None:
        assert detect_from_error_code(_result(error_code="some_new_error")) is None

    def test_none_code_returns_none(self) -> None:
        assert detect_from_error_code(_result(error_code=None)) is None


# ---------------------------------------------------------------------------
# detect_from_error_text
# ---------------------------------------------------------------------------

class TestDetectFromErrorText:
    @pytest.mark.parametrize("text,expected", [
        ("Captcha detected on page", FailureClass.CAPTCHA_CHALLENGE),
        ("Login required to continue", FailureClass.AUTH_REQUIRED),
        ("HTTP 401 Unauthorized", FailureClass.AUTH_REQUIRED),
        ("HTTP 403 Forbidden", FailureClass.ANTI_BOT_BLOCKED),
        ("Rate limited (429)", FailureClass.ANTI_BOT_BLOCKED),
        ("Unexpected modal appeared", FailureClass.UNEXPECTED_MODAL),
        ("A dialog box blocked the page", FailureClass.UNEXPECTED_MODAL),
        ("Element not visible on page", FailureClass.ELEMENT_OBSCURED),
        ("Target element could not be located", FailureClass.ELEMENT_NOT_FOUND),
        ("Proxy connection refused", FailureClass.PROXY_FAILURE),
        ("Navigation timeout after 30s", FailureClass.NETWORK_TIMEOUT),
        ("Stale reference in DOM", FailureClass.STALE_ELEMENT),
        # Playwright's exact error string — must match STALE_ELEMENT, not ELEMENT_NOT_FOUND.
        ("stale element reference: The element reference is stale", FailureClass.STALE_ELEMENT),
    ])
    def test_patterns(self, text: str, expected: FailureClass) -> None:
        assert detect_from_error_text(_result(error=text)) == expected

    def test_no_match_returns_none(self) -> None:
        assert detect_from_error_text(_result(error="Something completely different")) is None

    def test_none_error_returns_none(self) -> None:
        assert detect_from_error_text(_result(error=None)) is None

    def test_empty_error_returns_none(self) -> None:
        assert detect_from_error_text(_result(error="")) is None

    def test_case_insensitive(self) -> None:
        assert detect_from_error_text(_result(error="CAPTCHA DETECTED")) == FailureClass.CAPTCHA_CHALLENGE


# ---------------------------------------------------------------------------
# detect_from_network_signals
# ---------------------------------------------------------------------------

class TestDetectFromNetworkSignals:
    def test_401(self) -> None:
        obs = _obs(network_signals=[{"status": 401, "url": "/api", "method": "GET"}])
        assert detect_from_network_signals(obs) == FailureClass.AUTH_REQUIRED

    def test_403(self) -> None:
        obs = _obs(network_signals=[{"status": 403}])
        assert detect_from_network_signals(obs) == FailureClass.ANTI_BOT_BLOCKED

    def test_429(self) -> None:
        obs = _obs(network_signals=[{"status": 429}])
        assert detect_from_network_signals(obs) == FailureClass.ANTI_BOT_BLOCKED

    def test_500(self) -> None:
        obs = _obs(network_signals=[{"status": 500}])
        assert detect_from_network_signals(obs) == FailureClass.NETWORK_TIMEOUT

    def test_502(self) -> None:
        obs = _obs(network_signals=[{"status": 502}])
        assert detect_from_network_signals(obs) == FailureClass.NETWORK_TIMEOUT

    def test_200_returns_none(self) -> None:
        obs = _obs(network_signals=[{"status": 200}])
        assert detect_from_network_signals(obs) is None

    def test_empty_signals_returns_none(self) -> None:
        assert detect_from_network_signals(_obs()) is None

    def test_first_bad_signal_wins(self) -> None:
        # 401 comes first; the 500 later in the list must not override it.
        obs = _obs(network_signals=[{"status": 401}, {"status": 500}])
        assert detect_from_network_signals(obs) == FailureClass.AUTH_REQUIRED

    def test_bad_signal_after_ok_signal(self) -> None:
        obs = _obs(network_signals=[{"status": 200}, {"status": 401}])
        assert detect_from_network_signals(obs) == FailureClass.AUTH_REQUIRED

    def test_missing_status_key_skipped(self) -> None:
        obs = _obs(network_signals=[{"url": "/test"}])
        assert detect_from_network_signals(obs) is None


# ---------------------------------------------------------------------------
# detect_from_url
# ---------------------------------------------------------------------------

class TestDetectFromUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://example.com/login", FailureClass.AUTH_REQUIRED),
        ("https://example.com/signin", FailureClass.AUTH_REQUIRED),
        ("https://sso.example.com/start", FailureClass.AUTH_REQUIRED),
        ("https://example.com/auth/callback", FailureClass.AUTH_REQUIRED),
        ("https://example.com/captcha", FailureClass.CAPTCHA_CHALLENGE),
        ("https://example.com/challenge/verify", FailureClass.CAPTCHA_CHALLENGE),
        ("https://example.com/blocked", FailureClass.ANTI_BOT_BLOCKED),
        ("https://example.com/access-denied", FailureClass.ANTI_BOT_BLOCKED),
    ])
    def test_url_patterns(self, url: str, expected: FailureClass) -> None:
        assert detect_from_url(_obs(url=url)) == expected

    def test_normal_url_returns_none(self) -> None:
        assert detect_from_url(_obs(url="https://example.com/dashboard")) is None

    def test_case_insensitive(self) -> None:
        assert detect_from_url(_obs(url="https://example.com/LOGIN")) == FailureClass.AUTH_REQUIRED


# ---------------------------------------------------------------------------
# detect_stuck
# ---------------------------------------------------------------------------

class TestDetectStuck:
    def test_same_hash_means_stuck(self) -> None:
        obs = _obs(dom_hash="abc123")
        assert detect_stuck(obs, previous_dom_hash="abc123") == FailureClass.STUCK

    def test_different_hash_returns_none(self) -> None:
        obs = _obs(dom_hash="abc123")
        assert detect_stuck(obs, previous_dom_hash="def456") is None

    def test_no_previous_hash_returns_none(self) -> None:
        obs = _obs(dom_hash="abc123")
        assert detect_stuck(obs, previous_dom_hash=None) is None

    def test_no_current_hash_returns_none(self) -> None:
        obs = _obs(dom_hash=None)
        assert detect_stuck(obs, previous_dom_hash="abc123") is None

    def test_empty_string_previous_hash_returns_none(self) -> None:
        # An empty hash means "couldn't be computed" — must not trigger STUCK even if both are "".
        obs = _obs(dom_hash="")
        assert detect_stuck(obs, previous_dom_hash="") is None


# ---------------------------------------------------------------------------
# detect_from_outcome
# ---------------------------------------------------------------------------

class TestDetectFromOutcome:
    @pytest.mark.parametrize("verdict,expected", [
        (ValidatorVerdict.FAIL_UI, FailureClass.ELEMENT_NOT_FOUND),
        (ValidatorVerdict.FAIL_GOAL, FailureClass.GOAL_NOT_MET),
        (ValidatorVerdict.FAIL_NETWORK, FailureClass.NETWORK_TIMEOUT),
        (ValidatorVerdict.FAIL_POLICY, FailureClass.POLICY_VIOLATION),
        (ValidatorVerdict.FAIL_STUCK, FailureClass.STUCK),
    ])
    def test_mapped_verdicts(self, verdict: ValidatorVerdict, expected: FailureClass) -> None:
        assert detect_from_outcome(_outcome(verdict)) == expected

    def test_pass_returns_none(self) -> None:
        assert detect_from_outcome(_outcome(ValidatorVerdict.PASS)) is None

    def test_uncertain_returns_none(self) -> None:
        assert detect_from_outcome(_outcome(ValidatorVerdict.UNCERTAIN)) is None


# ---------------------------------------------------------------------------
# detect_failure (composer)
# ---------------------------------------------------------------------------

class TestDetectFailure:
    """Integration tests for the priority-ordered composer."""

    async def test_error_code_takes_priority_over_error_text(self) -> None:
        """error_code=element_not_found should win even though error text says 'timeout'."""
        r = _result(error_code="element_not_found", error="Navigation timeout")
        obs = _obs()
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)
        assert await detect_failure(outcome, r, obs) == FailureClass.ELEMENT_NOT_FOUND

    async def test_error_text_takes_priority_over_network(self) -> None:
        """error text should win over network signals."""
        obs = _obs(network_signals=[{"status": 429}])
        r = _result(error="Captcha detected", obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)
        assert await detect_failure(outcome, r, obs) == FailureClass.CAPTCHA_CHALLENGE

    async def test_network_takes_priority_over_url(self) -> None:
        obs = _obs(url="https://example.com/login", network_signals=[{"status": 429}])
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)
        assert await detect_failure(outcome, r, obs) == FailureClass.ANTI_BOT_BLOCKED

    async def test_url_takes_priority_over_stuck(self) -> None:
        obs = _obs(url="https://example.com/captcha", dom_hash="same")
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)
        assert await detect_failure(outcome, r, obs, previous_dom_hash="same") == FailureClass.CAPTCHA_CHALLENGE

    async def test_stuck_takes_priority_over_outcome(self) -> None:
        obs = _obs(dom_hash="same")
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.FAIL_GOAL)
        assert await detect_failure(outcome, r, obs, previous_dom_hash="same") == FailureClass.STUCK

    async def test_falls_through_to_outcome(self) -> None:
        obs = _obs()
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.FAIL_GOAL)
        assert await detect_failure(outcome, r, obs) == FailureClass.GOAL_NOT_MET

    async def test_falls_through_to_unknown(self) -> None:
        obs = _obs()
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)
        assert await detect_failure(outcome, r, obs) == FailureClass.UNKNOWN

    async def test_llm_fallback_called_when_no_detector_matches(self) -> None:
        obs = _obs()
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)

        async def fake_llm(result: StepResult, observation: Observation) -> FailureClass:
            return FailureClass.FALSE_SUCCESS

        assert await detect_failure(outcome, r, obs, llm_fallback=fake_llm) == FailureClass.FALSE_SUCCESS

    async def test_llm_fallback_error_returns_unknown(self) -> None:
        obs = _obs()
        r = _result(obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)

        async def broken_llm(result: StepResult, observation: Observation) -> FailureClass:
            raise RuntimeError("LLM unavailable")

        assert await detect_failure(outcome, r, obs, llm_fallback=broken_llm) == FailureClass.UNKNOWN

    async def test_llm_fallback_not_called_when_detector_matches(self) -> None:
        obs = _obs()
        r = _result(error_code="timeout", obs=obs)
        outcome = _outcome(ValidatorVerdict.UNCERTAIN)
        called = False

        async def spy_llm(result: StepResult, observation: Observation) -> FailureClass:
            nonlocal called
            called = True
            return FailureClass.UNKNOWN

        fc = await detect_failure(outcome, r, obs, llm_fallback=spy_llm)
        assert fc == FailureClass.NETWORK_TIMEOUT
        assert not called
