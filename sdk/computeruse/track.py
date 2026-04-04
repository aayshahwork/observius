"""
computeruse/track.py — Playwright Page tracking context manager.

Wraps a Playwright Page with automatic screenshot capture, step timing,
navigation retry, and session persistence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional

from computeruse.error_classifier import classify_error
from computeruse.models import ActionType, StepData
from computeruse.replay_generator import ReplayGenerator

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class TrackConfig:
    """Configuration for the track() context manager."""

    capture_screenshots: bool = True
    retry_navigations: bool = True
    max_navigation_retries: int = 3
    session_key: Optional[str] = None
    output_dir: str = ".pokant"
    task_id: Optional[str] = None

    # API reporting (optional)
    api_url: Optional[str] = None
    api_key: Optional[str] = None

    # Alerts (optional)
    alerts: Optional[Any] = None  # AlertConfig


class TrackedPage:
    """Wraps a Playwright Page with automatic step tracking.

    Tracked methods (goto, click, fill, type, select_option, press,
    wait_for_selector) record StepData with screenshots and timing.
    All other attribute access passes through to the underlying page.
    """

    def __init__(self, page: Any, config: TrackConfig) -> None:
        self._page = page
        self._config = config
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

    async def goto(self, url: str, **kwargs: Any) -> Any:
        """Navigate to URL with optional retry on transient errors."""
        max_attempts = (
            self._config.max_navigation_retries + 1
            if self._config.retry_navigations
            else 1
        )
        pre_url = await self._safe_get_url()

        for attempt in range(max_attempts):
            start = time.monotonic()
            try:
                result = await self._page.goto(url, **kwargs)
                duration = int((time.monotonic() - start) * 1000)
                post_url = await self._safe_get_url()
                screenshot = await self._safe_screenshot()

                # Enrichment: outcomes + verification
                outcomes: dict = {}
                verification = None
                try:
                    from computeruse.step_enrichment import infer_expected_outcomes
                    outcomes = await infer_expected_outcomes(
                        self._page, "navigate", pre_url,
                    )
                except Exception:
                    pass
                try:
                    from computeruse.action_verifier import ActionVerifier
                    verification = await ActionVerifier().verify_action(
                        self._page, "navigate",
                        expected_url_pattern=outcomes.get("url_pattern", ""),
                        expected_text=outcomes.get("expected_text", ""),
                        pre_url=pre_url,
                    )
                except Exception:
                    pass

                self._record_step(
                    ActionType.NAVIGATE, f"goto({url})", duration, True, screenshot,
                    pre_url=pre_url,
                    post_url=post_url,
                    intent=f"Navigate to {url}",
                    intent_detail=f"Open {url}",
                    expected_url_pattern=outcomes.get("url_pattern", ""),
                    expected_text=outcomes.get("expected_text", ""),
                    verification_result=(
                        verification.__dict__ if verification else None
                    ),
                )
                return result
            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                classified = classify_error(exc)
                if (
                    classified.category.startswith("transient")
                    and attempt < max_attempts - 1
                ):
                    logger.debug(
                        "goto(%s) attempt %d failed (retriable): %s",
                        url, attempt + 1, exc,
                    )
                    await asyncio.sleep(2 ** attempt)
                    continue
                screenshot = await self._safe_screenshot()
                self._record_step(
                    ActionType.NAVIGATE, f"goto({url})", duration, False,
                    screenshot, str(exc),
                    pre_url=pre_url,
                )
                if self._alert_emitter:
                    self._alert_emitter.emit_failure(
                        self._run_id, str(exc), classified.category,
                    )
                raise

    async def click(self, selector: str, **kwargs: Any) -> Any:
        """Click an element with enrichment capture."""
        pre_url = await self._safe_get_url()

        # Extract selectors and metadata BEFORE action (element exists now)
        selectors: list = []
        element_meta: dict = {}
        try:
            from computeruse.step_enrichment import (
                extract_element_metadata,
                extract_selectors,
            )
            selectors = await extract_selectors(self._page, selector)
            element_meta = await extract_element_metadata(self._page, selector)
        except Exception:
            pass

        start = time.monotonic()
        try:
            result = await self._page.click(selector, **kwargs)
            duration = int((time.monotonic() - start) * 1000)
            post_url = await self._safe_get_url()
            screenshot = await self._safe_screenshot()

            intent, intent_detail = "", ""
            outcomes: dict = {}
            try:
                from computeruse.step_enrichment import (
                    infer_expected_outcomes,
                    infer_intent_from_step,
                )
                intent, intent_detail = infer_intent_from_step("click", element_meta)
                outcomes = await infer_expected_outcomes(
                    self._page, "click", pre_url,
                )
            except Exception:
                pass

            verification = None
            try:
                from computeruse.action_verifier import ActionVerifier
                verification = await ActionVerifier().verify_action(
                    self._page, "click",
                    expected_url_pattern=outcomes.get("url_pattern", ""),
                    expected_element=outcomes.get("expected_element", ""),
                    pre_url=pre_url,
                )
            except Exception:
                pass

            self._record_step(
                ActionType.CLICK, f"click({selector})", duration, True, screenshot,
                selectors=selectors,
                intent=intent,
                intent_detail=intent_detail,
                pre_url=pre_url,
                post_url=post_url,
                expected_url_pattern=outcomes.get("url_pattern", ""),
                expected_element=outcomes.get("expected_element", ""),
                element_text=element_meta.get("text", ""),
                element_tag=element_meta.get("tag", ""),
                element_role=element_meta.get("role", ""),
                verification_result=(
                    verification.__dict__ if verification else None
                ),
            )
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            screenshot = await self._safe_screenshot()
            self._record_step(
                ActionType.CLICK, f"click({selector})", duration, False,
                screenshot, str(exc),
                pre_url=pre_url,
                selectors=selectors,
                element_text=element_meta.get("text", ""),
                element_tag=element_meta.get("tag", ""),
                element_role=element_meta.get("role", ""),
            )
            if self._alert_emitter:
                classified = classify_error(exc)
                self._alert_emitter.emit_failure(
                    self._run_id, str(exc), classified.category,
                )
            raise

    async def fill(self, selector: str, value: str, **kwargs: Any) -> Any:
        """Fill a form field with enrichment and value parameterization."""
        pre_url = await self._safe_get_url()

        selectors: list = []
        element_meta: dict = {}
        try:
            from computeruse.step_enrichment import (
                extract_element_metadata,
                extract_selectors,
            )
            selectors = await extract_selectors(self._page, selector)
            element_meta = await extract_element_metadata(self._page, selector)
        except Exception:
            pass

        fill_template = ""
        try:
            from computeruse.step_enrichment import detect_parameterizable_values
            fill_template = detect_parameterizable_values(value)
        except Exception:
            pass

        start = time.monotonic()
        try:
            result = await self._page.fill(selector, value, **kwargs)
            duration = int((time.monotonic() - start) * 1000)
            post_url = await self._safe_get_url()
            screenshot = await self._safe_screenshot()

            intent, intent_detail = "", ""
            try:
                from computeruse.step_enrichment import infer_intent_from_step
                intent, intent_detail = infer_intent_from_step("fill", element_meta)
            except Exception:
                pass

            self._record_step(
                ActionType.TYPE, f"fill({selector})", duration, True, screenshot,
                selectors=selectors,
                intent=intent,
                intent_detail=intent_detail,
                pre_url=pre_url,
                post_url=post_url,
                fill_value_template=fill_template,
                element_text=element_meta.get("text", ""),
                element_tag=element_meta.get("tag", ""),
                element_role=element_meta.get("role", ""),
            )
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            screenshot = await self._safe_screenshot()
            self._record_step(
                ActionType.TYPE, f"fill({selector})", duration, False,
                screenshot, str(exc),
                pre_url=pre_url,
                selectors=selectors,
            )
            if self._alert_emitter:
                classified = classify_error(exc)
                self._alert_emitter.emit_failure(
                    self._run_id, str(exc), classified.category,
                )
            raise

    async def type(self, selector: str, text: str, **kwargs: Any) -> Any:
        """Type text into a field with value parameterization."""
        pre_url = await self._safe_get_url()

        fill_template = ""
        try:
            from computeruse.step_enrichment import detect_parameterizable_values
            fill_template = detect_parameterizable_values(text)
        except Exception:
            pass

        start = time.monotonic()
        try:
            result = await self._page.type(selector, text, **kwargs)
            duration = int((time.monotonic() - start) * 1000)
            post_url = await self._safe_get_url()
            screenshot = await self._safe_screenshot()
            self._record_step(
                ActionType.TYPE, f"type({selector})", duration, True, screenshot,
                pre_url=pre_url,
                post_url=post_url,
                fill_value_template=fill_template,
            )
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            screenshot = await self._safe_screenshot()
            self._record_step(
                ActionType.TYPE, f"type({selector})", duration, False,
                screenshot, str(exc),
                pre_url=pre_url,
            )
            if self._alert_emitter:
                classified = classify_error(exc)
                self._alert_emitter.emit_failure(
                    self._run_id, str(exc), classified.category,
                )
            raise

    async def select_option(self, selector: str, value: Any = None, **kwargs: Any) -> Any:
        args = (selector,) if value is None else (selector, value)
        return await self._tracked_action(
            "select", f"select_option({selector})",
            self._page.select_option, *args, **kwargs,
        )

    async def press(self, selector: str, key: str, **kwargs: Any) -> Any:
        return await self._tracked_action(
            ActionType.KEY_PRESS, f"press({selector}, {key})",
            self._page.press, selector, key, **kwargs,
        )

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> Any:
        return await self._tracked_action(
            ActionType.WAIT, f"wait_for_selector({selector})",
            self._page.wait_for_selector, selector, **kwargs,
        )

    # -- Passthrough -------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)

    # -- Results access ----------------------------------------------------

    @property
    def steps(self) -> List[StepData]:
        """Return a copy of the recorded steps."""
        return list(self._steps)

    def save_replay(self, path: Optional[str] = None) -> str:
        """Generate and save replay HTML. Returns the file path."""
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

    async def _tracked_action(
        self,
        action_type: str,
        description: str,
        method: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Generic wrapper for tracked page methods."""
        pre_url = await self._safe_get_url()
        start = time.monotonic()
        try:
            result = await method(*args, **kwargs)
            duration = int((time.monotonic() - start) * 1000)
            post_url = await self._safe_get_url()
            screenshot = await self._safe_screenshot()
            self._record_step(
                action_type, description, duration, True, screenshot,
                pre_url=pre_url, post_url=post_url,
            )
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            screenshot = await self._safe_screenshot()
            self._record_step(
                action_type, description, duration, False, screenshot, str(exc),
                pre_url=pre_url,
            )
            if self._alert_emitter:
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
        **kwargs: Any,
    ) -> None:
        self._step_counter += 1
        enrichment = {
            k: v for k, v in kwargs.items()
            if v is not None and v != "" and v != []
        }
        step = StepData(
            step_number=self._step_counter,
            action_type=action_type,
            description=description,
            duration_ms=duration_ms,
            success=success,
            error=error,
            timestamp=datetime.now(timezone.utc),
            screenshot_bytes=screenshot_bytes,
            **enrichment,
        )
        self._steps.append(step)

    async def _safe_get_url(self) -> str:
        """Get current page URL without raising."""
        try:
            return self._page.url if hasattr(self._page, "url") else ""
        except Exception:
            return ""

    async def _safe_screenshot(self) -> Optional[bytes]:
        """Take a screenshot, never raising even if the page is closed."""
        if not self._config.capture_screenshots:
            return None
        try:
            return await self._page.screenshot(type="jpeg", quality=85)
        except Exception:
            return None

    def _build_task_metadata(self) -> dict[str, Any]:
        duration = int((time.monotonic() - self._start_time) * 1000) if self._start_time else 0
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
        screenshots_dir = Path(self._config.output_dir) / "screenshots" / self._run_id
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        for step in self._steps:
            if step.screenshot_bytes:
                path = screenshots_dir / f"step_{step.step_number:03d}.jpg"
                screenshot_data = step.screenshot_bytes
                if isinstance(screenshot_data, str):
                    import base64
                    screenshot_data = base64.b64decode(screenshot_data)
                path.write_bytes(screenshot_data)

    def _save_run_metadata(self) -> None:
        """Save run metadata JSON."""
        runs_dir = Path(self._config.output_dir) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._build_task_metadata()
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

    async def _restore_session(self) -> None:
        """Restore browser session for the configured session key."""
        from computeruse.session_manager import SessionManager

        session_dir = str(Path(self._config.output_dir) / "sessions")
        manager = SessionManager(storage_dir=session_dir)
        await manager.load_session(self._page, self._config.session_key)

    async def _save_session(self) -> None:
        """Save browser session for the configured session key."""
        from computeruse.session_manager import SessionManager

        session_dir = str(Path(self._config.output_dir) / "sessions")
        manager = SessionManager(storage_dir=session_dir)
        await manager.save_session(self._page, self._config.session_key)


@asynccontextmanager
async def track(
    page: Any,
    config: Optional[TrackConfig] = None,
    **kwargs: Any,
) -> AsyncGenerator[TrackedPage, None]:
    """Track a Playwright Page with automatic screenshots, timing, and retries.

    Usage::

        async with track(page) as t:
            await t.goto("https://example.com")
            await t.click("#login")

        print(t.steps)
        t.save_replay("debug.html")
    """
    cfg = config or TrackConfig(**kwargs)
    tracked = TrackedPage(page, cfg)
    tracked._start()

    try:
        if cfg.session_key:
            await tracked._restore_session()

        yield tracked

        if cfg.session_key:
            await tracked._save_session()
    finally:
        tracked._save_outputs()
        tracked._save_run_metadata()
        if cfg.api_url and cfg.api_key:
            try:
                from computeruse._reporting import report_to_api

                has_failure = any(
                    not s.success for s in tracked._steps
                )
                status = "failed" if has_failure else "completed"

                # Extract error info from the first failed step
                error_message = None
                error_category = None
                if has_failure:
                    from computeruse.error_classifier import classify_error_message
                    for s in tracked._steps:
                        if not s.success and getattr(s, "error", None):
                            error_message = str(s.error)[:500]
                            try:
                                classified = classify_error_message(
                                    error_message,
                                )
                                error_category = classified.category
                            except Exception:
                                pass
                            break

                # Run analysis so the dashboard can show suggestions
                analysis = None
                try:
                    import os
                    from computeruse.analyzer import AnalysisConfig, RunAnalyzer

                    llm_key = os.environ.get("ANTHROPIC_API_KEY") or None
                    analysis = await RunAnalyzer(AnalysisConfig(
                        llm_api_key=llm_key,
                    )).analyze(
                        tracked._steps, status, None, "", cfg.output_dir,
                    )
                except Exception:
                    pass

                # Extract start URL from first navigation step
                start_url = ""
                for s in tracked._steps:
                    if s.action_type == "navigate" and getattr(s, "post_url", ""):
                        start_url = s.post_url
                        break

                await report_to_api(
                    api_url=cfg.api_url,
                    api_key=cfg.api_key,
                    task_id=tracked._run_id,
                    task_description="Playwright session",
                    status=status,
                    steps=tracked._steps,
                    cost_cents=0.0,
                    error_category=error_category,
                    error_message=error_message,
                    duration_ms=int(
                        (time.monotonic() - tracked._start_time) * 1000
                    ),
                    created_at=tracked._start_dt,
                    analysis=analysis,
                    url=start_url,
                )
            except Exception:
                pass
