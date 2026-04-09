"""Circuit breaker — stops the repair loop when failures accumulate beyond thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field

from workers.shared_types import FailureClass


@dataclass
class CircuitBreaker:
    """Track failure counts and trip when thresholds are exceeded.

    Parameters
    ----------
    max_same_class:
        Stop if the *same* ``FailureClass`` repeats this many times.
    max_total_failures:
        Stop on total accumulated failures regardless of class.
    """

    max_same_class: int = 3
    max_total_failures: int = 10
    _counts: dict[str, int] = field(default_factory=dict)
    _total: int = 0

    def record_failure(self, failure_class: FailureClass) -> None:
        key = failure_class.value
        self._counts[key] = self._counts.get(key, 0) + 1
        self._total += 1

    def should_stop(self) -> bool:
        if self._total >= self.max_total_failures:
            return True
        return any(count >= self.max_same_class for count in self._counts.values())

    def dominant_failure(self) -> str | None:
        """Return the most frequent failure class value (string), or None if empty.

        Ties resolve by insertion order (first-recorded class wins).
        """
        if not self._counts:
            return None
        return max(self._counts, key=lambda k: self._counts[k])

    def reset(self) -> None:
        self._counts.clear()
        self._total = 0
