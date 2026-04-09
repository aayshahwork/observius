"""
workers/reliability/playbooks.py — Static repair playbooks per FailureClass.

Each playbook is a list[RepairAction] ordered by preference. The repair
loop tries the first action; if it fails, downstream logic falls through
to replan.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from workers.shared_types import FailureClass


class RepairStrategy(StrEnum):
    """What the repair loop should do."""

    WAIT_AND_RETRY = "wait_and_retry"
    REFRESH_PAGE = "refresh_page"
    SCROLL_AND_RETRY = "scroll_and_retry"
    DISMISS_OVERLAY = "dismiss_overlay"
    RE_NAVIGATE = "re_navigate"
    REPLAN = "replan"
    ABORT = "abort"


@dataclass(frozen=True)
class RepairAction:
    """A single repair step."""

    strategy: RepairStrategy
    wait_seconds: float = 0.0
    description: str = ""


# ---------------------------------------------------------------------------
# Static playbook table
# ---------------------------------------------------------------------------

_PLAYBOOKS: dict[FailureClass, list[RepairAction]] = {
    # -- LLM -----------------------------------------------------------------
    FailureClass.LLM_OVERLOADED: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=10.0, description="Wait for LLM capacity"),
    ],
    FailureClass.LLM_RATE_LIMITED: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=5.0, description="Back off rate limit"),
    ],
    FailureClass.LLM_AUTH_FAILED: [
        RepairAction(RepairStrategy.ABORT, description="LLM auth failed — cannot recover"),
    ],
    FailureClass.LLM_BAD_REQUEST: [
        RepairAction(RepairStrategy.REPLAN, description="LLM bad request — replan with different prompt"),
    ],
    FailureClass.LLM_CONTEXT_OVERFLOW: [
        RepairAction(RepairStrategy.REPLAN, description="Context overflow — replan with shorter context"),
    ],

    # -- Browser --------------------------------------------------------------
    FailureClass.BROWSER_CRASH: [
        RepairAction(RepairStrategy.ABORT, description="Browser crashed — cannot recover in-loop"),
    ],
    FailureClass.BROWSER_TIMEOUT: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=3.0, description="Wait and retry after timeout"),
        RepairAction(RepairStrategy.RE_NAVIGATE, description="Re-navigate to current URL"),
    ],
    FailureClass.BROWSER_NAVIGATION: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=2.0, description="Wait and retry navigation"),
        RepairAction(RepairStrategy.RE_NAVIGATE, description="Attempt re-navigation"),
    ],
    FailureClass.BROWSER_ELEMENT_MISSING: [
        RepairAction(RepairStrategy.SCROLL_AND_RETRY, description="Scroll to reveal element"),
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=2.0, description="Wait for element to appear"),
    ],
    FailureClass.BROWSER_ELEMENT_BLOCKED: [
        RepairAction(RepairStrategy.DISMISS_OVERLAY, description="Dismiss blocking overlay"),
    ],
    FailureClass.BROWSER_CLICK_INTERCEPTED: [
        RepairAction(RepairStrategy.DISMISS_OVERLAY, description="Dismiss intercepting overlay"),
    ],

    # -- Network --------------------------------------------------------------
    FailureClass.NETWORK_TIMEOUT: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=5.0, description="Wait after network timeout"),
    ],
    FailureClass.NETWORK_DNS: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=3.0, description="Wait for DNS resolution"),
    ],
    FailureClass.NETWORK_CONNECTION: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=3.0, description="Wait for connection recovery"),
    ],

    # -- Anti-bot -------------------------------------------------------------
    FailureClass.ANTI_BOT_CAPTCHA: [
        RepairAction(RepairStrategy.REPLAN, description="Captcha detected — replan with solver"),
    ],
    FailureClass.ANTI_BOT_RATE_LIMITED: [
        RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=10.0, description="Wait out site rate limit"),
    ],
    FailureClass.ANTI_BOT_BLOCKED: [
        RepairAction(RepairStrategy.ABORT, description="Bot blocked — cannot recover"),
    ],

    # -- Auth -----------------------------------------------------------------
    FailureClass.AUTH_REQUIRED: [
        RepairAction(RepairStrategy.REPLAN, description="Auth required — replan to login first"),
    ],
    FailureClass.AUTH_SESSION_EXPIRED: [
        RepairAction(RepairStrategy.REPLAN, description="Session expired — replan to re-authenticate"),
    ],

    # -- Agent ----------------------------------------------------------------
    FailureClass.AGENT_LOOP: [
        RepairAction(RepairStrategy.REFRESH_PAGE, description="Refresh page to break loop"),
    ],
    FailureClass.AGENT_EXHAUSTED_STEPS: [
        RepairAction(RepairStrategy.REPLAN, description="Steps exhausted — replan with fewer goals"),
    ],

    # -- Unknown --------------------------------------------------------------
    FailureClass.UNKNOWN: [
        RepairAction(RepairStrategy.REPLAN, description="Unknown failure — fall through to replan"),
    ],
}

_DEFAULT_PLAYBOOK: list[RepairAction] = [
    RepairAction(RepairStrategy.REPLAN, description="No playbook — fall through to replan"),
]


def get_playbook(failure_class: FailureClass) -> list[RepairAction]:
    """Return the ordered list of repair actions for a failure class."""
    return _PLAYBOOKS.get(failure_class, _DEFAULT_PLAYBOOK)
