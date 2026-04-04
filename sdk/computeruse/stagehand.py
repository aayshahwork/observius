"""
computeruse/stagehand.py — Stagehand session tracking wrapper.

Wraps a Stagehand AsyncSession with automatic step tracking, screenshot
capture, timing, replay generation, and API reporting.

Usage::

    from stagehand import AsyncStagehand
    from computeruse import observe_stagehand

    async with AsyncStagehand(...) as client:
        session = await client.sessions.start(
            model_name="anthropic/claude-sonnet-4-6",
        )

        async with observe_stagehand(session) as t:
            await t.act("click the login button")
            data = await t.extract("get all prices", schema={...})
            elements = await t.observe("what buttons are visible")

        print(t.steps)
        t.save_replay("debug.html")
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from computeruse.models import ActionType, StepData
from computeruse.replay_generator import ReplayGenerator

logger = logging.getLogger("pokant")

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class StagehandConfig:
    """Configuration for the observe_stagehand() context manager."""

    capture_screenshots: bool = True
    output_dir: str = ".pokant"
    task_id: Optional[str] = None

    # API reporting (optional)
    api_url: Optional[str] = None
    api_key: Optional[str] = None

    # Alerts (optional)
    alerts: Optional[Any] = None  # AlertConfig


class TrackedStagehand:
    """Wraps a Stagehand AsyncSession with automatic step tracking.

    Tracked methods (act, extract, observe, navigate, execute) record
    StepData with screenshots and timing.  All other attribute access
    passes through to the underlying session.
    """

    def __init__(
        self,
        session: Any,
        config: StagehandConfig,
        page: Any = None,
    ) -> None:
        self._session = session
        self._config = config
        self._page = page  # Optional Playwright Page for screenshots
        self._steps: List[StepData] = []
        self._step_counter: int = 0
        self._run_id: str = config.task_id or str(uuid.uuid4())
        self._start_time: float = 0.0
        self._start_dt: Optional[datetime] = None

        self._alert_emitter: Optional[Any] = None
        if config.alerts is not None:
            from computeruse.alerts import AlertEmitter

            self._alert_emitter = AlertEmitter(config.alerts)

    def _start(self) -> None:
        self._start_time = time.monotonic()
        self._start_dt = datetime.now(timezone.utc)

    # -- Tracked methods ---------------------------------------------------

    async def act(self, input: str, **kwargs: Any) -> Any:
        """Execute an AI-driven action and record the step."""
        return await self._tracked_call(
            ActionType.ACT,
            f"act({input[:80]})",
            self._session.act,
            input=input,
            **kwargs,
        )

    async def extract(
        self,
        instruction: str,
        schema: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Extract structured data and record the step."""
        desc = f"extract({instruction[:80]})"
        if schema:
            desc += f" schema={list(schema.keys())}"
        return await self._tracked_call(
            ActionType.EXTRACT,
            desc,
            self._session.extract,
            instruction=instruction,
            schema=schema,
            **kwargs,
        )

    async def observe(self, instruction: str, **kwargs: Any) -> Any:
        """Observe elements on the page and record the step."""
        return await self._tracked_call(
            ActionType.OBSERVE,
            f"observe({instruction[:80]})",
            self._session.observe,
            instruction=instruction,
            **kwargs,
        )

    async def navigate(self, url: str, **kwargs: Any) -> Any:
        """Navigate to a URL and record the step."""
        return await self._tracked_call(
            ActionType.NAVIGATE,
            f"navigate({url})",
            self._session.navigate,
            url=url,
            **kwargs,
        )

    async def execute(self, **kwargs: Any) -> Any:
        """Run multi-step agent execution and record the step."""
        return await self._tracked_call(
            ActionType.ACT,
            "execute(agent)",
            self._session.execute,
            **kwargs,
        )

    # -- Passthrough -------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    # -- Results access ----------------------------------------------------

    @property
    def steps(self) -> List[StepData]:
        """Return a copy of the recorded steps."""
        return list(self._steps)

    def save_replay(self, path: Optional[str] = None) -> str:
        """Generate and save replay HTML.  Returns the file path."""
        if path is None:
            replay_dir = Path(self._config.output_dir) / "replays"
            replay_dir.mkdir(parents=True, exist_ok=True)
            path = str(replay_dir / f"{self._run_id}.html")
        metadata = self._build_task_metadata()
        gen = ReplayGenerator(self._steps, metadata)
        return gen.generate(path)

    def generate_replay(self) -> str:
        """Generate replay HTML string without saving to disk."""
        metadata = self._build_task_metadata()
        gen = ReplayGenerator(self._steps, metadata)
        replay_json = gen._build_replay_json()

        html_template = (_TEMPLATES_DIR / "replay.html").read_text(encoding="utf-8")
        tailwind_css = (_TEMPLATES_DIR / "tailwind-subset.css").read_text(encoding="utf-8")
        html = html_template.replace("/* __TAILWIND_CSS__ */", tailwind_css)
        replay_data_js = json.dumps(replay_json, separators=(",", ":"))
        html = html.replace(
            'var replayData = "__REPLAY_DATA__";',
            f"var replayData = {replay_data_js};",
        )
        return html

    # -- Internal ----------------------------------------------------------

    async def _tracked_call(
        self,
        action_type: str,
        description: str,
        method: Any,
        **kwargs: Any,
    ) -> Any:
        """Generic wrapper for tracked session methods."""
        start = time.monotonic()
        try:
            result = await method(**kwargs)
            duration = int((time.monotonic() - start) * 1000)
            screenshot = await self._safe_screenshot()
            self._record_step(action_type, description, duration, True, screenshot)
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            screenshot = await self._safe_screenshot()
            self._record_step(
                action_type, description, duration, False, screenshot, str(exc),
            )
            if self._alert_emitter:
                from computeruse.error_classifier import classify_error

                classified = classify_error(exc)
                self._alert_emitter.emit_failure(
                    self._run_id, str(exc), classified.category,
                )
            raise

    def _record_step(
        self,
        action_type: str,
        description: str,
        duration_ms: int,
        success: bool,
        screenshot_bytes: Optional[bytes] = None,
        error: Optional[str] = None,
    ) -> None:
        self._step_counter += 1
        step = StepData(
            step_number=self._step_counter,
            action_type=action_type,
            description=description,
            duration_ms=duration_ms,
            success=success,
            error=error,
            timestamp=datetime.now(timezone.utc),
            screenshot_bytes=screenshot_bytes,
        )
        self._steps.append(step)

    async def _safe_screenshot(self) -> Optional[bytes]:
        """Take a screenshot via the Playwright page, if available."""
        if not self._config.capture_screenshots or self._page is None:
            return None
        try:
            return await self._page.screenshot(type="jpeg", quality=85)
        except Exception:
            return None

    def _build_task_metadata(self) -> Dict[str, Any]:
        duration = (
            int((time.monotonic() - self._start_time) * 1000)
            if self._start_time
            else 0
        )
        has_failure = any(not s.success for s in self._steps)
        return {
            "task_id": self._run_id,
            "url": "",
            "task": "",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration,
            "success": not has_failure,
        }

    def _save_outputs(self) -> None:
        """Save screenshots to disk."""
        if not self._config.capture_screenshots:
            return
        has_screenshots = any(s.screenshot_bytes for s in self._steps)
        if not has_screenshots:
            return
        screenshots_dir = (
            Path(self._config.output_dir) / "screenshots" / self._run_id
        )
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        for step in self._steps:
            if step.screenshot_bytes:
                path = screenshots_dir / f"step_{step.step_number:03d}.jpg"
                path.write_bytes(step.screenshot_bytes)

    def _save_run_metadata(self) -> None:
        """Save run metadata JSON."""
        runs_dir = Path(self._config.output_dir) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._build_task_metadata()
        metadata["executor_mode"] = "stagehand"
        metadata["steps_count"] = len(self._steps)
        metadata["steps"] = [
            {
                "step_number": s.step_number,
                "action_type": s.action_type,
                "description": s.description,
                "success": s.success,
                "duration_ms": s.duration_ms,
                "error": s.error,
            }
            for s in self._steps
        ]
        path = runs_dir / f"{self._run_id}.json"
        path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


@asynccontextmanager
async def observe_stagehand(
    session: Any,
    config: Optional[StagehandConfig] = None,
    page: Any = None,
    **kwargs: Any,
) -> AsyncGenerator[TrackedStagehand, None]:
    """Track a Stagehand AsyncSession with automatic screenshots, timing, and replays.

    Args:
        session: A Stagehand ``AsyncSession`` (from ``client.sessions.start()``).
        config: Optional :class:`StagehandConfig`.  If *None*, one is built
                from ``**kwargs``.
        page: Optional Playwright ``Page`` connected to the same session's
              CDP URL.  Required for screenshot capture.
        **kwargs: Forwarded to :class:`StagehandConfig` when *config* is not
                  given.

    Usage::

        async with observe_stagehand(session, page=playwright_page) as t:
            await t.act("click the login button")
            data = await t.extract("get all prices", schema={...})

        print(t.steps)
        t.save_replay("debug.html")
    """
    cfg = config or StagehandConfig(**kwargs)
    tracked = TrackedStagehand(session, cfg, page=page)
    tracked._start()

    try:
        yield tracked
    finally:
        tracked._save_outputs()
        tracked._save_run_metadata()
        if cfg.api_url and cfg.api_key:
            try:
                from computeruse._reporting import report_to_api

                has_failure = any(not s.success for s in tracked._steps)
                await report_to_api(
                    api_url=cfg.api_url,
                    api_key=cfg.api_key,
                    task_id=tracked._run_id,
                    task_description="Stagehand session",
                    status="failed" if has_failure else "completed",
                    steps=tracked._steps,
                    cost_cents=0.0,
                    error_category=None,
                    error_message=None,
                    duration_ms=int(
                        (time.monotonic() - tracked._start_time) * 1000
                    ),
                    created_at=tracked._start_dt,
                )
            except Exception as exc:
                logger.warning("API reporting failed for run %s: %s", tracked._run_id, exc)
