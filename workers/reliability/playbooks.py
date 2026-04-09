"""Repair playbooks — map each FailureClass to an ordered list of repair actions.

The executor walks the list top-to-bottom, attempting each repair until the step
succeeds or the list is exhausted.  Empty lists mean "no mechanical repair —
escalate to cognitive patch or fail closed."
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from workers.shared_types import FailureClass, StepIntent


class RepairAction(StrEnum):
    """Atomic repair actions the executor can attempt."""

    # UI repairs
    SCROLL_SEARCH = "scroll_search"
    BROADEN_LOCATOR = "broaden_locator"
    SWITCH_TO_VISION = "switch_to_vision"
    CLOSE_OVERLAY = "close_overlay"
    CLOSE_MODAL = "close_modal"
    WAIT_STABILITY = "wait_stability"
    SCROLL_INTO_VIEW = "scroll_into_view"
    DISMISS_DIALOG = "dismiss_dialog"
    SWITCH_FRAME = "switch_frame"

    # Navigation repairs
    BACKTRACK = "backtrack"
    USE_SITE_SEARCH = "use_site_search"
    REFRESH_PAGE = "refresh_page"
    RESTART_FROM_CHECKPOINT = "restart_checkpoint"

    # Auth/session repairs
    RE_AUTH = "re_auth"
    REFRESH_SESSION = "refresh_session"

    # Escalation
    ESCALATE_HUMAN = "escalate_human"


REPAIR_PLAYBOOK: dict[FailureClass, list[RepairAction]] = {
    # UI/Interaction
    FailureClass.ELEMENT_NOT_FOUND: [
        RepairAction.SCROLL_SEARCH,
        RepairAction.WAIT_STABILITY,
        RepairAction.BROADEN_LOCATOR,
        RepairAction.SWITCH_TO_VISION,
    ],
    FailureClass.ELEMENT_NOT_CLICKABLE: [
        RepairAction.WAIT_STABILITY,
        RepairAction.SCROLL_INTO_VIEW,
        RepairAction.CLOSE_OVERLAY,
    ],
    FailureClass.ELEMENT_OBSCURED: [
        RepairAction.CLOSE_OVERLAY,
        RepairAction.CLOSE_MODAL,
        RepairAction.SCROLL_INTO_VIEW,
    ],
    FailureClass.UNEXPECTED_MODAL: [
        RepairAction.CLOSE_MODAL,
        RepairAction.DISMISS_DIALOG,
    ],
    FailureClass.NAVIGATION_LOOP: [
        RepairAction.BACKTRACK,
        RepairAction.USE_SITE_SEARCH,
        RepairAction.RESTART_FROM_CHECKPOINT,
    ],
    FailureClass.WRONG_FRAME: [
        RepairAction.SWITCH_FRAME,
    ],
    FailureClass.STALE_ELEMENT: [
        RepairAction.REFRESH_PAGE,
        RepairAction.WAIT_STABILITY,
    ],
    FailureClass.STUCK: [
        RepairAction.SCROLL_SEARCH,
        RepairAction.REFRESH_PAGE,
        RepairAction.BACKTRACK,
    ],

    # Auth/session
    FailureClass.AUTH_REQUIRED: [
        RepairAction.RE_AUTH,
        RepairAction.REFRESH_SESSION,
    ],
    FailureClass.SESSION_EXPIRED: [
        RepairAction.REFRESH_SESSION,
        RepairAction.RE_AUTH,
    ],

    # Network/Infra
    FailureClass.CAPTCHA_CHALLENGE: [
        RepairAction.ESCALATE_HUMAN,
    ],
    FailureClass.ANTI_BOT_BLOCKED: [
        RepairAction.REFRESH_PAGE,
        RepairAction.ESCALATE_HUMAN,
    ],
    FailureClass.NETWORK_TIMEOUT: [
        RepairAction.REFRESH_PAGE,
    ],
    FailureClass.PROXY_FAILURE: [],

    # Task/Goal — cognitive patch only
    FailureClass.GOAL_NOT_MET: [],
    FailureClass.FALSE_SUCCESS: [],
    FailureClass.INCOMPLETE_EXECUTION: [],

    # Policy/Safety — fail closed
    FailureClass.POLICY_VIOLATION: [],
    FailureClass.CONSENT_REQUIRED: [],
    FailureClass.PII_EXPOSURE_RISK: [],

    # Meta
    FailureClass.BUDGET_EXCEEDED: [],
    FailureClass.UNKNOWN: [],
}


def repair_action_to_intent(
    action: RepairAction,
    context: dict[str, Any] | None = None,
) -> StepIntent:
    """Convert a repair action to a concrete StepIntent."""
    ctx = context or {}

    match action:
        case RepairAction.SCROLL_SEARCH:
            return StepIntent(action="scroll", target={"direction": "down", "amount": "page"})
        case RepairAction.CLOSE_OVERLAY | RepairAction.CLOSE_MODAL:
            return StepIntent(action="click", target={"strategy": "role", "role": "button", "name": "Close"})
        case RepairAction.DISMISS_DIALOG:
            return StepIntent(action="key_press", target={}, value="Escape")
        case RepairAction.WAIT_STABILITY:
            return StepIntent(action="wait", target={}, value="2000")
        case RepairAction.REFRESH_PAGE:
            return StepIntent(action="navigate", target={}, value=ctx.get("current_url", ""))
        case RepairAction.BACKTRACK:
            return StepIntent(action="go_back", target={})
        case RepairAction.SCROLL_INTO_VIEW:
            original_target = ctx.get("original_target", {})
            return StepIntent(
                action="scroll",
                target=original_target,
                metadata={"missing_target": True} if not original_target else {},
            )
        case RepairAction.RE_AUTH:
            return StepIntent(
                action="navigate", target={},
                value=ctx.get("login_url", ""),
                metadata={"auth_flow": True},
            )
        case RepairAction.REFRESH_SESSION:
            return StepIntent(action="wait", target={}, value="1000", metadata={"refresh_session": True})
        case RepairAction.BROADEN_LOCATOR:
            return StepIntent(action="wait", target={}, value="500", metadata={"broaden_locator": True})
        case RepairAction.SWITCH_TO_VISION:
            return StepIntent(action="wait", target={}, value="500", metadata={"switch_to_vision": True})
        case RepairAction.USE_SITE_SEARCH:
            return StepIntent(action="wait", target={}, value="500", metadata={"use_site_search": True})
        case RepairAction.RESTART_FROM_CHECKPOINT:
            return StepIntent(action="navigate", target={}, value=ctx.get("checkpoint_url", ""))
        case RepairAction.SWITCH_FRAME:
            return StepIntent(action="wait", target={}, value="500", metadata={"switch_frame": True})
        case RepairAction.ESCALATE_HUMAN:
            return StepIntent(action="wait", target={}, value="0", metadata={"escalate_human": True})
        case _:
            return StepIntent(action="wait", target={}, value="1000")
