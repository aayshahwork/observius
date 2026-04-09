"""Composable failure detectors.

Each detector checks ONE signal source and returns ``FailureClass | None``.
``detect_failure()`` runs them in priority order — first match wins.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from workers.shared_types import (
    FailureClass,
    Observation,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)

# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

_ERROR_CODE_MAP: dict[str, FailureClass] = {
    "element_not_found": FailureClass.ELEMENT_NOT_FOUND,
    "element_not_clickable": FailureClass.ELEMENT_NOT_CLICKABLE,
    "element_obscured": FailureClass.ELEMENT_OBSCURED,
    "timeout": FailureClass.NETWORK_TIMEOUT,
    "navigation_timeout": FailureClass.NETWORK_TIMEOUT,
    "stale_element": FailureClass.STALE_ELEMENT,
    "wrong_frame": FailureClass.WRONG_FRAME,
}


def detect_from_error_code(result: StepResult) -> FailureClass | None:
    """Map machine-readable error codes to failure classes."""
    if result.error_code is None:
        return None
    return _ERROR_CODE_MAP.get(result.error_code)


_ERROR_TEXT_PATTERNS: list[tuple[str, FailureClass]] = [
    # Ordered most-specific to least-specific — do NOT reorder without care.
    # Multi-word or overlapping patterns must precede their substrings.
    ("captcha", FailureClass.CAPTCHA_CHALLENGE),
    ("login", FailureClass.AUTH_REQUIRED),
    ("401", FailureClass.AUTH_REQUIRED),
    ("403", FailureClass.ANTI_BOT_BLOCKED),
    ("429", FailureClass.ANTI_BOT_BLOCKED),
    ("modal", FailureClass.UNEXPECTED_MODAL),
    ("dialog", FailureClass.UNEXPECTED_MODAL),
    ("not visible", FailureClass.ELEMENT_OBSCURED),
    # "stale" must precede "element" — "stale element reference" contains both.
    ("stale", FailureClass.STALE_ELEMENT),
    ("element", FailureClass.ELEMENT_NOT_FOUND),
    ("proxy", FailureClass.PROXY_FAILURE),
    ("timeout", FailureClass.NETWORK_TIMEOUT),
]


def detect_from_error_text(result: StepResult) -> FailureClass | None:
    """Pattern-match on free-text error messages."""
    if not result.error:
        return None
    lower = result.error.lower()
    for pattern, fc in _ERROR_TEXT_PATTERNS:
        if pattern in lower:
            return fc
    return None


def detect_from_network_signals(observation: Observation) -> FailureClass | None:
    """Check HTTP status codes in network signals."""
    for sig in observation.network_signals:
        status = sig.get("status", 0)
        if isinstance(status, int):
            if status == 401:
                return FailureClass.AUTH_REQUIRED
            if status in (403, 429):
                return FailureClass.ANTI_BOT_BLOCKED
            if status >= 500:
                return FailureClass.NETWORK_TIMEOUT
    return None


def detect_from_url(observation: Observation) -> FailureClass | None:
    """Check if the browser was redirected to login/error/captcha pages."""
    url = (observation.url or "").lower()
    if any(kw in url for kw in ("login", "signin", "sso", "auth")):
        return FailureClass.AUTH_REQUIRED
    if "captcha" in url or "challenge" in url:
        return FailureClass.CAPTCHA_CHALLENGE
    if "blocked" in url or "denied" in url:
        return FailureClass.ANTI_BOT_BLOCKED
    return None


def detect_stuck(
    observation: Observation,
    previous_dom_hash: str | None = None,
) -> FailureClass | None:
    """Same DOM hash as previous step means the agent is stuck."""
    if previous_dom_hash and observation.dom_hash == previous_dom_hash:
        return FailureClass.STUCK
    return None


_VERDICT_MAP: dict[ValidatorVerdict, FailureClass] = {
    ValidatorVerdict.FAIL_UI: FailureClass.ELEMENT_NOT_FOUND,
    ValidatorVerdict.FAIL_GOAL: FailureClass.GOAL_NOT_MET,
    ValidatorVerdict.FAIL_NETWORK: FailureClass.NETWORK_TIMEOUT,
    ValidatorVerdict.FAIL_POLICY: FailureClass.POLICY_VIOLATION,
    ValidatorVerdict.FAIL_STUCK: FailureClass.STUCK,
}


def detect_from_outcome(outcome: ValidatorOutcome) -> FailureClass | None:
    """Derive failure class from validator verdict."""
    return _VERDICT_MAP.get(outcome.verdict)


# ---------------------------------------------------------------------------
# Composer — runs detectors in priority order
# ---------------------------------------------------------------------------


async def detect_failure(
    outcome: ValidatorOutcome,
    result: StepResult,
    observation: Observation,
    previous_dom_hash: str | None = None,
    llm_fallback: Callable[[StepResult, Observation], Awaitable[FailureClass]] | None = None,
) -> FailureClass:
    """Run detectors in priority order. First match wins.

    Priority:
        1. error_code  (most specific, machine-readable)
        2. error_text  (pattern matching on free text)
        3. network_signals  (HTTP status codes)
        4. url  (redirect detection)
        5. stuck  (DOM stagnation)
        6. outcome  (validator verdict — least specific deterministic)
        7. LLM fallback  (optional async call)
        8. UNKNOWN
    """
    detectors: list[Callable[[], FailureClass | None]] = [
        lambda: detect_from_error_code(result),
        lambda: detect_from_error_text(result),
        lambda: detect_from_network_signals(observation),
        lambda: detect_from_url(observation),
        lambda: detect_stuck(observation, previous_dom_hash),
        lambda: detect_from_outcome(outcome),
    ]

    for detector in detectors:
        fc = detector()
        if fc is not None:
            return fc

    if llm_fallback is not None:
        try:
            return await llm_fallback(result, observation)
        except Exception:
            pass

    return FailureClass.UNKNOWN
