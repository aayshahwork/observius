"""Failure taxonomy — typed failure categories for detectors, repair playbooks, and metrics."""

from __future__ import annotations

from enum import StrEnum


class FailureClass(StrEnum):
    """Typed failure categories. Used by detectors, repair playbooks, and metrics."""

    # UI/Interaction
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_NOT_CLICKABLE = "element_not_clickable"
    ELEMENT_OBSCURED = "element_obscured"
    WRONG_FRAME = "wrong_frame"
    STALE_ELEMENT = "stale_element"
    UNEXPECTED_MODAL = "unexpected_modal"
    NAVIGATION_LOOP = "navigation_loop"
    # Task/Goal
    GOAL_NOT_MET = "goal_not_met"
    FALSE_SUCCESS = "false_success"
    INCOMPLETE_EXECUTION = "incomplete_execution"
    # Network/Infra
    ANTI_BOT_BLOCKED = "anti_bot_blocked"
    PROXY_FAILURE = "proxy_failure"
    NETWORK_TIMEOUT = "network_timeout"
    CAPTCHA_CHALLENGE = "captcha_challenge"
    AUTH_REQUIRED = "auth_required"
    SESSION_EXPIRED = "session_expired"
    # Policy/Safety
    POLICY_VIOLATION = "policy_violation"
    CONSENT_REQUIRED = "consent_required"
    PII_EXPOSURE_RISK = "pii_exposure_risk"
    # Meta
    BUDGET_EXCEEDED = "budget_exceeded"
    STUCK = "stuck"
    UNKNOWN = "unknown"
