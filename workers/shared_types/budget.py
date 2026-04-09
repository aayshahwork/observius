"""
workers/shared_types/budget.py — Lightweight budget envelope for a single run.

Budget is a simple value object — it tracks limits and usage but does NOT
enforce them (no exceptions). The executor and retry system read
has_remaining() to decide whether to continue.

For enforcement with BudgetExceededError, see sdk/computeruse/budget.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    """Cost and step budget for a single task run.

    The executor decrements remaining capacity as steps execute.
    Retry logic checks has_remaining() before launching new attempts.
    """

    max_cost_cents: float = 0.0
    spent_cents: float = 0.0
    max_steps: int = 50
    steps_used: int = 0
    max_seconds: float = 0.0  # 0 = no time limit

    def has_remaining(self) -> bool:
        """Whether both cost and step budgets have capacity left."""
        cost_ok = self.max_cost_cents <= 0 or self.spent_cents < self.max_cost_cents
        steps_ok = self.steps_used < self.max_steps
        return cost_ok and steps_ok

    @property
    def remaining_steps(self) -> int:
        """Steps remaining before budget is exhausted."""
        return max(0, self.max_steps - self.steps_used)

    @property
    def remaining_cost_cents(self) -> float:
        """Cost headroom in cents. Returns float('inf') if no cost limit."""
        if self.max_cost_cents <= 0:
            return float("inf")
        return max(0.0, self.max_cost_cents - self.spent_cents)

    @property
    def cost_utilization(self) -> float:
        """Fraction of cost budget used (0.0 to 1.0+). Returns 0.0 if no limit."""
        if self.max_cost_cents <= 0:
            return 0.0
        return self.spent_cents / self.max_cost_cents

    @property
    def step_utilization(self) -> float:
        """Fraction of step budget used (0.0 to 1.0+)."""
        if self.max_steps <= 0:
            return 0.0
        return self.steps_used / self.max_steps

    def record_step(self, cost_cents: float = 0.0) -> None:
        """Record one step and its cost."""
        self.steps_used += 1
        self.spent_cents += cost_cents
