from __future__ import annotations

from sdk.models import TaskConfig, TaskResult


class ComputerUse:
    """Client for the ComputerUse.dev API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.computeruse.dev",
        timeout: float = 300.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def run(self, task: str, *, config: TaskConfig | None = None) -> TaskResult:
        """Execute a browser automation task."""
        raise NotImplementedError("SDK not yet connected to API")

    async def get_task(self, task_id: str) -> TaskResult:
        """Retrieve the result of a previously submitted task."""
        raise NotImplementedError("SDK not yet connected to API")

    async def cancel_task(self, task_id: str) -> None:
        """Cancel a running task."""
        raise NotImplementedError("SDK not yet connected to API")
