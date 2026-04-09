"""Tests for workers.reliability.playbooks — repair playbook coverage."""

from __future__ import annotations

import pytest

from workers.shared_types import FailureClass
from workers.reliability.playbooks import (
    RepairAction,
    RepairStrategy,
    get_playbook,
)


# ---------------------------------------------------------------------------
# Playbook completeness
# ---------------------------------------------------------------------------


class TestPlaybookCoverage:
    def test_every_failure_class_has_playbook(self) -> None:
        """Every FailureClass member must return a non-empty list from get_playbook."""
        for fc in FailureClass:
            playbook = get_playbook(fc)
            assert isinstance(playbook, list), f"Expected list for {fc!r}, got {type(playbook)}"
            assert len(playbook) > 0, f"Empty playbook for {fc!r}"

    def test_all_entries_are_repair_actions(self) -> None:
        for fc in FailureClass:
            for action in get_playbook(fc):
                assert isinstance(action, RepairAction), (
                    f"Expected RepairAction in {fc!r} playbook, got {action!r}"
                )

    def test_all_strategies_are_valid(self) -> None:
        for fc in FailureClass:
            for action in get_playbook(fc):
                assert isinstance(action.strategy, RepairStrategy), (
                    f"Invalid strategy {action.strategy!r} in {fc!r} playbook"
                )


# ---------------------------------------------------------------------------
# Specific playbook entries
# ---------------------------------------------------------------------------


class TestSpecificPlaybooks:
    def test_llm_auth_failed_aborts(self) -> None:
        """LLM auth failure is unrecoverable — must abort."""
        actions = get_playbook(FailureClass.LLM_AUTH_FAILED)
        assert actions[0].strategy == RepairStrategy.ABORT

    def test_browser_crash_aborts(self) -> None:
        actions = get_playbook(FailureClass.BROWSER_CRASH)
        assert actions[0].strategy == RepairStrategy.ABORT

    def test_llm_overloaded_waits(self) -> None:
        actions = get_playbook(FailureClass.LLM_OVERLOADED)
        assert actions[0].strategy == RepairStrategy.WAIT_AND_RETRY
        assert actions[0].wait_seconds > 0

    def test_element_missing_scrolls(self) -> None:
        actions = get_playbook(FailureClass.BROWSER_ELEMENT_MISSING)
        assert actions[0].strategy == RepairStrategy.SCROLL_AND_RETRY

    def test_anti_bot_blocked_aborts(self) -> None:
        actions = get_playbook(FailureClass.ANTI_BOT_BLOCKED)
        assert actions[0].strategy == RepairStrategy.ABORT

    def test_unknown_replans(self) -> None:
        actions = get_playbook(FailureClass.UNKNOWN)
        assert actions[0].strategy == RepairStrategy.REPLAN


# ---------------------------------------------------------------------------
# RepairAction dataclass
# ---------------------------------------------------------------------------


class TestRepairAction:
    def test_frozen(self) -> None:
        action = RepairAction(RepairStrategy.WAIT_AND_RETRY, wait_seconds=5.0)
        with pytest.raises(AttributeError):
            action.strategy = RepairStrategy.ABORT  # type: ignore[misc]

    def test_description_default(self) -> None:
        action = RepairAction(RepairStrategy.REPLAN)
        assert action.description == ""

    def test_wait_seconds_default(self) -> None:
        action = RepairAction(RepairStrategy.REPLAN)
        assert action.wait_seconds == 0.0
