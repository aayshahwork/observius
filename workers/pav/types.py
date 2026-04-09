"""
workers/pav/types.py — Plan-Act-Validate data types.

SubGoal: a single verifiable step in a plan.
PlanState: the full plan with cursor tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SubGoal:
    """A single verifiable step in a plan.

    Each subgoal is either executed step-by-step (delegation_mode=False)
    or delegated to the backend's agentic loop (delegation_mode=True).
    """

    id: str
    description: str
    success_criteria: str
    status: str = "pending"  # pending | active | done | failed | skipped
    attempts: int = 0
    max_attempts: int = 3
    delegation_mode: bool = False


@dataclass
class PlanState:
    """Full plan with cursor tracking.

    The orchestrator walks through subgoals sequentially, advancing
    the cursor on success and replanning on failure.
    """

    task_goal: str
    subgoals: list[SubGoal] = field(default_factory=list)
    current_index: int = 0
    context: dict[str, Any] = field(default_factory=dict)

    def current_subgoal(self) -> SubGoal | None:
        """Return the subgoal at the cursor, or None if plan is exhausted."""
        if self.current_index < len(self.subgoals):
            return self.subgoals[self.current_index]
        return None

    def is_complete(self) -> bool:
        """True when every subgoal is done/skipped or cursor is past the end."""
        if self.current_index >= len(self.subgoals):
            return True
        return all(
            sg.status in ("done", "skipped") for sg in self.subgoals
        )

    def advance(self) -> None:
        """Mark current subgoal as done and move cursor forward."""
        if self.current_index < len(self.subgoals):
            self.subgoals[self.current_index].status = "done"
            self.current_index += 1

    def mark_failed(self, subgoal: SubGoal) -> None:
        """Mark a subgoal as failed."""
        subgoal.status = "failed"
