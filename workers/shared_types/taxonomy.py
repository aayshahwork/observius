"""
workers/shared_types/taxonomy.py — Unified failure classification.

FailureClass is the canonical 22-value enum that replaces the split between
ErrorCategory (sdk/error_classifier.py, 8 values) and FailureCategory
(sdk/failure_analyzer.py, 9 values).

Both Person A (executor/PAV) and Person B (reliability/retry) use this
taxonomy so they share a single language for failure types.
"""

from __future__ import annotations

from enum import StrEnum


class FailureClass(StrEnum):
    """Fine-grained failure taxonomy for browser automation.

    22 values organized into 7 groups:
    - LLM (5):      API-level LLM failures
    - Browser (6):   Playwright / browser-level failures
    - Network (3):   Connectivity failures
    - Anti-bot (3):  Bot detection / rate limiting
    - Auth (2):      Authentication failures
    - Agent (2):     Agent reasoning / loop failures
    - Unknown (1):   Catch-all
    """

    # -- LLM failures ----------------------------------------------------------
    LLM_OVERLOADED = "llm_overloaded"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_AUTH_FAILED = "llm_auth_failed"
    LLM_BAD_REQUEST = "llm_bad_request"
    LLM_CONTEXT_OVERFLOW = "llm_context_overflow"

    # -- Browser failures ------------------------------------------------------
    BROWSER_CRASH = "browser_crash"
    BROWSER_TIMEOUT = "browser_timeout"
    BROWSER_NAVIGATION = "browser_navigation"
    BROWSER_ELEMENT_MISSING = "browser_element_missing"
    BROWSER_ELEMENT_BLOCKED = "browser_element_blocked"
    BROWSER_CLICK_INTERCEPTED = "browser_click_intercepted"

    # -- Network failures ------------------------------------------------------
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_DNS = "network_dns"
    NETWORK_CONNECTION = "network_connection"

    # -- Anti-bot failures -----------------------------------------------------
    ANTI_BOT_CAPTCHA = "anti_bot_captcha"
    ANTI_BOT_RATE_LIMITED = "anti_bot_rate_limited"
    ANTI_BOT_BLOCKED = "anti_bot_blocked"

    # -- Authentication failures -----------------------------------------------
    AUTH_REQUIRED = "auth_required"
    AUTH_SESSION_EXPIRED = "auth_session_expired"

    # -- Agent failures --------------------------------------------------------
    AGENT_LOOP = "agent_loop"
    AGENT_EXHAUSTED_STEPS = "agent_exhausted_steps"

    # -- Catch-all -------------------------------------------------------------
    UNKNOWN = "unknown"

    @property
    def is_retriable(self) -> bool:
        """Whether this failure class is generally safe to retry."""
        return self in _RETRIABLE

    @property
    def group(self) -> str:
        """High-level group name (llm, browser, network, anti_bot, auth, agent, unknown)."""
        prefix = self.value.split("_")[0]
        if prefix in ("llm", "browser", "network", "auth", "agent", "unknown"):
            return prefix
        if self.value.startswith("anti_bot"):
            return "anti_bot"
        return "unknown"


# Pre-computed set for O(1) retriability lookups.
_RETRIABLE: frozenset[FailureClass] = frozenset({
    FailureClass.LLM_OVERLOADED,
    FailureClass.LLM_RATE_LIMITED,
    FailureClass.LLM_CONTEXT_OVERFLOW,
    FailureClass.BROWSER_TIMEOUT,
    FailureClass.BROWSER_NAVIGATION,
    FailureClass.BROWSER_ELEMENT_MISSING,
    FailureClass.BROWSER_ELEMENT_BLOCKED,
    FailureClass.BROWSER_CLICK_INTERCEPTED,
    FailureClass.NETWORK_TIMEOUT,
    FailureClass.NETWORK_DNS,
    FailureClass.NETWORK_CONNECTION,
    FailureClass.ANTI_BOT_RATE_LIMITED,
    FailureClass.AUTH_SESSION_EXPIRED,
    FailureClass.AGENT_LOOP,
    FailureClass.AGENT_EXHAUSTED_STEPS,
    FailureClass.UNKNOWN,
})
