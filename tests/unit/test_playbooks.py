"""Tests for workers.reliability.playbooks — repair playbook coverage and intent generation."""

from __future__ import annotations

import pytest

from workers.shared_types import FailureClass, StepIntent
from workers.reliability.playbooks import (
    REPAIR_PLAYBOOK,
    RepairAction,
    repair_action_to_intent,
)


# ---------------------------------------------------------------------------
# REPAIR_PLAYBOOK completeness
# ---------------------------------------------------------------------------


class TestPlaybookCoverage:
    def test_every_failure_class_has_entry(self) -> None:
        """Every FailureClass member must have an entry in REPAIR_PLAYBOOK."""
        for fc in FailureClass:
            assert fc in REPAIR_PLAYBOOK, f"Missing REPAIR_PLAYBOOK entry for {fc!r}"

    def test_all_entries_are_lists_of_repair_actions(self) -> None:
        for fc, actions in REPAIR_PLAYBOOK.items():
            assert isinstance(actions, list), f"Expected list for {fc!r}, got {type(actions)}"
            for action in actions:
                assert isinstance(action, RepairAction), (
                    f"Expected RepairAction in {fc!r} list, got {action!r}"
                )

    def test_no_duplicate_actions_per_class(self) -> None:
        for fc, actions in REPAIR_PLAYBOOK.items():
            assert len(actions) == len(set(actions)), (
                f"Duplicate actions in {fc!r}: {actions}"
            )


# ---------------------------------------------------------------------------
# repair_action_to_intent — basic contracts
# ---------------------------------------------------------------------------


class TestRepairActionToIntent:
    def test_every_action_returns_step_intent(self) -> None:
        """Every RepairAction must produce a valid StepIntent."""
        for action in RepairAction:
            intent = repair_action_to_intent(action)
            assert isinstance(intent, StepIntent), f"Expected StepIntent for {action!r}"
            assert isinstance(intent.action, str) and intent.action, (
                f"StepIntent.action must be a non-empty string for {action!r}"
            )
            assert isinstance(intent.target, dict), (
                f"StepIntent.target must be a dict for {action!r}"
            )

    def test_scroll_search(self) -> None:
        intent = repair_action_to_intent(RepairAction.SCROLL_SEARCH)
        assert intent.action == "scroll"
        assert intent.target["direction"] == "down"

    def test_close_overlay(self) -> None:
        intent = repair_action_to_intent(RepairAction.CLOSE_OVERLAY)
        assert intent.action == "click"
        assert intent.target["role"] == "button"

    def test_close_modal(self) -> None:
        intent = repair_action_to_intent(RepairAction.CLOSE_MODAL)
        assert intent.action == "click"
        assert intent.target["name"] == "Close"

    def test_dismiss_dialog(self) -> None:
        intent = repair_action_to_intent(RepairAction.DISMISS_DIALOG)
        assert intent.action == "key_press"
        assert intent.value == "Escape"

    def test_wait_stability(self) -> None:
        intent = repair_action_to_intent(RepairAction.WAIT_STABILITY)
        assert intent.action == "wait"
        assert intent.value == "2000"

    def test_refresh_page_uses_context_url(self) -> None:
        intent = repair_action_to_intent(
            RepairAction.REFRESH_PAGE,
            context={"current_url": "https://example.com/page"},
        )
        assert intent.action == "navigate"
        assert intent.value == "https://example.com/page"

    def test_refresh_page_without_context(self) -> None:
        intent = repair_action_to_intent(RepairAction.REFRESH_PAGE)
        assert intent.action == "navigate"
        assert intent.value == ""

    def test_backtrack(self) -> None:
        intent = repair_action_to_intent(RepairAction.BACKTRACK)
        assert intent.action == "go_back"

    def test_scroll_into_view_uses_original_target(self) -> None:
        target = {"strategy": "css", "selector": "#submit-btn"}
        intent = repair_action_to_intent(
            RepairAction.SCROLL_INTO_VIEW,
            context={"original_target": target},
        )
        assert intent.action == "scroll"
        assert intent.target == target

    def test_re_auth_uses_login_url(self) -> None:
        intent = repair_action_to_intent(
            RepairAction.RE_AUTH,
            context={"login_url": "https://example.com/login"},
        )
        assert intent.action == "navigate"
        assert intent.value == "https://example.com/login"
        assert intent.metadata.get("auth_flow") is True

    def test_escalate_human(self) -> None:
        intent = repair_action_to_intent(RepairAction.ESCALATE_HUMAN)
        assert intent.metadata.get("escalate_human") is True

    def test_scroll_into_view_without_context_sets_missing_target_flag(self) -> None:
        """SCROLL_INTO_VIEW with no context must signal missing target via metadata."""
        intent = repair_action_to_intent(RepairAction.SCROLL_INTO_VIEW)
        assert intent.action == "scroll"
        assert intent.target == {}
        assert intent.metadata.get("missing_target") is True

    def test_none_context_is_safe(self) -> None:
        """Passing context=None must not raise."""
        for action in RepairAction:
            intent = repair_action_to_intent(action, context=None)
            assert isinstance(intent, StepIntent)
