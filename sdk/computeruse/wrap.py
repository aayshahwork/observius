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
import base64
import inspect
import json
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from computeruse.cost import calculate_cost_cents
from computeruse.error_classifier import classify_error
from computeruse.models import ActionType, StepData
from computeruse.replay_generator import ReplayGenerator
from computeruse.retry_policy import should_retry_task
from computeruse.stuck_detector import StuckDetector

try:
    from computeruse.failure_analyzer import FailureAnalyzer
except ImportError:
    FailureAnalyzer = None  # type: ignore[assignment,misc]

try:
    from computeruse.recovery_router import RecoveryRouter
except ImportError:
    RecoveryRouter = None  # type: ignore[assignment,misc]

try:
    from computeruse.retry_memory import AttemptRecord, RetryMemory
except ImportError:
    AttemptRecord = None  # type: ignore[assignment,misc]
    RetryMemory = None  # type: ignore[assignment,misc]

try:
    from computeruse.budget import BudgetExceededError, BudgetMonitor
except ImportError:  # budget.py not yet committed
    BudgetMonitor = None  # type: ignore[assignment,misc]
    BudgetExceededError = None  # type: ignore[assignment,misc]

logger = logging.getLogger("pokant")


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
    output_dir: str = ".pokant"
    generate_replay: bool = True

    # Task identification
    task_id: Optional[str] = None

    # Budget enforcement
    max_cost_cents: Optional[float] = None

    # API reporting (optional)
    api_url: Optional[str] = None
    api_key: Optional[str] = None

    # Alerts (optional)
    alerts: Optional[Any] = None  # AlertConfig

    # Analysis (optional)
    analysis: Optional[Any] = None  # AnalysisConfig

    # Adaptive retry (AR3)
    adaptive_retry: bool = True
    diagnostic_model: str = "claude-haiku-4-5-20251001"
    diagnostic_api_key: Optional[str] = None


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
# Token extraction helper (multi-path for browser_use version compat)
# ---------------------------------------------------------------------------


def _extract_step_tokens(agent_step: Any) -> Tuple[int, int]:
    """Extract input/output token counts from a browser_use history step.

    Tries multiple attribute paths to handle different browser_use versions:
    - ``metadata.input_tokens`` / ``metadata.output_tokens`` (< 0.12)
    - ``metadata.tokens_in`` / ``metadata.tokens_out`` (0.12.x alternate)
    - ``agent_step.input_tokens`` / ``agent_step.output_tokens`` (direct)

    Returns:
        ``(tokens_in, tokens_out)`` tuple, defaulting to ``(0, 0)``.
    """
    meta = getattr(agent_step, "metadata", None)
    tokens_in = (
        (getattr(meta, "input_tokens", 0) or 0) if meta else 0
    ) or (
        (getattr(meta, "tokens_in", 0) or 0) if meta else 0
    ) or (
        getattr(agent_step, "input_tokens", 0) or 0
    )
    tokens_out = (
        (getattr(meta, "output_tokens", 0) or 0) if meta else 0
    ) or (
        (getattr(meta, "tokens_out", 0) or 0) if meta else 0
    ) or (
        getattr(agent_step, "output_tokens", 0) or 0
    )
    return int(tokens_in), int(tokens_out)


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
        self._budget: Any = (
            BudgetMonitor(max_cost_cents=config.max_cost_cents)
            if BudgetMonitor is not None
            else None
        )
        self._accumulated_cost: float = 0.0  # inline fallback when BudgetMonitor unavailable
        self._error_category: Optional[str] = None
        self._replay_path: Optional[str] = None
        self._created_at: datetime = datetime.now(timezone.utc)
        self._start_time: float = 0.0
        self._run_saved: bool = False
        self._interrupted: bool = False
        self._analysis: Any = None  # RunAnalysis, set by _run_analysis()

        # Adaptive retry state (AR3)
        self._attempt_history: List[Dict[str, Any]] = []
        self._consecutive_step_failures: int = 0

        self._stuck_detector: Optional[StuckDetector] = None
        if config.enable_stuck_detection:
            self._stuck_detector = StuckDetector(
                screenshot_threshold=config.stuck_screenshot_threshold,
                action_threshold=config.stuck_action_threshold,
                failure_threshold=config.stuck_failure_threshold,
            )

        self._alert_emitter: Optional[Any] = None
        if config.alerts is not None:
            from computeruse.alerts import AlertEmitter

            self._alert_emitter = AlertEmitter(config.alerts)

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

    @property
    def analysis(self) -> Any:
        """Analysis result (:class:`RunAnalysis`), or ``None``."""
        return self._analysis

    @property
    def attempt_history(self) -> List[Dict[str, Any]]:
        """Per-attempt diagnosis and recovery plan history."""
        return list(self._attempt_history)

    # -- Main entry point --------------------------------------------------

    async def run(self, max_steps: int = 100, **run_kwargs: Any) -> Any:
        """Run the wrapped agent with full reliability layer.

        Feature-detects whether the underlying ``agent.run()`` accepts an
        ``on_step_end`` parameter (browser_use 0.11+).  If it does, real-time
        stuck detection and budget enforcement are wired in.  If not, the
        wrapper still works — analysis runs post-execution only.

        Returns the same object that ``agent.run()`` returns (typically an
        ``AgentHistoryList``).
        """
        self._start_time = time.monotonic()
        self._created_at = datetime.now(timezone.utc)
        self._interrupted = False
        self._attempt_history = []
        self._consecutive_step_failures = 0
        last_exception: Optional[Exception] = None

        # Initialize adaptive retry components (AR3).
        # Guarded: if AR modules are not installed, falls back to dumb retry.
        _ar_available = (
            FailureAnalyzer is not None
            and RecoveryRouter is not None
            and RetryMemory is not None
            and AttemptRecord is not None
        )
        retry_memory = RetryMemory(max_entries=3) if RetryMemory else None
        analyzer: Optional[Any] = None
        router = RecoveryRouter() if RecoveryRouter else None

        if self._config.adaptive_retry and _ar_available:
            _diag_key = (
                self._config.diagnostic_api_key
                or os.environ.get("ANTHROPIC_API_KEY")
            )
            analyzer = FailureAnalyzer(
                api_key=_diag_key,
                enable_llm=bool(_diag_key),
                model=self._config.diagnostic_model,
            )
        elif self._config.adaptive_retry and not _ar_available:
            logger.warning(
                "Adaptive retry modules not installed; "
                "falling back to basic retry",
            )

        # Ensure browser_use tracks token usage for cost calculation.
        # The monolith passes calculate_cost=True at Agent construction;
        # SDK users often omit it, causing $0.00 cost on every run.
        try:
            if not getattr(self._agent, "calculate_cost", False):
                self._agent.calculate_cost = True
        except (AttributeError, TypeError):
            pass

        # Install SIGINT handler for graceful interrupt.
        # signal.signal() only works from the main thread; skip gracefully
        # when called from worker threads (e.g. Celery, ThreadPoolExecutor).
        import threading

        _is_main_thread = threading.current_thread() is threading.main_thread()
        original_handler = (
            signal.getsignal(signal.SIGINT) if _is_main_thread else None
        )

        if _is_main_thread:

            def _handle_sigint(signum: int, frame: Any) -> None:
                self._interrupted = True
                stop_fn = getattr(self._agent, "stop", None)
                if callable(stop_fn):
                    stop_fn()

            signal.signal(signal.SIGINT, _handle_sigint)

        try:
            for attempt in range(self._config.max_retries + 1):
                try:
                    # -- Hook setup via inspect.signature ------------------
                    try:
                        sig = inspect.signature(self._agent.run)
                        if "on_step_end" in sig.parameters:
                            run_kwargs.setdefault(
                                "on_step_end", self._on_step_end,
                            )
                    except (ValueError, TypeError):
                        pass  # can't inspect — skip real-time hooks

                    # -- Run the agent -------------------------------------
                    result = await self._agent.run(
                        max_steps=max_steps, **run_kwargs
                    )

                    # -- Post-run enrichment -------------------------------
                    self._enrich_steps(result)

                    if self._config.track_cost:
                        self._calculate_cost(result)
                        if self._alert_emitter:
                            self._alert_emitter.check_cost(
                                self._task_id, self._cost_cents,
                            )

                    if self._stuck_detector:
                        self._stuck_detector.analyze_full_history(
                            self._steps,
                        )

                    await self._run_analysis(status="completed")
                    self._save_outputs()
                    self._save_run_metadata(status="completed")

                    self._attempt_history.append({
                        "attempt": attempt + 1,
                        "status": "completed",
                        "diagnosis": None,
                        "recovery_plan": None,
                    })

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
                                (time.monotonic() - self._start_time)
                                * 1000
                            ),
                            created_at=self._created_at,
                            analysis=self._analysis,
                            attempts=self._attempt_history,
                        )

                    return result

                except Exception as exc:
                    last_exception = exc
                    classified = classify_error(exc)
                    self._error_category = classified.category

                    # Enrich steps from partial history so diagnosis
                    # has data to work with even on failure.
                    try:
                        if (
                            hasattr(self._agent, "history")
                            and self._agent.history
                            and not self._steps
                        ):
                            self._enrich_steps_partial()
                    except Exception:
                        pass

                    # === ADAPTIVE RETRY (AR3) ===
                    if self._config.adaptive_retry and analyzer:
                        # 1. Diagnose the failure
                        diagnosis = await analyzer.analyze(
                            task_description=getattr(
                                self._agent, "task", "",
                            ),
                            steps=self._steps,
                            error=str(exc),
                            error_category=classified.category,
                            last_url=self._get_last_url(),
                            max_steps=max_steps,
                        )

                        # 2. Record in memory
                        failed_actions = [
                            s.description
                            for s in self._steps
                            if not s.success
                        ]
                        retry_memory.record(AttemptRecord(
                            attempt_number=attempt + 1,
                            category=diagnosis.category.value,
                            root_cause=diagnosis.root_cause,
                            retry_hint=diagnosis.retry_hint,
                            progress_achieved=diagnosis.progress_achieved,
                            failed_actions=failed_actions,
                            cost_cents=self._accumulated_cost,
                            analysis_method=diagnosis.analysis_method,
                        ))

                        # 3. Get recovery plan
                        plan = router.plan_recovery(
                            original_task=getattr(
                                self._agent, "task", "",
                            ),
                            diagnosis=diagnosis,
                            attempt_number=attempt + 1,
                            max_attempts=self._config.max_retries + 1,
                            memory=retry_memory,
                        )

                        # 4. Store for dashboard
                        self._attempt_history.append({
                            "attempt": attempt + 1,
                            "status": "failed",
                            "diagnosis": diagnosis.to_dict(),
                            "recovery_plan": plan.to_dict(),
                        })

                        # 5. Should we retry?
                        if not plan.should_retry:
                            logger.info(
                                "Adaptive retry: giving up after "
                                "attempt %d. Reason: %s",
                                attempt + 1,
                                diagnosis.root_cause,
                            )
                            if self._alert_emitter:
                                self._alert_emitter.emit_failure(
                                    self._task_id,
                                    str(exc),
                                    classified.category,
                                )
                            await self._run_analysis(
                                status="failed", error=str(exc),
                            )
                            self._save_run_metadata(
                                status="failed", error=str(exc),
                            )
                            if (
                                self._config.api_url
                                and self._config.api_key
                            ):
                                from computeruse._reporting import (
                                    report_to_api,
                                )

                                await report_to_api(
                                    api_url=self._config.api_url,
                                    api_key=self._config.api_key,
                                    task_id=self._task_id,
                                    task_description=getattr(
                                        self._agent, "task", "",
                                    ),
                                    status="failed",
                                    steps=self._steps,
                                    cost_cents=self._cost_cents,
                                    error_category=self._error_category,
                                    error_message=str(exc),
                                    duration_ms=int(
                                        (
                                            time.monotonic()
                                            - self._start_time
                                        )
                                        * 1000
                                    ),
                                    created_at=self._created_at,
                                    analysis=self._analysis,
                                    attempts=self._attempt_history,
                                )
                            raise

                        # 6. Wait if needed
                        if plan.wait_seconds > 0:
                            logger.info(
                                "Adaptive retry: waiting %ds before "
                                "attempt %d",
                                plan.wait_seconds,
                                attempt + 2,
                            )
                            await asyncio.sleep(plan.wait_seconds)

                        # 7. Apply environment changes
                        await self._apply_recovery_environment(plan)

                        # 8. Inject modified context into agent
                        await self._inject_recovery_context(plan)

                        logger.info(
                            "Adaptive retry: attempt %d. "
                            "Category: %s, Hint: %.80s",
                            attempt + 2,
                            diagnosis.category.value,
                            diagnosis.retry_hint,
                        )

                        # 9. Reset for next attempt
                        self._steps = []
                        if BudgetMonitor is not None:
                            self._budget = BudgetMonitor(
                                max_cost_cents=self._config.max_cost_cents,
                            )
                        self._accumulated_cost = 0.0
                        self._consecutive_step_failures = 0
                        if self._stuck_detector:
                            self._stuck_detector.reset()
                        continue

                    # === DUMB RETRY (existing behavior) ===
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
                        self._attempt_history.append({
                            "attempt": attempt + 1,
                            "status": "failed",
                            "diagnosis": None,
                            "recovery_plan": None,
                        })
                        await asyncio.sleep(decision.delay_seconds)
                        self._steps = []
                        if BudgetMonitor is not None:
                            self._budget = BudgetMonitor(
                                max_cost_cents=self._config.max_cost_cents,
                            )
                        self._accumulated_cost = 0.0
                        if self._stuck_detector:
                            self._stuck_detector.reset()
                        continue

                    if self._alert_emitter:
                        self._alert_emitter.emit_failure(
                            self._task_id, str(exc), classified.category,
                        )

                    await self._run_analysis(
                        status="failed", error=str(exc),
                    )
                    self._save_run_metadata(
                        status="failed", error=str(exc),
                    )
                    if self._config.api_url and self._config.api_key:
                        from computeruse._reporting import report_to_api

                        await report_to_api(
                            api_url=self._config.api_url,
                            api_key=self._config.api_key,
                            task_id=self._task_id,
                            task_description=getattr(
                                self._agent, "task", "",
                            ),
                            status="failed",
                            steps=self._steps,
                            cost_cents=self._cost_cents,
                            error_category=self._error_category,
                            error_message=str(exc),
                            duration_ms=int(
                                (time.monotonic() - self._start_time)
                                * 1000
                            ),
                            created_at=self._created_at,
                            analysis=self._analysis,
                            attempts=self._attempt_history,
                        )
                    raise

            # All retries exhausted — should not normally reach here
            # because the last attempt either returns or raises, but
            # guard defensively.
            if last_exception:
                self._save_run_metadata(
                    status="failed", error=str(last_exception),
                )
                raise last_exception  # pragma: no cover
        finally:
            if _is_main_thread and original_handler is not None:
                signal.signal(signal.SIGINT, original_handler)
            if self._steps and not self._run_saved:
                try:
                    self._save_outputs()
                    status = "interrupted" if self._interrupted else "failed"
                    self._save_run_metadata(status=status)
                except Exception:
                    pass

    # -- Real-time hook ----------------------------------------------------

    async def _on_step_end(self, agent: Any) -> None:
        """Async callback invoked after each browser_use step.

        Performs real-time stuck detection and budget enforcement.
        If a stuck pattern or budget overrun is detected, calls
        ``agent.stop()`` to terminate the run early.
        """
        try:
            history = getattr(agent, "history", None)
            if not history:
                return
            latest = history[-1] if isinstance(history, list) else None
            if latest is None:
                return

            # Stuck detection
            if self._stuck_detector:
                stuck_signal = self._stuck_detector.check_agent_step(latest)
                if stuck_signal.detected:
                    logger.warning(
                        "Stuck agent detected: reason=%s step=%d details=%s",
                        stuck_signal.reason,
                        stuck_signal.step_number,
                        stuck_signal.details,
                    )
                    if self._alert_emitter:
                        self._alert_emitter.emit_stuck(
                            self._task_id, stuck_signal.reason,
                        )
                    stop_fn = getattr(agent, "stop", None)
                    if callable(stop_fn):
                        stop_fn()
                    return

            # Budget enforcement
            if self._config.max_cost_cents is not None:
                tokens_in, tokens_out = _extract_step_tokens(latest)
                if tokens_in or tokens_out:
                    if self._budget is not None:
                        try:
                            self._budget.record_step_cost(tokens_in, tokens_out)
                        except BudgetExceededError:
                            logger.warning(
                                "Budget exceeded: %.2f¢ > %.2f¢. Stopping.",
                                self._budget.total_cost_cents,
                                self._config.max_cost_cents,
                            )
                            stop_fn = getattr(agent, "stop", None)
                            if callable(stop_fn):
                                stop_fn()
                    else:
                        # Inline fallback when budget.py unavailable
                        step_cost = calculate_cost_cents(tokens_in, tokens_out)
                        self._accumulated_cost += step_cost
                        if self._accumulated_cost > self._config.max_cost_cents:
                            logger.warning(
                                "Budget exceeded: %.2f¢ > %.2f¢. Stopping.",
                                self._accumulated_cost,
                                self._config.max_cost_cents,
                            )
                            stop_fn = getattr(agent, "stop", None)
                            if callable(stop_fn):
                                stop_fn()

            # Mid-run intervention (AR3): inject hints after consecutive
            # step failures within a single attempt.
            if self._config.adaptive_retry:
                results = getattr(latest, "result", [])
                step_failed = any(
                    getattr(r, "error", None)
                    for r in (
                        results if isinstance(results, list) else []
                    )
                )
                if step_failed:
                    self._consecutive_step_failures += 1
                else:
                    self._consecutive_step_failures = 0

                if self._consecutive_step_failures >= 2:
                    try:
                        hint = (
                            "HINT: The last 2 actions failed. Try a "
                            "completely different selector strategy or "
                            "navigation path. Consider using text-based "
                            "selectors instead of element indices."
                        )
                        mm = (
                            getattr(agent, "_message_manager", None)
                            or getattr(agent, "message_manager", None)
                        )
                        if mm and hasattr(mm, "add_plan"):
                            mm.add_plan(hint)
                        elif hasattr(agent, "add_new_task"):
                            agent.add_new_task(
                                getattr(agent, "task", "")
                                + f"\n\n{hint}"
                            )
                        logger.debug(
                            "Mid-run hint injected after %d "
                            "consecutive failures",
                            self._consecutive_step_failures,
                        )
                    except Exception:
                        pass  # Must never break execution

        except Exception as exc:
            logger.debug("on_step_end check failed: %s", exc)

    # -- Analysis ----------------------------------------------------------

    async def _run_analysis(
        self, status: str, error: Optional[str] = None,
    ) -> None:
        """Run post-execution analysis if configured.  Never raises."""
        try:
            import os
            from computeruse.analyzer import AnalysisConfig, RunAnalyzer

            config = self._config.analysis
            if config is None:
                # Auto-enable with env key so analysis runs by default
                llm_key = os.environ.get("ANTHROPIC_API_KEY") or None
                config = AnalysisConfig(llm_api_key=llm_key)
            elif not isinstance(config, AnalysisConfig):
                config = AnalysisConfig()
            if not config.enable_analysis:
                return

            self._analysis = await RunAnalyzer(config).analyze(
                self._steps, status, error,
                getattr(self._agent, "task", ""),
                self._config.output_dir,
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

                # Screenshot — browser_use may return base64 strings or raw bytes
                if i < len(screenshots) and screenshots[i]:
                    ss = screenshots[i]
                    if isinstance(ss, str):
                        ss = base64.b64decode(ss)
                    step.screenshot_bytes = ss

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

                # Token counts (multi-path for browser_use version compat)
                step.tokens_in, step.tokens_out = _extract_step_tokens(
                    agent_step,
                )

                # Duration from metadata
                meta = getattr(agent_step, "metadata", None)
                if meta:
                    dur = getattr(meta, "duration_seconds", None)
                    if dur is None:
                        dur = getattr(meta, "step_duration", None)
                    if dur is not None:
                        step.duration_ms = int(dur * 1000)

                self._steps.append(step)

            # Second pass: enrich intent and selectors from model_output
            for enriched_step, raw_step in zip(self._steps, history):
                mo = getattr(raw_step, "model_output", None)
                if mo is None:
                    continue
                # Intent from LLM reasoning (next_goal)
                reasoning = getattr(mo, "next_goal", "")
                if reasoning and not enriched_step.intent:
                    enriched_step.intent = str(reasoning)[:200]
                # Selectors from action objects
                actions = getattr(mo, "action", [])
                if not isinstance(actions, list):
                    actions = [actions] if actions else []
                for action in actions:
                    selector = getattr(action, "selector", None)
                    if selector and not enriched_step.selectors:
                        enriched_step.selectors = [
                            {"type": "css", "value": selector, "confidence": 0.8},
                        ]
                        break

            # browser_use 6.0+: per-step metadata has no token counts.
            # Distribute total tokens from result.usage evenly across steps.
            has_step_tokens = any(
                s.tokens_in > 0 or s.tokens_out > 0 for s in self._steps
            )
            if not has_step_tokens and self._steps:
                usage = getattr(result, "usage", None)
                if usage:
                    total_in = (
                        getattr(usage, "total_prompt_tokens", 0)
                        or getattr(usage, "prompt_tokens", 0)
                        or getattr(usage, "input_tokens", 0)
                        or 0
                    )
                    total_out = (
                        getattr(usage, "total_completion_tokens", 0)
                        or getattr(usage, "completion_tokens", 0)
                        or getattr(usage, "output_tokens", 0)
                        or 0
                    )
                    if total_in or total_out:
                        n = len(self._steps)
                        per_step_in = total_in // n
                        per_step_out = total_out // n
                        for j, s in enumerate(self._steps):
                            s.tokens_in = per_step_in
                            s.tokens_out = per_step_out
                        # Give remainder to last step
                        self._steps[-1].tokens_in += total_in % n
                        self._steps[-1].tokens_out += total_out % n

        except Exception as exc:
            logger.warning(
                "Failed to enrich steps from browser_use history: %s", exc
            )

    # -- Cost calculation --------------------------------------------------

    def _calculate_cost(self, result: Any) -> None:
        """Calculate cost in cents from browser_use result or step tokens."""
        try:
            # Path 1: result.total_cost() — browser_use <6.0
            if hasattr(result, "total_cost") and callable(
                getattr(result, "total_cost")
            ):
                total_dollars = result.total_cost()
                if total_dollars and total_dollars > 0:
                    self._cost_cents = total_dollars * 100
                    return

            # Path 2: result.usage — browser_use 6.0+
            usage = getattr(result, "usage", None)
            if usage:
                total_cost = getattr(usage, "total_cost", 0.0) or 0.0
                if total_cost > 0:
                    self._cost_cents = total_cost * 100
                    return
                # browser_use 6.0 tracks tokens but not dollar cost;
                # calculate from prompt/completion token counts.
                prompt_tokens = (
                    getattr(usage, "total_prompt_tokens", 0) or 0
                )
                completion_tokens = (
                    getattr(usage, "total_completion_tokens", 0) or 0
                )
                if prompt_tokens or completion_tokens:
                    self._cost_cents = calculate_cost_cents(
                        prompt_tokens, completion_tokens
                    )
                    return
        except Exception:
            pass

        # Final fallback: sum from per-step token counts
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
                    screenshot_data = step.screenshot_bytes
                    if isinstance(screenshot_data, str):
                        screenshot_data = base64.b64decode(screenshot_data)
                    path.write_bytes(screenshot_data)
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
                    steps=self._steps, task_metadata=task_metadata,
                    analysis=self._analysis,
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
        self._run_saved = True
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
                self._serialize_step(s) for s in self._steps
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
            # Adaptive retry metadata (AR3)
            "attempts": self._attempt_history,
            "total_attempts": len(self._attempt_history),
            "adaptive_retry_used": self._config.adaptive_retry,
        }
        try:
            path = runs_dir / f"{self._task_id}.json"
            path.write_text(json.dumps(metadata, indent=2, default=str))
        except Exception as exc:
            logger.warning("Failed to save run metadata: %s", exc)

    @staticmethod
    def _serialize_step(s: StepData) -> Dict[str, Any]:
        """Serialize a step for JSON metadata output."""
        data: Dict[str, Any] = {
            "action_type": s.action_type,
            "description": s.description,
            "duration_ms": s.duration_ms,
            "success": s.success,
            "tokens_in": s.tokens_in,
            "tokens_out": s.tokens_out,
            "screenshot_path": s.screenshot_path,
        }
        # Enrichment fields (populated when step_enrichment is active)
        for attr in (
            "selectors", "intent", "intent_detail",
            "pre_url", "post_url",
            "pre_dom_hash", "post_dom_hash",
            "expected_url_pattern", "expected_element", "expected_text",
            "fill_value_template",
            "element_text", "element_tag", "element_role",
            "verification_result",
            "window_title", "control_type", "control_name",
        ):
            val = getattr(s, attr, None)
            if val:
                data[attr] = val
        return data

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

    # -- Adaptive retry helpers (AR3) --------------------------------------

    def _get_last_url(self) -> str:
        """Get the URL from the last step, or empty string."""
        for step in reversed(self._steps):
            url = step.post_url or step.pre_url
            if url:
                return url
        return ""

    def _enrich_steps_partial(self) -> None:
        """Extract whatever step data is available from a failed run.

        Called in the except block BEFORE diagnosis so the analyzer has
        step history to work with.  Less thorough than :meth:`_enrich_steps`
        but captures action types, descriptions, and token counts.
        """
        try:
            history = getattr(self._agent, "history", [])
            if not history:
                return

            action_names: list[str] = []
            try:
                result_proxy = getattr(self._agent, "_history", None)
                if result_proxy and hasattr(result_proxy, "action_names"):
                    action_names = result_proxy.action_names()
            except Exception:
                pass

            for i, agent_step in enumerate(history):
                step = StepData(
                    step_number=i + 1,
                    timestamp=datetime.now(timezone.utc),
                )

                # Action type from action_names or model_output
                if i < len(action_names) and action_names[i]:
                    step.action_type = _ACTION_MAP.get(
                        action_names[i], ActionType.UNKNOWN,
                    )

                # Description from model_output
                mo = getattr(agent_step, "model_output", None)
                if mo:
                    next_goal = getattr(mo, "next_goal", None)
                    if next_goal:
                        step.description = str(next_goal)[:500]

                # Success / error from action results
                step_result = getattr(agent_step, "result", None)
                if step_result and isinstance(step_result, list):
                    for r in step_result:
                        err = getattr(r, "error", None)
                        if err:
                            step.success = False
                            step.error = str(err)[:200]
                            break

                # Token counts
                step.tokens_in, step.tokens_out = _extract_step_tokens(
                    agent_step,
                )

                self._steps.append(step)
        except Exception:
            pass  # Partial enrichment must never crash

    async def _apply_recovery_environment(self, plan: Any) -> None:
        """Apply environment changes from the recovery plan before retrying.

        Best-effort: logs what it can't change rather than raising.
        """
        if plan.fresh_browser:
            try:
                browser_session = (
                    getattr(self._agent, "browser", None)
                    or getattr(self._agent, "browser_session", None)
                )
                if browser_session and hasattr(browser_session, "close"):
                    await browser_session.close()
                    logger.info("Adaptive retry: closed old browser session")
            except Exception:
                logger.debug(
                    "Could not close browser for fresh session",
                    exc_info=True,
                )

        if plan.clear_cookies:
            try:
                browser_session = getattr(self._agent, "browser", None)
                if browser_session:
                    context = getattr(browser_session, "context", None)
                    if context and hasattr(context, "clear_cookies"):
                        await context.clear_cookies()
                        logger.info("Adaptive retry: cleared cookies")
            except Exception:
                logger.debug("Could not clear cookies", exc_info=True)

        if plan.increase_timeout:
            try:
                config = (
                    getattr(self._agent, "controller_config", None)
                    or getattr(self._agent, "config", None)
                )
                if config and hasattr(config, "timeout"):
                    config.timeout = min(config.timeout * 2, 120)
                    logger.info(
                        "Adaptive retry: increased timeout to %ss",
                        config.timeout,
                    )
            except Exception:
                logger.debug("Could not increase timeout", exc_info=True)

        if plan.reduce_max_actions:
            try:
                if hasattr(self._agent, "settings"):
                    current = getattr(
                        self._agent.settings, "max_actions_per_step", 10,
                    )
                    self._agent.settings.max_actions_per_step = max(
                        1, current // 2,
                    )
                    logger.info(
                        "Adaptive retry: reduced max_actions_per_step to %d",
                        self._agent.settings.max_actions_per_step,
                    )
            except Exception:
                logger.debug(
                    "Could not reduce max_actions_per_step", exc_info=True,
                )

        if plan.diagnosis_category == "agent_reasoning":
            try:
                if hasattr(self._agent, "max_failures"):
                    self._agent.max_failures = max(
                        2, self._agent.max_failures // 2,
                    )
                    logger.info(
                        "Adaptive retry: reduced max_failures to %d",
                        self._agent.max_failures,
                    )
            except Exception:
                logger.debug(
                    "Could not reduce max_failures", exc_info=True,
                )

    async def _inject_recovery_context(self, plan: Any) -> None:
        """Inject recovery context into the agent before retry.

        Three strategies in priority order:

        1. ``injected_agent_state`` — create new Agent with state preserved
           plus system message changes.
        2. ``add_new_task`` — inject modified task into existing agent
           (carries forward message history).
        3. Direct task mutation — last resort.
        """
        # Strategy 1: new Agent with injected_agent_state
        if (
            plan.extend_system_message
            and hasattr(self._agent, "state")
            and hasattr(self._agent, "llm")
        ):
            try:
                from browser_use import Agent as BUAgent

                state = self._agent.state
                state.stopped = False
                state.paused = False
                if hasattr(state, "consecutive_failures"):
                    state.consecutive_failures = 0
                if hasattr(state, "follow_up_task"):
                    state.follow_up_task = True

                new_agent = BUAgent(
                    task=plan.modified_task or getattr(
                        self._agent, "task", "",
                    ),
                    llm=self._agent.llm,
                    browser=getattr(self._agent, "browser", None),
                    injected_agent_state=state,
                    extend_system_message=plan.extend_system_message,
                )

                # Re-wire calculate_cost flag from old agent
                try:
                    if getattr(self._agent, "calculate_cost", False):
                        new_agent.calculate_cost = True
                except (AttributeError, TypeError):
                    pass

                self._agent = new_agent
                logger.info(
                    "Adaptive retry: created new Agent with "
                    "injected_agent_state + system message",
                )
                return
            except Exception:
                logger.debug(
                    "injected_agent_state failed, falling back",
                    exc_info=True,
                )

        # Strategy 2: add_new_task
        if plan.modified_task and hasattr(self._agent, "add_new_task"):
            try:
                self._agent.add_new_task(plan.modified_task)
                logger.info("Adaptive retry: injected via add_new_task")
                return
            except Exception:
                logger.debug(
                    "add_new_task failed, falling back", exc_info=True,
                )

        # Strategy 3: direct task mutation
        if plan.modified_task and hasattr(self._agent, "task"):
            try:
                self._agent.task = plan.modified_task
                logger.info(
                    "Adaptive retry: set task directly "
                    "(no history preservation)",
                )
            except Exception:
                logger.debug(
                    "Could not set task directly", exc_info=True,
                )
