"""
workers/reliability/circuit_breaker.py — Per-task circuit breaker.

Tracks consecutive failures by FailureClass.group so unrelated failures
don't interfere. Created once per task execution in the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    """Prevents runaway repair attempts for the same failure group.

    Groups by FailureClass.group (llm, browser, network, etc.) so a
    string of browser failures doesn't block retrying an LLM failure.
    """

    max_consecutive: int = 3
    _counts: dict[str, int] = field(default_factory=dict)

    def allow_attempt(self, group: str) -> bool:
        """Return True if we haven't exceeded max_consecutive for *group*."""
        return self._counts.get(group, 0) < self.max_consecutive

    def record_failure(self, group: str) -> None:
        """Increment consecutive failure count for *group*."""
        self._counts[group] = self._counts.get(group, 0) + 1

    def record_success(self, group: str) -> None:
        """Reset the failure counter for *group* on success."""
        self._counts.pop(group, None)

    def reset(self) -> None:
        """Clear all counters."""
        self._counts.clear()
