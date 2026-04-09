"""Skyvern backend — delegates to the Skyvern API."""

from __future__ import annotations

from typing import List

from workers.shared_types import Observation, StepIntent, StepResult


class SkyvernBackend:
    """CUABackend implementation using the Skyvern cloud API."""

    @property
    def name(self) -> str:
        return "skyvern"

    async def initialize(self, config: dict) -> None:
        self._config = config

    async def execute_step(self, intent: StepIntent) -> StepResult:
        raise NotImplementedError("Skyvern delegates full goals, not single steps")

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        raise NotImplementedError("TODO: call Skyvern API")

    async def get_observation(self) -> Observation:
        raise NotImplementedError("TODO: fetch state from Skyvern")

    async def teardown(self) -> None:
        pass
