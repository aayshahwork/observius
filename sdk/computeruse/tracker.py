"""
computeruse/tracker.py -- Generic observability tracker for custom agent loops.

Provides PokantTracker, a synchronous, framework-agnostic class that lets
developers push step-level data into Pokant without using Browser Use or
Playwright.  Reuses existing SDK infrastructure: StuckDetector, ReplayGenerator,
cost calculation, error classification, and API reporting.

Usage::

    from computeruse import PokantTracker

    tracker = PokantTracker(task_description="Extract pricing")
    tracker.start()

    tracker.record_step(
        action_type="navigate",
        description="Navigated to portal",
        screenshot=page.screenshot(),
        tokens_in=0,
        tokens_out=0,
    )

    tracker.complete(result={"prices": [99, 149]})
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import signal
import threading
import time
import uuid
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from computeruse.cost import calculate_cost_cents
from computeruse.error_classifier import classify_error, classify_error_message
from computeruse.models import StepData
from computeruse.replay_generator import ReplayGenerator
from computeruse.stuck_detector import StuckDetector, StuckSignal

logger = logging.getLogger("pokant")

_NOT_STUCK = StuckSignal()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackerConfig:
    """Configuration for :class:`PokantTracker`."""

    task_description: str = ""

    # Stuck detection
    enable_stuck_detection: bool = True
    stuck_screenshot_threshold: int = 4
    stuck_action_threshold: int = 5
    stuck_failure_threshold: int = 3

    # Output
    save_screenshots: bool = True
    generate_replay: bool = True
    output_dir: str = ".pokant"

    # API reporting
    api_url: Optional[str] = None
    api_key: Optional[str] = None

    # Task ID
    task_id: Optional[str] = None

    # Alerts (optional)
    alerts: Optional[Any] = None  # AlertConfig

    # Analysis (optional)
    analysis: Optional[Any] = None  # AnalysisConfig

    # Browser page for auto-screenshots
    page: Any = None  # Playwright Page, Selenium WebDriver, or any object with a screenshot method
    screenshot_fn: Optional[Any] = None  # Manual screenshot callable: () -> bytes | str


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class PokantTracker:
    """Generic observability tracker for custom agent loops.

    Provides step recording, stuck detection, cost tracking, replay
    generation, and API reporting -- all without requiring Browser Use
    or Playwright.  The public API is fully synchronous.

    Usage::

        tracker = PokantTracker(task_description="My task")
        tracker.start()
        tracker.record_step(action_type="navigate", description="...")
        tracker.complete(result={"key": "value"})
    """

    def __init__(
        self,
        config: Optional[TrackerConfig] = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            config = TrackerConfig(**kwargs)
        self._config = config
        self._task_id: str = config.task_id or str(uuid.uuid4())
        self._steps: List[StepData] = []
        self._step_counter: int = 0
        self._cost_cents: float = 0.0
        self._error_category: Optional[str] = None
        self._replay_path: Optional[str] = None
        self._started: bool = False
        self._finished: bool = False
        self._start_time: float = 0.0
        self._last_step_time: float = 0.0
        self._created_at: datetime = datetime.now(timezone.utc)
        self._stuck_signal: StuckSignal = _NOT_STUCK
        self._original_sigint: Any = None
        self._analysis: Any = None  # RunAnalysis, set by _run_analysis()

        self._stuck_detector: Optional[StuckDetector] = None
        if config.enable_stuck_detection:
            self._stuck_detector = StuckDetector(
                screenshot_threshold=config.stuck_screenshot_threshold,
                action_threshold=config.stuck_action_threshold,
                failure_threshold=config.stuck_failure_threshold,
            )

        self._alert_emitter: Any = None
        if config.alerts is not None:
            from computeruse.alerts import AlertEmitter

            self._alert_emitter = AlertEmitter(config.alerts)

        if config.screenshot_fn is not None:
            self._screenshot_fn = config.screenshot_fn
        else:
            self._screenshot_fn = self._detect_screenshot_fn(config.page)

    # -- Context manager / cleanup -------------------------------------------

    def __enter__(self) -> "PokantTracker":
        if not self._started:
            self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self._finished:
            return False
        if exc_type is not None:
            try:
                self.fail(
                    error=str(exc_val) if exc_val else f"Exception: {exc_type.__name__}",
                    error_category=None,
                )
            except Exception:
                pass  # never mask the original exception
        else:
            self.complete()
        return False

    async def __aenter__(self) -> "PokantTracker":
        if not self._started:
            self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self._finished:
            return False
        if exc_type is not None:
            try:
                self.fail(
                    error=str(exc_val) if exc_val else f"Exception: {exc_type.__name__}",
                    error_category=None,
                )
            except Exception:
                pass  # never mask the original exception
        else:
            self.complete()
        return False

    def __del__(self) -> None:
        """Save partial data if tracker is garbage collected without complete/fail."""
        if self._started and not self._finished:
            try:
                self.fail(
                    error="Tracker destroyed without calling complete() or fail()",
                    error_category="unknown",
                )
            except Exception:
                pass

    # -- Public API -----------------------------------------------------------

    def start(self) -> None:
        """Begin tracking.  Records start time.

        Must be called before :meth:`record_step`.

        Raises:
            RuntimeError: If already started.
        """
        if self._started:
            raise RuntimeError("Tracker already started.")
        self._started = True
        self._start_time = time.monotonic()
        self._last_step_time = self._start_time
        self._created_at = datetime.now(timezone.utc)

        # Register Ctrl+C handler (main thread only).
        # Use weakref so the handler doesn't prevent GC of the tracker.
        if threading.current_thread() is threading.main_thread():
            self._original_sigint = signal.getsignal(signal.SIGINT)
            weak_self = weakref.ref(self)

            def _handle_interrupt(signum: int, frame: Any) -> None:
                tracker = weak_self()
                if tracker is not None:
                    try:
                        tracker.fail(
                            error="Interrupted by user (Ctrl+C)",
                            error_category="unknown",
                        )
                    except Exception:
                        pass
                raise KeyboardInterrupt

            signal.signal(signal.SIGINT, _handle_interrupt)

    def record_step(
        self,
        action_type: str = "unknown",
        description: str = "",
        screenshot: Optional[bytes | str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> StepData:
        """Record a single agent step.

        Args:
            action_type: Category of action (e.g. ``"navigate"``, ``"click"``).
            description: Human-readable summary.
            screenshot: Raw bytes, base64-encoded string, or ``None``.
            tokens_in: Input tokens consumed by LLM for this step.
            tokens_out: Output tokens produced by LLM for this step.
            success: Whether the step succeeded.
            error: Error message if the step failed.
            duration_ms: Explicit duration in milliseconds.
                Auto-calculated from wall clock if ``None``.
            context: Arbitrary debug data (LLM traces, API responses, state).

        Returns:
            The created :class:`StepData`.

        Raises:
            RuntimeError: If tracker not started or already finished.
        """
        if not self._started:
            raise RuntimeError("Tracker not started. Call start() first.")
        if self._finished:
            raise RuntimeError("Tracker already finished.")

        # Auto-capture screenshot from page if none provided
        if screenshot is None and self._screenshot_fn is not None:
            screenshot = self._take_screenshot_sync()

        now = time.monotonic()
        self._step_counter += 1

        if duration_ms is None:
            duration_ms = int((now - self._last_step_time) * 1000)
        self._last_step_time = now

        screenshot_bytes = _normalize_screenshot(screenshot)

        step = StepData(
            step_number=self._step_counter,
            action_type=action_type,
            description=description,
            timestamp=datetime.now(timezone.utc),
            screenshot_bytes=screenshot_bytes,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            error=error,
            duration_ms=duration_ms,
            context=context,
        )
        self._steps.append(step)

        # Accumulate cost
        if tokens_in or tokens_out:
            self._cost_cents = calculate_cost_cents(
                sum(s.tokens_in for s in self._steps),
                sum(s.tokens_out for s in self._steps),
            )
            if self._alert_emitter:
                self._alert_emitter.check_cost(self._task_id, self._cost_cents)

        # Stuck detection
        self._check_stuck()

        return step

    def complete(self, result: Any = None) -> None:
        """Mark tracking as complete (success).

        Saves screenshots, replay HTML, and run metadata to disk.
        Reports to the API if configured.

        Raises:
            RuntimeError: If not started or already finished.
        """
        if not self._started:
            raise RuntimeError("Tracker not started. Call start() first.")
        if self._finished:
            raise RuntimeError("Tracker already finished.")
        self._finished = True
        self._restore_signal()

        self._run_analysis(status="completed")
        self._save_outputs()
        self._save_run_metadata(status="completed", result=result)
        self._report(status="completed")

    def fail(
        self,
        error: Exception | str,
        error_category: Optional[str] = None,
    ) -> None:
        """Mark tracking as failed.

        If *error_category* is not provided, the error is classified
        automatically via :func:`classify_error` (for exceptions) or
        :func:`classify_error_message` (for strings).

        Raises:
            RuntimeError: If not started or already finished.
        """
        if not self._started:
            raise RuntimeError("Tracker not started. Call start() first.")
        if self._finished:
            raise RuntimeError("Tracker already finished.")
        self._finished = True
        self._restore_signal()

        error_str = str(error)
        if error_category is None:
            if isinstance(error, Exception):
                classified = classify_error(error)
            else:
                classified = classify_error_message(error_str)
            error_category = classified.category
        self._error_category = error_category

        if self._alert_emitter:
            self._alert_emitter.emit_failure(
                self._task_id, error_str, error_category,
            )

        self._run_analysis(status="failed", error=error_str)
        self._save_outputs()
        self._save_run_metadata(status="failed", error=error_str)
        self._report(
            status="failed",
            error_category=error_category,
            error_message=error_str,
        )

    def save_replay(self, path: Optional[str] = None) -> str:
        """Generate and save replay HTML.

        Args:
            path: Output file path.  Defaults to
                ``{output_dir}/replays/{task_id}.html``.

        Returns:
            The output file path.
        """
        if path is None:
            replay_dir = Path(self._config.output_dir) / "replays"
            replay_dir.mkdir(parents=True, exist_ok=True)
            path = str(replay_dir / f"{self._task_id}.html")
        metadata = self._build_task_metadata()
        gen = ReplayGenerator(self._steps, metadata, analysis=self._analysis)
        result_path = gen.generate(path)
        self._replay_path = result_path
        return result_path

    async def arecord_step(
        self,
        action_type: str = "unknown",
        description: str = "",
        screenshot: Optional[bytes | str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> StepData:
        """Async version of :meth:`record_step`.

        Awaits async screenshot functions (e.g. Playwright pages).
        Falls back to :meth:`record_step` for all step-recording logic.
        """
        if screenshot is None and self._screenshot_fn is not None:
            try:
                result = self._screenshot_fn()
                if asyncio.iscoroutine(result):
                    screenshot = await result
                else:
                    screenshot = result
            except Exception:
                pass

        return self.record_step(
            action_type=action_type,
            description=description,
            screenshot=screenshot,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            error=error,
            duration_ms=duration_ms,
            context=context,
        )

    # -- Convenience helpers --------------------------------------------------

    def record_llm_step(
        self,
        prompt: str,
        response: str,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
        action_type: str = "llm_call",
        description: str = "",
        **kwargs: Any,
    ) -> StepData:
        """Record an LLM call step with prompt/response context."""
        return self.record_step(
            action_type=action_type,
            description=description or (f"LLM call ({model})" if model else "LLM call"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            context={
                "type": "llm_call",
                "prompt": prompt[:5000],
                "response": response[:5000],
                "model": model,
            },
            **kwargs,
        )

    async def arecord_llm_step(
        self,
        prompt: str,
        response: str,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
        action_type: str = "llm_call",
        description: str = "",
        **kwargs: Any,
    ) -> StepData:
        """Async version of :meth:`record_llm_step`."""
        return await self.arecord_step(
            action_type=action_type,
            description=description or (f"LLM call ({model})" if model else "LLM call"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            context={
                "type": "llm_call",
                "prompt": prompt[:5000],
                "response": response[:5000],
                "model": model,
            },
            **kwargs,
        )

    def record_api_step(
        self,
        method: str,
        url: str,
        status_code: int = 200,
        request_body: Any = None,
        response_body: Any = None,
        success: bool = True,
        **kwargs: Any,
    ) -> StepData:
        """Record an API call step with request/response context."""
        return self.record_step(
            action_type="api_call",
            description=f"{method} {url} \u2192 {status_code}",
            success=success,
            context={
                "type": "api_call",
                "method": method,
                "url": url,
                "status_code": status_code,
                "request_body": _safe_serialize(request_body),
                "response_body": _safe_serialize(response_body),
            },
            **kwargs,
        )

    def record_desktop_step(
        self,
        action_type: str = "desktop_click",
        description: str = "",
        window_title: str = "",
        coordinates: tuple[int, int] | None = None,
        screenshot: bytes | str | None = None,
        **kwargs: Any,
    ) -> StepData:
        """Record a desktop automation step with optional window context.

        Args:
            action_type: Desktop action category (e.g. ``"desktop_click"``,
                ``"desktop_type"``, ``"desktop_launch"``).
            description: Human-readable summary.
            window_title: Title of the target window.
            coordinates: Screen coordinates ``(x, y)`` of the action.
            screenshot: Raw bytes, base64 string, or ``None``.
            **kwargs: Forwarded to :meth:`record_step`.

        Returns:
            The created :class:`StepData`.
        """
        context: Dict[str, Any] = {
            "type": "desktop_action",
            "window_title": window_title,
        }
        if coordinates:
            context["coordinates"] = {"x": coordinates[0], "y": coordinates[1]}

        return self.record_step(
            action_type=action_type,
            description=description,
            screenshot=screenshot,
            context=context,
            **kwargs,
        )

    def record_state_snapshot(
        self,
        state: Dict[str, Any],
        decision: str = "",
        **kwargs: Any,
    ) -> StepData:
        """Record a state snapshot with optional decision reasoning."""
        return self.record_step(
            action_type="state_snapshot",
            description=decision or "State snapshot",
            context={
                "type": "state_snapshot",
                "state": _safe_serialize(state),
                "decision": decision,
            },
            **kwargs,
        )

    # -- Properties -----------------------------------------------------------

    @property
    def steps(self) -> List[StepData]:
        """Copy of the recorded steps."""
        return list(self._steps)

    @property
    def cost_cents(self) -> float:
        """Accumulated cost in US cents."""
        return self._cost_cents

    @property
    def task_id(self) -> str:
        """Task identifier (auto-generated UUID if not provided)."""
        return self._task_id

    @property
    def replay_path(self) -> Optional[str]:
        """Path to the generated replay HTML, or ``None``."""
        return self._replay_path

    @property
    def is_stuck(self) -> bool:
        """Whether the stuck detector has fired."""
        return self._stuck_signal.detected

    @property
    def stuck_reason(self) -> Optional[str]:
        """Human-readable stuck reason, or ``None`` if not stuck."""
        if self._stuck_signal.detected:
            return self._stuck_signal.reason
        return None

    @property
    def analysis(self) -> Any:
        """Analysis result (:class:`RunAnalysis`), or ``None``."""
        return self._analysis

    # -- Analysis -------------------------------------------------------------

    def _run_analysis(self, status: str, error: Optional[str] = None) -> None:
        """Run post-execution analysis if configured.  Never raises."""
        if self._config.analysis is None:
            return
        try:
            from computeruse.analyzer import AnalysisConfig, RunAnalyzer

            config = self._config.analysis
            if not isinstance(config, AnalysisConfig):
                config = AnalysisConfig()
            if not config.enable_analysis:
                return

            analyzer = RunAnalyzer(config)
            self._analysis = analyzer.analyze_sync(
                self._steps, status, error,
                self._config.task_description, self._config.output_dir,
            )
            if status == "failed" or (self._analysis and self._analysis.findings):
                self._log_analysis()
        except Exception:
            logger.debug("Analysis failed", exc_info=True)

    def _log_analysis(self) -> None:
        """Log analysis results."""
        if not self._analysis or not self._analysis.findings:
            return
        logger.info(
            "\n%s\nPOKANT ANALYSIS: %s\n%s",
            "=" * 60, self._analysis.summary, "=" * 60,
        )
        logger.info("Suggestion: %s", self._analysis.primary_suggestion)
        if self._analysis.wasted_steps > 0:
            logger.info(
                "Wasted: %d steps ($%.4f)",
                self._analysis.wasted_steps,
                self._analysis.wasted_cost_cents / 100,
            )
        for f in self._analysis.findings[:5]:
            logger.info(
                "  [Tier %d, %.0f%%] %s: %s", f.tier, f.confidence * 100,
                f.category, f.summary,
            )
            logger.info("    -> %s", f.suggestion)
        logger.info("=" * 60)

    # -- Internal -------------------------------------------------------------

    def _restore_signal(self) -> None:
        """Restore the original SIGINT handler if we installed one."""
        if self._original_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._original_sigint)
            except (ValueError, OSError):
                pass
            self._original_sigint = None

    def _check_stuck(self) -> None:
        """Run stuck detection on the full step history."""
        if self._stuck_detector is None or self._stuck_signal.detected:
            return
        signal = self._stuck_detector.analyze_full_history(self._steps)
        if signal.detected:
            self._stuck_signal = signal
            logger.warning(
                "Stuck detected: reason=%s step=%d details=%s",
                signal.reason,
                signal.step_number,
                signal.details,
            )
            if self._alert_emitter:
                self._alert_emitter.emit_stuck(self._task_id, signal.reason)

    def _detect_screenshot_fn(self, page: Any) -> Any:
        """Auto-detect a screenshot callable from a browser page object."""
        if page is None:
            return None

        # Playwright async Page
        if hasattr(page, "screenshot") and asyncio.iscoroutinefunction(
            page.screenshot
        ):
            return page.screenshot

        # Playwright sync Page or generic sync screenshot
        if hasattr(page, "screenshot") and callable(page.screenshot):
            return page.screenshot

        # Selenium WebDriver
        if hasattr(page, "get_screenshot_as_png") and callable(
            page.get_screenshot_as_png
        ):
            return page.get_screenshot_as_png

        # Generic fallback — try less-common method names
        for name in (
            "get_screenshot",
            "capture_screenshot",
            "take_screenshot",
        ):
            method = getattr(page, name, None)
            if callable(method):
                return method

        logger.warning(
            "Cannot auto-detect screenshot method on %s. "
            "Pass screenshot= to record_step() manually.",
            type(page).__name__,
        )
        return None

    def _take_screenshot_sync(self) -> Optional[bytes]:
        """Take a screenshot synchronously.  Never raises."""
        try:
            result = self._screenshot_fn()
            if asyncio.iscoroutine(result):
                try:
                    asyncio.get_running_loop()
                    # Can't await from sync context with a running loop;
                    # caller should use arecord_step() instead.
                    result.close()  # prevent "coroutine was never awaited"
                    return None
                except RuntimeError:
                    return asyncio.run(result)
            return result
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
            "task_id": self._task_id,
            "task": self._config.task_description,
            "url": "",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration,
            "success": not has_failure,
        }

    def _save_outputs(self) -> None:
        """Save screenshots and generate replay HTML."""
        output_dir = Path(self._config.output_dir)

        if self._config.save_screenshots:
            has_screenshots = any(
                s.screenshot_bytes for s in self._steps
            )
            if has_screenshots:
                ss_dir = output_dir / "screenshots" / self._task_id
                ss_dir.mkdir(parents=True, exist_ok=True)
                for step in self._steps:
                    if step.screenshot_bytes:
                        path = ss_dir / f"step_{step.step_number:03d}.png"
                        data = step.screenshot_bytes
                        if isinstance(data, str):
                            data = base64.b64decode(data)
                        path.write_bytes(data)
                        step.screenshot_path = str(path)

        if self._config.generate_replay and self._steps:
            self.save_replay()

    def _save_run_metadata(
        self,
        status: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """Save run metadata as JSON."""
        runs_dir = Path(self._config.output_dir) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        metadata: Dict[str, Any] = {
            "task_id": self._task_id,
            "task_description": self._config.task_description,
            "status": status,
            "step_count": len(self._steps),
            "cost_cents": self._cost_cents,
            "error_category": self._error_category,
            "error": error,
            "result": result,
            "created_at": self._created_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int(
                (time.monotonic() - self._start_time) * 1000
            ),
            "steps": [
                {
                    "step_number": s.step_number,
                    "action_type": s.action_type,
                    "description": s.description,
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "error": s.error,
                    "screenshot_path": s.screenshot_path,
                    "context": s.context,
                }
                for s in self._steps
            ],
            "analysis": {
                "summary": self._analysis.summary,
                "primary_suggestion": self._analysis.primary_suggestion,
                "wasted_steps": self._analysis.wasted_steps,
                "wasted_cost_cents": self._analysis.wasted_cost_cents,
                "tiers_executed": self._analysis.tiers_executed,
                "findings": [
                    {
                        "tier": f.tier,
                        "category": f.category,
                        "summary": f.summary,
                        "suggestion": f.suggestion,
                        "confidence": f.confidence,
                    }
                    for f in self._analysis.findings
                ],
            } if self._analysis else None,
        }
        try:
            path = runs_dir / f"{self._task_id}.json"
            path.write_text(json.dumps(metadata, indent=2, default=str))
        except Exception as exc:
            logger.warning("Failed to save run metadata: %s", exc)

    def _report(
        self,
        status: str,
        error_category: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Report to Pokant API if configured.  Never raises."""
        if not self._config.api_url or not self._config.api_key:
            return
        try:
            from computeruse._reporting import _report_to_api_sync

            _report_to_api_sync(
                api_url=self._config.api_url,
                api_key=self._config.api_key,
                task_id=self._task_id,
                task_description=self._config.task_description,
                status=status,
                steps=self._steps,
                cost_cents=self._cost_cents,
                error_category=error_category,
                error_message=error_message,
                duration_ms=int(
                    (time.monotonic() - self._start_time) * 1000
                ),
                created_at=self._created_at,
                analysis={
                    "summary": self._analysis.summary,
                    "primary_suggestion": self._analysis.primary_suggestion,
                    "wasted_steps": self._analysis.wasted_steps,
                    "wasted_cost_cents": self._analysis.wasted_cost_cents,
                    "tiers_executed": self._analysis.tiers_executed,
                    "findings": [
                        {
                            "tier": f.tier,
                            "category": f.category,
                            "summary": f.summary,
                            "suggestion": f.suggestion,
                            "confidence": f.confidence,
                        }
                        for f in self._analysis.findings
                    ],
                } if self._analysis else None,
            )
        except Exception as exc:
            logger.debug("API reporting failed: %s", exc)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _safe_serialize(obj: Any, max_length: int = 10000) -> Any:
    """Safely serialize an object for context storage. Truncates large values."""
    if obj is None:
        return None
    try:
        s = json.dumps(obj, default=str)
        if len(s) > max_length:
            return s[:max_length] + "...(truncated)"
        return obj
    except (TypeError, ValueError):
        return str(obj)[:max_length]


def create_tracker(
    task_description: str = "",
    page: Any = None,
    screenshot_fn: Any = None,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs: Any,
) -> PokantTracker:
    """Convenience factory for :class:`PokantTracker`.

    Args:
        task_description: Human-readable description of the task.
        page: Browser page for auto-screenshots (optional).
        screenshot_fn: Manual screenshot callable (optional).
        api_url: Pokant API base URL (optional).
        api_key: Pokant API key (optional).
        **kwargs: Forwarded to :class:`TrackerConfig`.

    Returns:
        A new :class:`PokantTracker`.
    """
    return PokantTracker(
        TrackerConfig(
            task_description=task_description,
            page=page,
            screenshot_fn=screenshot_fn,
            api_url=api_url,
            api_key=api_key,
            **kwargs,
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_screenshot(screenshot: Optional[bytes | str]) -> Optional[bytes]:
    """Convert screenshot input to bytes."""
    if screenshot is None:
        return None
    if isinstance(screenshot, bytes):
        return screenshot
    try:
        return base64.b64decode(screenshot)
    except Exception:
        return screenshot.encode("utf-8")
