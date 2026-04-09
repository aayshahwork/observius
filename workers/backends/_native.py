"""Native CUA backend — Anthropic computer_use tool with Playwright."""

from __future__ import annotations

from typing import List

from workers.shared_types import Observation, StepIntent, StepResult


class NativeBackend:
    """CUABackend implementation using the Anthropic computer_use API directly."""

    @property
    def name(self) -> str:
        return "native"

    async def initialize(self, config: dict) -> None:
        self._config = config

    async def execute_step(self, intent: StepIntent) -> StepResult:
        raise NotImplementedError("TODO: map StepIntent to computer_use action")

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        raise NotImplementedError("TODO: run native agentic loop")

    async def get_observation(self) -> Observation:
        raise NotImplementedError("TODO: screenshot from Playwright page")

    async def teardown(self) -> None:
        pass
