"""
workers/reliability/circuit_breaker.py — Per-task circuit breaker.

Tracks consecutive failures both by FailureClass (fine-grained) and by
group (llm/browser/network/etc.) so unrelated failures don't interfere.
Created once per task execution in the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from workers.shared_types import FailureClass


@dataclass
class CircuitBreaker:
    """Prevents runaway repair attempts for the same failure group.

    Tracks by both fine-grained FailureClass and coarse group so
    unrelated failures don't block each other.

    Parameters
    ----------
    max_consecutive:
        Stop if the same *group* fails this many times in a row.
    max_same_class:
        Stop if the same *FailureClass* repeats this many times.
    max_total_failures:
        Stop on total accumulated failures regardless of class.
    """

    max_consecutive: int = 3
    max_same_class: int = 3
    max_total_failures: int = 10
    _group_counts: dict[str, int] = field(default_factory=dict)
    _class_counts: dict[str, int] = field(default_factory=dict)
    _total: int = 0

    # -------------------------------------------------------------------------
    # Group-based API (used by PAV loop)
    # -------------------------------------------------------------------------

    def allow_attempt(self, group: str) -> bool:
        """Return True if we haven't exceeded max_consecutive for *group*."""
        return self._group_counts.get(group, 0) < self.max_consecutive

    def record_failure(self, group_or_class: str | FailureClass) -> None:
        """Record a failure by group name or FailureClass."""
        if isinstance(group_or_class, FailureClass):
            fc = group_or_class
            group = fc.group
            key = fc.value
        else:
            group = group_or_class
            key = group_or_class

        self._group_counts[group] = self._group_counts.get(group, 0) + 1
        self._class_counts[key] = self._class_counts.get(key, 0) + 1
        self._total += 1

    def record_success(self, group: str) -> None:
        """Reset the failure counter for *group* on success."""
        self._group_counts.pop(group, None)

    # -------------------------------------------------------------------------
    # Class-based API (richer stopping logic)
    # -------------------------------------------------------------------------

    def should_stop(self) -> bool:
        """Return True if any threshold is exceeded."""
        if self._total >= self.max_total_failures:
            return True
        if any(count >= self.max_same_class for count in self._class_counts.values()):
            return True
        if any(count >= self.max_consecutive for count in self._group_counts.values()):
            return True
        return False

    def dominant_failure(self) -> str | None:
        """Return the most frequent FailureClass value, or None if empty."""
        if not self._class_counts:
            return None
        return max(self._class_counts, key=lambda k: self._class_counts[k])

    def reset(self) -> None:
        """Clear all counters."""
        self._group_counts.clear()
        self._class_counts.clear()
        self._total = 0
