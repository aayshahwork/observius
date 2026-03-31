"""
computeruse/wrap.py — Reliability wrapper for browser_use Agent.

One function call that adds error classification, auto-retry, stuck detection,
cost tracking, step enrichment, and replay generation to any browser_use Agent.

Usage::

    from browser_use import Agent
    from computeruse import wrap

    agent = Agent(task="Extract pricing", llm=llm, browser=browser)
    result = await wrap(agent).run()
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from computeruse.cost import calculate_cost_cents
from computeruse.error_classifier import classify_error
from computeruse.models import ActionType, StepData
from computeruse.replay_generator import ReplayGenerator
from computeruse.retry_policy import should_retry_task
from computeruse.stuck_detector import StuckDetector

logger = logging.getLogger("observius")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrapConfig:
    """Configuration for the reliability wrapper."""

    # Retry
    max_retries: int = 3

    # Stuck detection
    enable_stuck_detection: bool = True
    stuck_screenshot_threshold: int = 4
    stuck_action_threshold: int = 5
    stuck_failure_threshold: int = 3

    # Cost tracking
    track_cost: bool = True

    # Session persistence
    session_key: Optional[str] = None

    # Output
    save_screenshots: bool = True
    output_dir: str = ".observius"
    generate_replay: bool = True

    # Task identification
    task_id: Optional[str] = None

    # API reporting (optional)
    api_url: Optional[str] = None
    api_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def wrap(
    agent: Any,
    config: Optional[WrapConfig] = None,
    **kwargs: Any,
) -> WrappedAgent:
    """Add reliability and observability to a browser_use Agent.

    Args:
        agent: A browser_use ``Agent`` (or any object with a callable ``run``).
        config: Optional :class:`WrapConfig`. If *None*, one is built from
                ``**kwargs``.
        **kwargs: Forwarded to :class:`WrapConfig` when *config* is not given.

    Returns:
        A :class:`WrappedAgent` whose :meth:`run` method adds retry, stuck
        detection, cost tracking, step enrichment, and replay generation.

    Raises:
        TypeError: If *agent* does not have a callable ``run`` attribute.
    """
    run_attr = getattr(agent, "run", None)
    if not callable(run_attr):
        raise TypeError(
            f"Expected an agent with a callable 'run' method, "
            f"got {type(agent).__name__}"
        )
    if config is None:
        config = WrapConfig(**kwargs)
    return WrappedAgent(agent, config)


# ---------------------------------------------------------------------------
# Action-name mapping (mirrors workers/executor.py)
# ---------------------------------------------------------------------------

_ACTION_MAP: Dict[str, ActionType] = {
    # Class-name style (browser_use action_names())
    "GoToUrlAction": ActionType.NAVIGATE,
    "ClickElementAction": ActionType.CLICK,
    "InputTextAction": ActionType.TYPE,
    "ScrollAction": ActionType.SCROLL,
    "ExtractPageContentAction": ActionType.EXTRACT,
    "WaitAction": ActionType.WAIT,
    "DoneAction": ActionType.EXTRACT,
    # snake_case style (alternative format)
    "go_to_url": ActionType.NAVIGATE,
    "click_element": ActionType.CLICK,
    "input_text": ActionType.TYPE,
    "scroll": ActionType.SCROLL,
    "extract_content": ActionType.EXTRACT,
    "wait": ActionType.WAIT,
    "done": ActionType.EXTRACT,
}


# ---------------------------------------------------------------------------
# WrappedAgent
# ---------------------------------------------------------------------------


class WrappedAgent:
    """Browser-use Agent wrapped with reliability features.

    Do not instantiate directly — use :func:`wrap` instead.
    """

    def __init__(self, agent: Any, config: WrapConfig) -> None:
        self._agent = agent
        self._config = config
        self._task_id: str = config.task_id or str(uuid.uuid4())
        self._steps: List[StepData] = []
        self._cost_cents: float = 0.0
        self._error_category: Optional[str] = None
        self._replay_path: Optional[str] = None
        self._created_at: datetime = datetime.now(timezone.utc)
        self._start_time: float = 0.0

        self._stuck_detector: Optional[StuckDetector] = None
        if config.enable_stuck_detection:
            self._stuck_detector = StuckDetector(
                screenshot_threshold=config.stuck_screenshot_threshold,
                action_threshold=config.stuck_action_threshold,
                failure_threshold=config.stuck_failure_threshold,
            )

    # -- Public properties -------------------------------------------------

    @property
    def steps(self) -> List[StepData]:
        return list(self._steps)

    @property
    def cost_cents(self) -> float:
        return self._cost_cents

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def replay_path(self) -> Optional[str]:
        return self._replay_path

    # -- Main entry point --------------------------------------------------

    async def run(self, max_steps: int = 100, **run_kwargs: Any) -> Any:
        """Run the wrapped agent with full reliability layer.

        Feature-detects whether the underlying ``agent.run()`` accepts an
        ``on_step_end`` parameter (browser_use 0.11+).  If it does, real-time
        stuck detection is wired in.  If not, the wrapper still works — stuck
        analysis runs post-execution only.

        Returns the same object that ``agent.run()`` returns (typically an
        ``AgentHistoryList``).
        """
        self._start_time = time.monotonic()
        self._created_at = datetime.now(timezone.utc)
        last_exception: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            try:
                # -- Hook setup via inspect.signature ----------------------
                #
                # Check if agent.run() accepts on_step_end.  If yes, wire in
                # our real-time stuck detection callback.  If the signature
                # can't be inspected (e.g. C extension), skip gracefully.
                try:
                    sig = inspect.signature(self._agent.run)
                    if "on_step_end" in sig.parameters:
                        run_kwargs.setdefault("on_step_end", self._on_step_end)
                except (ValueError, TypeError):
                    pass  # can't inspect — skip real-time hooks

                # -- Run the agent -----------------------------------------
                result = await self._agent.run(
                    max_steps=max_steps, **run_kwargs
                )

                # -- Post-run enrichment -----------------------------------
                self._enrich_steps(result)

                if self._config.track_cost:
                    self._calculate_cost(result)

                if self._stuck_detector:
                    self._stuck_detector.analyze_full_history(self._steps)

                self._save_outputs()
                self._save_run_metadata(status="completed")

                if self._config.api_url and self._config.api_key:
                    from computeruse._reporting import report_to_api

                    await report_to_api(
                        api_url=self._config.api_url,
                        api_key=self._config.api_key,
                        task_id=self._task_id,
                        task_description=getattr(
                            self._agent, "task", ""
                        ),
                        status="completed",
                        steps=self._steps,
                        cost_cents=self._cost_cents,
                        error_category=None,
                        error_message=None,
                        duration_ms=int(
                            (time.monotonic() - self._start_time) * 1000
                        ),
                        created_at=self._created_at,
                    )

                return result

            except Exception as exc:
                last_exception = exc
                classified = classify_error(exc)
                self._error_category = classified.category

                decision = should_retry_task(
                    classified.category,
                    attempt,
                    self._config.max_retries,
                    retry_after_seconds=classified.retry_after_seconds,
                )

                if decision.should_retry:
                    logger.warning(
                        "Attempt %d/%d failed (%s), retrying in %ds",
                        attempt + 1,
                        self._config.max_retries + 1,
                        classified.category,
                        decision.delay_seconds,
                    )
                    await asyncio.sleep(decision.delay_seconds)
                    self._steps = []
                    if self._stuck_detector:
                        self._stuck_detector.reset()
                    continue

                self._save_run_metadata(status="failed", error=str(exc))
                if self._config.api_url and self._config.api_key:
                    from computeruse._reporting import report_to_api

                    await report_to_api(
                        api_url=self._config.api_url,
                        api_key=self._config.api_key,
                        task_id=self._task_id,
                        task_description=getattr(
                            self._agent, "task", ""
                        ),
                        status="failed",
                        steps=self._steps,
                        cost_cents=self._cost_cents,
                        error_category=self._error_category,
                        error_message=str(exc),
                        duration_ms=int(
                            (time.monotonic() - self._start_time) * 1000
                        ),
                        created_at=self._created_at,
                    )
                raise

        # All retries exhausted — should not normally reach here because the
        # last attempt either returns or raises, but guard defensively.
        if last_exception:
            self._save_run_metadata(
                status="failed", error=str(last_exception)
            )
            raise last_exception  # pragma: no cover

    # -- Real-time hook ----------------------------------------------------

    async def _on_step_end(self, agent: Any) -> None:
        """Async callback invoked after each browser_use step.

        Performs real-time stuck detection.  If a stuck pattern is detected,
        calls ``agent.stop()`` to terminate the run early.
        """
        if not self._stuck_detector:
            return
        try:
            history = getattr(agent, "history", None)
            if not history:
                return
            latest = history[-1] if isinstance(history, list) else None
            if latest is None:
                return
            signal = self._stuck_detector.check_agent_step(latest)
            if signal.detected:
                logger.warning(
                    "Stuck agent detected: reason=%s step=%d details=%s",
                    signal.reason,
                    signal.step_number,
                    signal.details,
                )
                stop_fn = getattr(agent, "stop", None)
                if callable(stop_fn):
                    stop_fn()
        except Exception as exc:
            logger.debug("on_step_end stuck check failed: %s", exc)

    # -- Step enrichment (mirrors workers/executor.py) ---------------------

    def _enrich_steps(self, result: Any) -> None:
        """Backfill step data from browser_use AgentHistoryList."""
        try:
            history = getattr(result, "history", None) or []

            screenshots: list[Any] = []
            try:
                if hasattr(result, "screenshots"):
                    screenshots = result.screenshots()
            except Exception:
                pass

            action_names: list[str] = []
            try:
                if hasattr(result, "action_names"):
                    action_names = result.action_names()
            except Exception:
                pass

            for i, agent_step in enumerate(history):
                step = StepData(
                    step_number=i + 1,
                    timestamp=datetime.now(timezone.utc),
                )

                # Screenshot
                if i < len(screenshots) and screenshots[i]:
                    step.screenshot_bytes = screenshots[i]

                # Action type
                if i < len(action_names) and action_names[i]:
                    step.action_type = _ACTION_MAP.get(
                        action_names[i], ActionType.UNKNOWN
                    )

                # Success / error from action results
                step_result = getattr(agent_step, "result", None)
                if step_result and isinstance(step_result, list):
                    errors = []
                    for r in step_result:
                        err = getattr(r, "error", None)
                        if err:
                            errors.append(str(err))
                    if errors:
                        step.success = False
                        step.error = "; ".join(errors[:3])

                # Description from model_output
                mo = getattr(agent_step, "model_output", None)
                if mo:
                    parts: list[str] = []
                    next_goal = getattr(mo, "next_goal", None)
                    if next_goal:
                        parts.append(str(next_goal))
                    eval_prev = getattr(
                        mo, "evaluation_previous_goal", None
                    )
                    if eval_prev:
                        parts.append(f"[eval: {eval_prev}]")
                    if parts:
                        step.description = " | ".join(parts)[:500]

                # Token counts + duration from metadata
                meta = getattr(agent_step, "metadata", None)
                if meta:
                    step.tokens_in = getattr(meta, "input_tokens", 0) or 0
                    step.tokens_out = getattr(meta, "output_tokens", 0) or 0
                    step_dur = getattr(meta, "step_duration", None)
                    if step_dur is not None:
                        step.duration_ms = int(step_dur * 1000)

                self._steps.append(step)

        except Exception as exc:
            logger.warning(
                "Failed to enrich steps from browser_use history: %s", exc
            )

    # -- Cost calculation --------------------------------------------------

    def _calculate_cost(self, result: Any) -> None:
        """Calculate cost in cents from browser_use result or step tokens."""
        try:
            if hasattr(result, "total_cost"):
                total_dollars = result.total_cost()
                if total_dollars and total_dollars > 0:
                    self._cost_cents = total_dollars * 100
                    return
            usage = getattr(result, "usage", None)
            if usage:
                total_cost = getattr(usage, "total_cost", 0.0) or 0.0
                if total_cost > 0:
                    self._cost_cents = total_cost * 100
                    return
        except Exception:
            pass

        # Fallback: sum from per-step token counts
        total_in = sum(s.tokens_in for s in self._steps)
        total_out = sum(s.tokens_out for s in self._steps)
        if total_in or total_out:
            self._cost_cents = calculate_cost_cents(total_in, total_out)

    # -- Output persistence ------------------------------------------------

    def _save_outputs(self) -> None:
        """Save screenshots and generate HTML replay."""
        output_dir = Path(self._config.output_dir)

        if self._config.save_screenshots:
            ss_dir = output_dir / "screenshots" / self._task_id
            ss_dir.mkdir(parents=True, exist_ok=True)
            for i, step in enumerate(self._steps):
                if step.screenshot_bytes:
                    path = ss_dir / f"step_{i}.png"
                    path.write_bytes(step.screenshot_bytes)
                    step.screenshot_path = str(path)

        if self._config.generate_replay and self._steps:
            replay_dir = output_dir / "replays"
            replay_dir.mkdir(parents=True, exist_ok=True)
            replay_path = str(replay_dir / f"{self._task_id}.html")
            try:
                task_metadata: Dict[str, Any] = {
                    "task_id": self._task_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": int(
                        (time.monotonic() - self._start_time) * 1000
                    ),
                    "success": True,
                }
                generator = ReplayGenerator(
                    steps=self._steps, task_metadata=task_metadata
                )
                generator.generate(replay_path)
                self._replay_path = replay_path
            except Exception as exc:
                logger.warning("Failed to generate replay: %s", exc)

    def _save_run_metadata(
        self,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Save run metadata as JSON for external tooling."""
        runs_dir = Path(self._config.output_dir) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        metadata: Dict[str, Any] = {
            "task_id": self._task_id,
            "status": status,
            "step_count": len(self._steps),
            "cost_cents": self._cost_cents,
            "error_category": self._error_category,
            "error": error,
            "created_at": self._created_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int(
                (time.monotonic() - self._start_time) * 1000
            ),
            "steps": [
                {
                    "action_type": s.action_type,
                    "description": s.description,
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "screenshot_path": s.screenshot_path,
                }
                for s in self._steps
            ],
        }
        try:
            path = runs_dir / f"{self._task_id}.json"
            path.write_text(json.dumps(metadata, indent=2))
        except Exception as exc:
            logger.warning("Failed to save run metadata: %s", exc)

    # -- Session helpers (best-effort) -------------------------------------

    async def _restore_session(self) -> None:
        """Restore cookies from filesystem if session_key is set."""
        if not self._config.session_key:
            return
        try:
            from computeruse.session_manager import SessionManager

            page = self._get_agent_page()
            if page is None:
                return
            sm = SessionManager()
            await sm.load_session(page, self._config.session_key)
        except Exception as exc:
            logger.debug("Session restore failed: %s", exc)

    async def _save_session(self) -> None:
        """Save cookies to filesystem if session_key is set."""
        if not self._config.session_key:
            return
        try:
            from computeruse.session_manager import SessionManager

            page = self._get_agent_page()
            if page is None:
                return
            sm = SessionManager()
            await sm.save_session(page, self._config.session_key)
        except Exception as exc:
            logger.debug("Session save failed: %s", exc)

    def _get_agent_page(self) -> Any:
        """Best-effort extraction of the Playwright Page from the agent."""
        # browser_use stores the page in various locations across versions
        for attr_path in (
            "browser_session.page",
            "browser.page",
            "page",
        ):
            obj: Any = self._agent
            for part in attr_path.split("."):
                obj = getattr(obj, part, None)
                if obj is None:
                    break
            if obj is not None:
                return obj
        return None
