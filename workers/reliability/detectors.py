"""
workers/reliability/detectors.py — Classify failures into FailureClass.

Provides two APIs:
- classify_outcome(outcome): PAV loop fast-path using check_name + regex heuristics.
- detect_failure(...): composable multi-signal detection pipeline (richer, async).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from workers.shared_types import FailureClass, ValidatorOutcome


# ---------------------------------------------------------------------------
# check_name -> FailureClass (fast path, used by classify_outcome)
# ---------------------------------------------------------------------------

_CHECK_NAME_MAP: dict[str, FailureClass] = {
    "auth_redirect": FailureClass.AUTH_REQUIRED,
    "error_page_url": FailureClass.BROWSER_NAVIGATION,
    "error_page_title": FailureClass.BROWSER_NAVIGATION,
    "step_error": FailureClass.UNKNOWN,  # refined below by message
    "delegation_error": FailureClass.UNKNOWN,
    "step_execution_error": FailureClass.UNKNOWN,
}


# ---------------------------------------------------------------------------
# Message heuristic patterns (compiled once, shared by both APIs)
# ---------------------------------------------------------------------------

_MSG_PATTERNS: list[tuple[re.Pattern[str], FailureClass]] = [
    # LLM
    (re.compile(r"overloaded|529|capacity", re.I), FailureClass.LLM_OVERLOADED),
    (re.compile(r"rate.?limit|429|too many requests", re.I), FailureClass.LLM_RATE_LIMITED),
    (re.compile(r"auth.*key|api.?key.*invalid|401.*anthropic|invalid.*api", re.I), FailureClass.LLM_AUTH_FAILED),
    (re.compile(r"context.*(length|overflow|too long|window)", re.I), FailureClass.LLM_CONTEXT_OVERFLOW),
    (re.compile(r"bad.?request|400.*anthropic|invalid.*request", re.I), FailureClass.LLM_BAD_REQUEST),
    # Browser
    (re.compile(r"browser.*crash|target.*closed|page.*closed|browser.*disconnect", re.I), FailureClass.BROWSER_CRASH),
    (re.compile(r"timeout|timed?\s*out", re.I), FailureClass.BROWSER_TIMEOUT),
    (re.compile(r"navigation|net::err_|goto.*failed", re.I), FailureClass.BROWSER_NAVIGATION),
    (re.compile(r"element.*not.*found|no.*element|selector.*not.*found|missing.*element", re.I), FailureClass.BROWSER_ELEMENT_MISSING),
    (re.compile(r"click.*intercept|element.*intercept", re.I), FailureClass.BROWSER_CLICK_INTERCEPTED),
    (re.compile(r"element.*blocked|obscured|overlay|not.*clickable|covered", re.I), FailureClass.BROWSER_ELEMENT_BLOCKED),
    # Network
    (re.compile(r"dns.*resolv|name.*resolution|getaddrinfo", re.I), FailureClass.NETWORK_DNS),
    (re.compile(r"connection.*refused|econnrefused|connection.*reset|econnreset", re.I), FailureClass.NETWORK_CONNECTION),
    (re.compile(r"network.*timeout|socket.*timeout|connect.*timeout", re.I), FailureClass.NETWORK_TIMEOUT),
    # Anti-bot
    (re.compile(r"captcha|recaptcha|hcaptcha|turnstile", re.I), FailureClass.ANTI_BOT_CAPTCHA),
    (re.compile(r"blocked|access.*denied|forbidden.*bot|cloudflare.*block", re.I), FailureClass.ANTI_BOT_BLOCKED),
    (re.compile(r"rate.*limited.*site|too.*many.*attempts", re.I), FailureClass.ANTI_BOT_RATE_LIMITED),
    # Agent
    (re.compile(r"stuck|loop.*detect|repeated.*action", re.I), FailureClass.AGENT_LOOP),
    (re.compile(r"max.*steps|step.*limit|exhausted", re.I), FailureClass.AGENT_EXHAUSTED_STEPS),
    # Auth
    (re.compile(r"session.*expired|token.*expired", re.I), FailureClass.AUTH_SESSION_EXPIRED),
    (re.compile(r"login.*required|auth.*required|unauthenticated", re.I), FailureClass.AUTH_REQUIRED),
]


def _classify_from_message(text: str) -> FailureClass | None:
    """Apply regex patterns to free text. Returns first match or None."""
    for pattern, fc in _MSG_PATTERNS:
        if pattern.search(text):
            return fc
    return None


# ---------------------------------------------------------------------------
# classify_outcome — PAV loop fast-path
# ---------------------------------------------------------------------------


def classify_outcome(outcome: ValidatorOutcome) -> FailureClass:
    """Classify a failed ValidatorOutcome into a FailureClass.

    Strategy:
    1. check_name fast-path (exact match).
    2. Message heuristics (regex scan).
    3. Fallback to UNKNOWN.
    """
    # 1. check_name fast-path (but may need refinement via message)
    check_fc = _CHECK_NAME_MAP.get(outcome.check_name)
    if check_fc is not None and check_fc != FailureClass.UNKNOWN:
        return check_fc

    # 2. Message heuristics
    text = f"{outcome.message} {outcome.actual}".strip()
    if text:
        fc = _classify_from_message(text)
        if fc is not None:
            return fc

    # 3. Fallback
    return FailureClass.UNKNOWN


# ---------------------------------------------------------------------------
# detect_failure — multi-signal composable pipeline (richer, async)
# ---------------------------------------------------------------------------


async def detect_failure(
    outcome: ValidatorOutcome,
    error_message: str | None = None,
    llm_fallback: Callable[[str], Awaitable[FailureClass]] | None = None,
) -> FailureClass:
    """Run detectors in priority order. First match wins.

    Priority:
        1. check_name fast-path (most specific, machine-readable)
        2. Message regex heuristics (free text)
        3. LLM fallback (optional async call)
        4. UNKNOWN
    """
    # 1. check_name fast-path
    check_fc = _CHECK_NAME_MAP.get(outcome.check_name)
    if check_fc is not None and check_fc != FailureClass.UNKNOWN:
        return check_fc

    # 2. Message heuristics — combine outcome message with extra error text
    text = " ".join(filter(None, [outcome.message, outcome.actual, error_message])).strip()
    if text:
        fc = _classify_from_message(text)
        if fc is not None:
            return fc

    # 3. LLM fallback
    if llm_fallback is not None and text:
        try:
            return await llm_fallback(text)
        except Exception:
            pass

    return FailureClass.UNKNOWN
