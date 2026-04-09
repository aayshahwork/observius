"""Budget tracking for task execution."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Budget:
    max_steps: int = 50
    max_seconds: int = 300
    max_llm_calls: int = 100
    steps_used: int = 0
    llm_calls_used: int = 0
    _start_time: float | None = field(default=None, repr=False)

    def start(self) -> None:
        self._start_time = time.time()

    def record_step(self) -> None:
        self.steps_used += 1

    def record_llm_call(self) -> None:
        self.llm_calls_used += 1

    def has_remaining(self) -> bool:
        if self.steps_used >= self.max_steps:
            return False
        if self.llm_calls_used >= self.max_llm_calls:
            return False
        if self._start_time and (time.time() - self._start_time) >= self.max_seconds:
            return False
        return True

    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    def elapsed_seconds(self) -> float:
        return time.time() - (self._start_time or time.time())
