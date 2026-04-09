"""
workers/backends/skyvern.py — Skyvern cloud API backend.

Delegates full goals to the Skyvern API (https://api.skyvern.com).
Goal-delegation mode only: execute_step() is NOT supported.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from workers.backends.protocol import BackendCapabilities
from workers.config import worker_settings
from workers.shared_types import Observation, StepIntent, StepResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKYVERN_BASE_URL = "https://api.skyvern.com"
_DEFAULT_ENGINE = "skyvern-2.0"
_POLL_INTERVAL_SECONDS = 3
_MAX_POLL_SECONDS = 600  # 10 minute ceiling

# Terminal Skyvern run statuses.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "terminated", "timed_out", "canceled"})


class SkyvernBackend:
    """CUABackend implementation using the Skyvern cloud API.

    Skyvern runs its own browser in the cloud and returns step-by-step
    timelines with screenshots and extracted data.

    API reference:
      POST /v1/run/tasks     — create a new task run
      GET  /v1/run/{run_id}  — poll run status
      GET  /v1/run/{run_id}/timeline — step-by-step timeline
    """

    capabilities = BackendCapabilities(
        supports_single_step=False,
        supports_goal_delegation=True,
        supports_screenshots=True,
        supports_har=False,
        supports_trace=False,
        supports_video=True,
        supports_ax_tree=False,
    )

    def __init__(self) -> None:
        self._config: dict = {}
        self._client: Any = None  # httpx.AsyncClient
        self._api_key: str = ""
        self._base_url: str = _SKYVERN_BASE_URL
        self._last_run_id: Optional[str] = None
        self._last_run_status: Optional[Dict[str, Any]] = None

    @property
    def name(self) -> str:
        return "skyvern"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, config: dict) -> None:
        """Set up httpx.AsyncClient with Skyvern API credentials.

        Config keys:
            skyvern_api_key: str — Skyvern API key (required)
            skyvern_base_url: str — override base URL (default: https://api.skyvern.com)
            skyvern_engine: str — engine version (default: skyvern-2.0)
            proxy_location: str — proxy location (default: RESIDENTIAL)
            url: str — target URL for the task
            data_extraction_schema: dict — JSON schema for structured output
            max_steps: int — max steps (default: 20)
            timeout_seconds: int — max poll duration (default: 600)
        """
        import httpx

        self._config = config
        self._api_key = config.get("skyvern_api_key") or ""
        self._base_url = config.get("skyvern_base_url", _SKYVERN_BASE_URL).rstrip("/")

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def teardown(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("Skyvern httpx client close failed: %s", exc)
            self._client = None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute_step(self, intent: StepIntent) -> StepResult:
        """Not supported — SkyvernBackend uses goal delegation only."""
        raise NotImplementedError(
            "SkyvernBackend uses execute_goal — delegation mode only"
        )

    async def execute_goal(self, goal: str, max_steps: int = 20) -> List[StepResult]:
        """Delegate a full goal to the Skyvern API.

        1. POST /v1/run/tasks with goal as prompt
        2. Poll GET /v1/run/{run_id} every 3s until completed/failed
        3. Fetch timeline for step-by-step results
        4. Convert to list[StepResult]
        """
        if self._client is None:
            raise RuntimeError(
                "SkyvernBackend not initialized — call initialize() first"
            )

        # -- 1. Create the task run --
        run_id = await self._create_run(goal, max_steps)
        self._last_run_id = run_id
        logger.info("Skyvern run created: %s", run_id)

        # -- 2. Poll until terminal status --
        timeout = self._config.get("timeout_seconds", _MAX_POLL_SECONDS)
        run_data = await self._poll_run(run_id, timeout)
        self._last_run_status = run_data

        # -- 3. Fetch timeline --
        timeline = await self._fetch_timeline(run_id)

        # -- 4. Convert to StepResults --
        return self._convert_timeline(run_data, timeline)

    async def get_observation(self) -> Observation:
        """Return latest state from the most recent run."""
        if self._client is None or self._last_run_id is None:
            return Observation()

        try:
            run_data = await self._get_run(self._last_run_id)
            self._last_run_status = run_data

            screenshot_b64 = _extract_screenshot(run_data)
            url = run_data.get("url") or run_data.get("navigation_goal_url") or ""

            return Observation(
                url=url,
                screenshot_b64=screenshot_b64,
                timestamp_ms=int(time.time() * 1000),
            )
        except Exception as exc:
            logger.debug("get_observation failed: %s", exc)
            return Observation()

    # ------------------------------------------------------------------
    # Skyvern API calls
    # ------------------------------------------------------------------

    async def _create_run(self, goal: str, max_steps: int) -> str:
        """POST /v1/run/tasks — create a new task run."""
        body: Dict[str, Any] = {
            "prompt": goal,
            "url": self._config.get("url", ""),
            "engine": self._config.get("skyvern_engine", _DEFAULT_ENGINE),
            "max_steps": max_steps,
        }

        proxy = self._config.get("proxy_location")
        if proxy:
            body["proxy_location"] = proxy

        schema = self._config.get("data_extraction_schema")
        if schema:
            body["data_extraction_schema"] = schema

        resp = await self._client.post("/v1/run/tasks", json=body)
        resp.raise_for_status()
        data = resp.json()

        run_id = data.get("run_id") or data.get("task_id") or data.get("id")
        if not run_id:
            raise RuntimeError(f"Skyvern API returned no run_id: {data}")
        return run_id

    async def _get_run(self, run_id: str) -> Dict[str, Any]:
        """GET /v1/run/{run_id} — fetch current run status."""
        resp = await self._client.get(f"/v1/run/{run_id}")
        resp.raise_for_status()
        return resp.json()

    async def _poll_run(self, run_id: str, timeout: float) -> Dict[str, Any]:
        """Poll run status until terminal or timeout."""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            data = await self._get_run(run_id)
            status = (data.get("status") or "").lower()

            logger.debug("Skyvern run %s status: %s", run_id, status)

            if status in _TERMINAL_STATUSES:
                return data

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        raise TimeoutError(
            f"Skyvern run {run_id} did not complete within {timeout}s"
        )

    async def _fetch_timeline(self, run_id: str) -> List[Dict[str, Any]]:
        """GET /v1/run/{run_id}/timeline — step-by-step timeline."""
        try:
            resp = await self._client.get(f"/v1/run/{run_id}/timeline")
            resp.raise_for_status()
            data = resp.json()
            # Timeline may be a list directly or nested under a key
            if isinstance(data, list):
                return data
            return data.get("timeline") or data.get("steps") or data.get("actions") or []
        except Exception as exc:
            logger.warning("Failed to fetch Skyvern timeline for %s: %s", run_id, exc)
            return []

    # ------------------------------------------------------------------
    # Timeline → StepResult conversion
    # ------------------------------------------------------------------

    def _convert_timeline(
        self,
        run_data: Dict[str, Any],
        timeline: List[Dict[str, Any]],
    ) -> List[StepResult]:
        """Convert Skyvern timeline entries into list[StepResult]."""
        results: List[StepResult] = []
        run_status = (run_data.get("status") or "").lower()

        if not timeline:
            # No timeline — create a single result from the run itself
            screenshot_b64 = _extract_screenshot(run_data)
            success = run_status == "completed"
            error_msg = run_data.get("failure_reason") or run_data.get("error")

            extracted = run_data.get("extracted_information") or run_data.get("result")
            side_effects: list[str] = []
            if extracted is not None:
                side_effects.append(f"extracted:{_truncate(str(extracted), 500)}")
            side_effects.append(f"status:{run_status}")

            results.append(StepResult(
                success=success,
                error=str(error_msg) if error_msg else None,
                observation=Observation(
                    url=run_data.get("url") or "",
                    screenshot_b64=screenshot_b64,
                    timestamp_ms=int(time.time() * 1000),
                ),
                side_effects=side_effects,
            ))
            return results

        for i, step in enumerate(timeline):
            step_type = step.get("type") or step.get("action_type") or ""
            step_status = (step.get("status") or "").lower()
            success = step_status not in ("failed", "error")
            error_msg = step.get("failure_reason") or step.get("error")

            # Screenshot from step
            screenshot_b64 = _extract_screenshot(step)

            # URL from step
            step_url = step.get("url") or step.get("navigation_url") or ""

            # Duration
            duration_ms = 0
            started = step.get("started_at") or step.get("created_at")
            completed = step.get("completed_at") or step.get("ended_at")
            if started and completed:
                try:
                    from datetime import datetime

                    t_start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                    t_end = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                    duration_ms = int((t_end - t_start).total_seconds() * 1000)
                except Exception:
                    pass

            observation = Observation(
                url=step_url,
                screenshot_b64=screenshot_b64,
                timestamp_ms=int(time.time() * 1000),
            )

            side_effects: list[str] = []
            if step_type:
                side_effects.append(f"action:{step_type}")

            reasoning = step.get("reasoning") or step.get("thought")
            if reasoning:
                side_effects.append(f"thought:{_truncate(str(reasoning), 200)}")

            # Tag last step with extracted data and completion status
            if i == len(timeline) - 1:
                extracted = run_data.get("extracted_information") or run_data.get("result")
                if extracted is not None:
                    side_effects.append(f"extracted:{_truncate(str(extracted), 500)}")
                side_effects.append(f"run_status:{run_status}")

            results.append(StepResult(
                success=success,
                error=str(error_msg) if error_msg else None,
                duration_ms=duration_ms,
                observation=observation,
                side_effects=side_effects,
            ))

        return results


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_screenshot(data: Dict[str, Any]) -> Optional[str]:
    """Extract a base64 screenshot from a Skyvern response dict.

    Skyvern may return screenshots in various fields depending on
    the API version.
    """
    for key in ("screenshot_base64", "screenshot_b64", "screenshot", "screenshot_url"):
        val = data.get(key)
        if val and isinstance(val, str):
            # If it's a URL, we can't inline it — skip
            if val.startswith("http://") or val.startswith("https://"):
                continue
            return val
    return None


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string with ellipsis."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."
