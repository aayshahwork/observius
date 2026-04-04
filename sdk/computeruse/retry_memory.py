"""
computeruse/retry_memory.py — Sliding-window memory of failed attempts.

Follows the Reflexion paper's design: omega = 3 for decision-making tasks.
Old entries are dropped, not summarized.

Used by RecoveryRouter to inform task rewriting and give-up decisions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttemptRecord:
    """Record of a single failed attempt."""

    attempt_number: int
    category: str
    root_cause: str
    retry_hint: str
    progress_achieved: str
    failed_actions: list[str] = field(default_factory=list)
    last_url: str = ""
    last_page_title: str = ""
    cost_cents: float = 0.0
    analysis_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage."""
        return {
            "attempt_number": self.attempt_number,
            "category": self.category,
            "root_cause": self.root_cause,
            "retry_hint": self.retry_hint,
            "progress_achieved": self.progress_achieved,
            "failed_actions": self.failed_actions,
            "last_url": self.last_url,
            "last_page_title": self.last_page_title,
            "cost_cents": self.cost_cents,
            "analysis_method": self.analysis_method,
        }


class RetryMemory:
    """Sliding-window memory of failed attempts. Max 3 entries.

    Usage::

        memory = RetryMemory(max_entries=3)
        memory.record(AttemptRecord(...))

        memory.same_category_count("element_interaction")
        memory.all_failed_actions()
        memory.get_context_for_prompt()
    """

    def __init__(self, max_entries: int = 3) -> None:
        self._entries: deque[AttemptRecord] = deque(maxlen=max_entries)

    def record(self, attempt: AttemptRecord) -> None:
        """Append an attempt record. Oldest entry is evicted if at capacity."""
        self._entries.append(attempt)

    def same_category_count(self, category: str) -> int:
        """Count how many recorded attempts share the given category."""
        return sum(1 for entry in self._entries if entry.category == category)

    def all_failed_actions(self) -> set[str]:
        """Deduplicated set of all failed actions across all recorded attempts."""
        actions: set[str] = set()
        for entry in self._entries:
            for action in entry.failed_actions:
                if action:
                    actions.add(action)
        return actions

    def get_context_for_prompt(self) -> str:
        """Format memory entries for injection into task prompt.

        Returns empty string if no entries.
        Target: < 200 tokens total.
        """
        if not self._entries:
            return ""

        lines = ["EARLIER ATTEMPTS (do not repeat these mistakes):"]
        for entry in self._entries:
            lines.append(f"- Attempt {entry.attempt_number}: {entry.root_cause}")
            if entry.retry_hint:
                lines.append(f"  Strategy tried: {entry.retry_hint}")
            if entry.failed_actions:
                actions_str = ", ".join(entry.failed_actions[:3])
                lines.append(f"  Failed actions: {actions_str}")
        return "\n".join(lines)

    def to_list(self) -> list[dict[str, Any]]:
        """Serialize all entries for JSON storage."""
        return [entry.to_dict() for entry in self._entries]

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        """Remove all recorded attempts."""
        self._entries.clear()
