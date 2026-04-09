"""
workers/executor.py — Core task execution engine with Anthropic tool-use API.

The TaskExecutor acquires a browser, navigates to a URL, runs a screenshot-based
LLM agent loop using Anthropic's tool-use API, captures step data, and returns
structured results.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from rich.console import Console

from workers.browser_manager import BrowserManager
from workers.captcha_solver import CaptchaSolver
from workers.config import worker_settings
from workers.credential_injector import CredentialInjector
from workers.models import ActionType, StepData, TaskConfig, TaskResult
from workers.output_validator import OutputValidator, ValidationError
from workers.stuck_detector import StuckDetector

logger = logging.getLogger(__name__)
console = Console()


class TaskExecutionError(RuntimeError):
    """Raised when the browser_use agent fails during task execution."""


# Claude Sonnet pricing (per million tokens).
_COST_PER_M_INPUT = 3.00
_COST_PER_M_OUTPUT = 15.00

# Key mapping: Anthropic computer_use key names → Playwright key names.
_KEY_MAP: Dict[str, str] = {
    "Return": "Enter",
    "BackSpace": "Backspace",
    "Tab": "Tab",
    "Escape": "Escape",
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "super": "Meta",
    "space": " ",
    "Space": " ",
}

# Maximum screenshot width sent to Claude (pixels).
_MAX_SCREENSHOT_WIDTH = 1280

# Context window management: keep first message + last N.
_MAX_CONTEXT_MESSAGES = 20

# Stuck detection frequency (every N steps).
_STUCK_CHECK_INTERVAL = 5


@dataclass
class NativeResult:
    """Return value from _execute_native."""

    result_data: Optional[Dict[str, Any]]
    cost_cents: float
    total_tokens_in: int
    total_tokens_out: int

# Tool definitions for the Anthropic messages API.
_TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The URL to navigate to."}},
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": "Click an element on the page identified by CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string", "description": "CSS selector of the element to click."}},
            "required": ["selector"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into an input element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input element."},
                "text": {"type": "string", "description": "The text to type."},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page up or down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."},
                "pixels": {"type": "integer", "description": "Pixels to scroll. Default 300.", "default": 300},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for a short duration.",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "description": "Seconds to wait (max 10).", "maximum": 10}},
            "required": ["seconds"],
        },
    },
    {
        "name": "inject_credentials",
        "description": (
            "Fill in login credentials on the current page. Use this when you see a login form. "
            "Provide CSS selectors for the username and password fields. "
            "The system will securely inject the credentials — do NOT type them yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username_selector": {"type": "string", "description": "CSS selector for the username/email input."},
                "password_selector": {"type": "string", "description": "CSS selector for the password input."},
            },
        },
    },
    {
        "name": "solve_captcha",
        "description": (
            "Detect and solve a CAPTCHA on the current page "
            "(reCAPTCHA v2, hCaptcha, or Cloudflare Turnstile). "
            "Auto-detects type if not specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "captcha_type": {
                    "type": "string",
                    "enum": ["recaptcha_v2", "hcaptcha", "turnstile"],
                    "description": "CAPTCHA type. Omit to auto-detect.",
                },
            },
        },
    },
    {
        "name": "done",
        "description": "Signal that the task is complete. Include extracted result data if an output schema was specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "result": {"type": "object", "description": "Extracted data matching the output schema."},
                "message": {"type": "string", "description": "Completion summary."},
            },
        },
    },
]


class TaskExecutor:
    """Screenshot-based LLM agent loop using the Anthropic tool-use API."""

    def __init__(
        self,
        config: TaskConfig,
        browser_manager: BrowserManager,
        llm_client: Any,
        use_cloud: bool = False,
        shutdown_check: Optional[Callable[[], bool]] = None,
        step_data: Optional[List[StepData]] = None,
        model: str = "claude-sonnet-4-6",
        account_id: Optional[str] = None,
    ) -> None:
        self.config = config
        self.browser_manager = browser_manager
        self.llm_client = llm_client
        self.use_cloud = use_cloud
        self.shutdown_check = shutdown_check
        self._shared_step_data = step_data
        self.model = model
        self.account_id = account_id
        self.steps: List[StepData] = []
        self._stuck_detector = StuckDetector()
        self._output_validator = OutputValidator()

        # Budget monitor — only active when max_cost_cents is configured
        self._budget_monitor = None
        if config.max_cost_cents:
            try:
                from computeruse.budget import BudgetMonitor
                self._budget_monitor = BudgetMonitor(
                    max_cost_cents=float(config.max_cost_cents)
                )
            except ImportError:
                pass

    async def execute(self) -> TaskResult:
        """Execute the task through the Plan-Act-Validate loop.

        1. Create backend, planner, validator, and budget.
        2. Delegate to run_pav_loop for orchestration.
        3. PAV loop handles: plan decomposition, subgoal execution,
           validation, replanning, and result construction.
        """
        task_id = str(uuid.uuid4())
        start_time = time.monotonic()
        self.steps = self._shared_step_data if self._shared_step_data is not None else []

        try:
            from workers.backends.registry import backend_for_task
            from workers.memory.episodic import EpisodicMemory
            from workers.memory.store import MemoryStore
            from workers.pav.loop import run_pav_loop
            from workers.pav.planner import Planner
            from workers.pav.validator import Validator
            from workers.reliability import CircuitBreaker, run_repair
            from workers.shared_types import Budget

            backend = backend_for_task(self.config)
            llm_client = self._get_llm_client()
            planner = Planner(llm_client=llm_client)
            validator = Validator(llm_client=llm_client)
            circuit_breaker = CircuitBreaker(max_consecutive=3)
            budget = Budget(
                max_steps=self.config.max_steps or 50,
                max_cost_cents=float(self.config.max_cost_cents or 0),
                max_seconds=float(self.config.timeout_seconds or 0),
            )

            memory_store = MemoryStore(worker_settings.DATABASE_URL)
            await memory_store.init()
            episodic_memory = EpisodicMemory(
                store=memory_store,
                run_id=str(task_id),
                tenant_id=str(self.account_id or ""),
            )
            task_domain = urlparse(self.config.url).netloc if self.config.url else ""

            async def repair_fn(outcome, subgoal, be, pl, va):
                return await run_repair(
                    outcome, subgoal, be, pl, va,
                    circuit_breaker=circuit_breaker,
                    episodic_memory=episodic_memory,
                    domain=task_domain,
                )

            return await run_pav_loop(
                task_config=self.config,
                backend=backend,
                planner=planner,
                validator=validator,
                budget=budget,
                repair_fn=repair_fn,
                on_step=self._persist_step,
            )

        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            return TaskResult(
                task_id=task_id,
                status="failed",
                success=False,
                error=str(exc),
                steps=len(self.steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=0.0,
                total_tokens_in=sum(s.tokens_in for s in self.steps),
                total_tokens_out=sum(s.tokens_out for s in self.steps),
                step_data=self.steps,
            )

    def _get_llm_client(self) -> Any:
        """Return an async messages client for planner and validator."""
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(
            api_key=worker_settings.ANTHROPIC_API_KEY,
        ).messages

    def _persist_step(self, step_result: Any) -> None:
        """Append StepResult to shared step list for real-time visibility."""
        from workers.pav.loop import step_result_to_step_data
        step_data = step_result_to_step_data(step_result, len(self.steps) + 1)
        self.steps.append(step_data)

    # ------------------------------------------------------------------
    # Legacy methods below — preserved for backward compatibility.
    # No longer called by execute() after the PAV rewire.
    # ------------------------------------------------------------------

    async def _execute_legacy_noop(self) -> None:
        """Placeholder — old execute() body superseded by run_pav_loop."""
        # -- Session manager setup (lazy, only when session_id is present) --
        session_manager = None
        if self.account_id and self.config.session_id:
            try:
                from workers.db import get_async_session_factory
                from workers.encryption import EncryptionKeyCache
                from workers.session_manager import SessionManager
                import redis as redis_lib

                async_db_session = get_async_session_factory()()
                encryption_cache = EncryptionKeyCache(worker_settings.ENCRYPTION_MASTER_KEY)
                redis_client = redis_lib.Redis.from_url(worker_settings.REDIS_URL, decode_responses=True)
                session_manager = SessionManager(async_db_session, encryption_cache, redis_client)
            except Exception as exc:
                logger.warning("Failed to initialise SessionManager: %s", exc)

        try:
            # -- Step 2: Browser setup --
            browser = await self.browser_manager.get_browser(self.use_cloud)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )

            # -- Hook B/C: Restore session cookies --
            session_restored = False
            if session_manager:
                try:
                    domain = urlparse(self.config.url).netloc
                    cookies = await session_manager.load_session(
                        account_id=uuid.UUID(self.account_id),
                        domain=domain,
                    )
                    if cookies:
                        await context.add_cookies(cookies)
                        session_restored = True
                        logger.info("Restored %d cookies for %s", len(cookies), domain)
                        from workers.metrics import celery_session_restored_total

                        celery_session_restored_total.labels(domain=domain).inc()
                except Exception as exc:
                    logger.warning("Session restore failed: %s", exc)

            page = await context.new_page()
            await self.browser_manager.apply_stealth(page, task_id)

            # -- Step 3: Navigate (with retry) --
            step_start = time.monotonic()
            await self._navigate_with_retry(page, self.config.url)
            screenshot_bytes = await page.screenshot(type="png")
            self.steps.append(
                StepData(
                    step_number=1,
                    timestamp=datetime.now(timezone.utc),
                    action_type=ActionType.NAVIGATE,
                    description=f"Navigated to {self.config.url}",
                    screenshot_bytes=screenshot_bytes,
                    duration_ms=int((time.monotonic() - step_start) * 1000),
                    success=True,
                )
            )

            # -- Hook D: Verify restored session --
            if session_restored:
                domain = urlparse(self.config.url).netloc
                if not await self._verify_session(page, domain):
                    logger.warning("Stale session for %s", domain)
                    session_restored = False
                    from workers.metrics import celery_session_stale_total

                    celery_session_stale_total.labels(domain=domain).inc()

            # -- Step 4: Run executor --
            if self.config.executor_mode == "native":
                native_result = await self._execute_native(page)
                result_data = native_result.result_data
                cost_cents = native_result.cost_cents
                total_in = native_result.total_tokens_in
                total_out = native_result.total_tokens_out
                success = result_data is not None
            else:
                raw_result = await self._execute_with_agent(browser, self.config)
                result_data = None
                if hasattr(raw_result, "final_result"):
                    result_data = raw_result.final_result()

                # -- Validate output against schema --
                if result_data and self.config.output_schema:
                    try:
                        result_data = self._output_validator.validate(
                            result_data, self.config.output_schema,
                        )
                    except ValidationError as val_err:
                        logger.warning(
                            "browser_use output validation failed: %s",
                            val_err.message,
                        )
                        # Keep unvalidated result — browser_use manages its
                        # own browser session so we can't extract from the page.

                # -- Step 5: Enrich steps from browser_use history --
                self._enrich_steps_from_history(raw_result)

                # -- Stuck analysis on enriched steps --
                stuck_signal = self._stuck_detector.analyze_full_history(self.steps)
                if stuck_signal.detected:
                    logger.warning(
                        "Post-execution stuck analysis: reason=%s step=%d details=%s",
                        stuck_signal.reason,
                        stuck_signal.step_number,
                        stuck_signal.details,
                    )
                    from workers.metrics import (
                        celery_stuck_action_repetition_total,
                        celery_stuck_failure_spiral_total,
                        celery_stuck_visual_stagnation_total,
                    )

                    _stuck_metric_map = {
                        "visual_stagnation": celery_stuck_visual_stagnation_total,
                        "action_repetition": celery_stuck_action_repetition_total,
                        "failure_spiral": celery_stuck_failure_spiral_total,
                    }
                    counter = _stuck_metric_map.get(stuck_signal.reason)
                    if counter:
                        counter.labels(task_name="computeruse.execute_task").inc()

                cost_cents = self._calculate_cost_from_result(raw_result)
                total_in = sum(s.tokens_in for s in self.steps)
                total_out = sum(s.tokens_out for s in self.steps)

                # Determine success from browser_use result if available
                success = True
                if hasattr(raw_result, "is_done"):
                    try:
                        success = bool(raw_result.is_done())
                    except Exception:
                        pass

            # -- Hook G: Save session cookies on success --
            if session_manager and self.config.session_id and session_restored is not False:
                try:
                    domain = urlparse(self.config.url).netloc
                    cookies = await context.cookies()
                    await session_manager.save_session(
                        account_id=uuid.UUID(self.account_id),
                        domain=domain,
                        cookies=cookies,
                    )
                    logger.info("Saved %d cookies for %s", len(cookies), domain)
                    from workers.metrics import celery_session_saved_total

                    celery_session_saved_total.labels(domain=domain).inc()
                except Exception as exc:
                    logger.warning("Failed to save session: %s", exc)

            return TaskResult(
                task_id=task_id,
                status="completed",
                success=success,
                result=result_data,
                steps=len(self.steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=cost_cents,
                total_tokens_in=total_in,
                total_tokens_out=total_out,
                step_data=self.steps,
            )

        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            return TaskResult(
                task_id=task_id,
                status="failed",
                success=False,
                error=str(exc),
                steps=len(self.steps),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                cost_cents=0.0,
                total_tokens_in=sum(s.tokens_in for s in self.steps),
                total_tokens_out=sum(s.tokens_out for s in self.steps),
                step_data=self.steps,
            )

        finally:
            if async_db_session is not None:
                try:
                    await async_db_session.close()
                except Exception as exc:
                    logger.warning("Error closing async DB session: %s", exc)
            if browser is not None:
                try:
                    await self.browser_manager.release_browser(browser)
                except Exception as exc:
                    logger.warning("Error releasing browser: %s", exc)

    async def _execute_with_agent(self, browser: Any, config: TaskConfig) -> Any:
        prompt = self._build_task_prompt(config)

        from browser_use.llm.anthropic.chat import ChatAnthropic

        llm = ChatAnthropic(
            model=self.model,
            api_key=worker_settings.ANTHROPIC_API_KEY,
            timeout=60,
        )

        from browser_use import Agent
        from browser_use.browser.session import BrowserSession

        # browser-use 0.11+ requires a BrowserSession, not a raw Playwright Browser.
        browser_session = BrowserSession(
            headless=True,
            enable_default_extensions=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        agent = Agent(
            task=prompt,
            llm=llm,
            browser=browser_session,
            register_new_step_callback=self._on_agent_step,
            calculate_cost=True,
            use_vision=True,
        )

        try:
            import inspect

            run_kwargs: dict[str, Any] = {"max_steps": config.max_steps}
            if "on_step_end" in inspect.signature(agent.run).parameters:
                run_kwargs["on_step_end"] = self._on_step_end
            result = await agent.run(**run_kwargs)
            return result
        except Exception as exc:
            raise TaskExecutionError(f"Browser Use agent failed: {exc}") from exc

    def _on_agent_step(self, *args: Any, **kwargs: Any) -> None:
        step_number = len(self.steps) + 1
        step = StepData(
            step_number=step_number,
            timestamp=datetime.now(timezone.utc),
            action_type=ActionType.UNKNOWN,
            description=str(args[0]) if args else "step",
            screenshot_bytes=None,
            success=True,
        )
        self.steps.append(step)
        console.log(f"[dim]Step {step_number}[/]")

    async def _on_step_end(self, agent: Any) -> None:
        """Async hook for real-time stuck detection during agent.run()."""
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
                from workers.metrics import (
                    celery_stuck_action_repetition_total,
                    celery_stuck_failure_spiral_total,
                    celery_stuck_visual_stagnation_total,
                )

                _metric_map = {
                    "visual_stagnation": celery_stuck_visual_stagnation_total,
                    "action_repetition": celery_stuck_action_repetition_total,
                    "failure_spiral": celery_stuck_failure_spiral_total,
                }
                counter = _metric_map.get(signal.reason)
                if counter:
                    counter.labels(task_name="computeruse.execute_task").inc()
                stop_fn = getattr(agent, "stop", None)
                if callable(stop_fn):
                    stop_fn()
        except Exception as exc:
            logger.debug("on_step_end stuck check failed: %s", exc)

    # ------------------------------------------------------------------
    # Session verification
    # ------------------------------------------------------------------

    async def _verify_session(self, page: Any, expected_domain: str) -> bool:
        """Check if a restored session is still valid.

        Conservative: returns ``True`` when uncertain so that valid sessions
        are never incorrectly discarded.
        """
        try:
            url = page.url.lower()
            login_patterns = ["/login", "/signin", "/sign-in", "/auth", "/sso", "/oauth"]
            if any(p in url for p in login_patterns):
                return False

            password_visible = await page.evaluate(
                """() => {
                    const inputs = document.querySelectorAll('input[type="password"]');
                    return Array.from(inputs).some(input => {
                        const rect = input.getBoundingClientRect();
                        const style = window.getComputedStyle(input);
                        return rect.width > 0 && rect.height > 0
                            && style.display !== 'none' && style.visibility !== 'hidden';
                    });
                }"""
            )
            if password_visible:
                return False

            return True
        except Exception:
            return True  # don't break on verification errors

    # ------------------------------------------------------------------
    # Navigation with transient-error retry
    # ------------------------------------------------------------------

    async def _navigate_with_retry(self, page: Any, url: str, max_attempts: int = 3) -> None:
        """Navigate to *url* with retry for transient network errors."""
        for attempt in range(max_attempts):
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                return
            except Exception as exc:
                msg = str(exc).lower()
                transient_patterns = ["net::err_", "timeout", "connection refused", "dns"]
                is_transient = any(p in msg for p in transient_patterns)
                if not is_transient or attempt == max_attempts - 1:
                    raise
                delay = 2**attempt
                logger.warning(
                    "Navigation failed (attempt %d/%d): %s. Retrying in %ds",
                    attempt + 1,
                    max_attempts,
                    exc,
                    delay,
                )
                from workers.metrics import celery_navigation_retry_total

                celery_navigation_retry_total.inc()
                await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Post-run enrichment from browser_use AgentHistoryList
    # ------------------------------------------------------------------

    def _enrich_steps_from_history(self, result: Any) -> None:
        """Backfill step data from browser_use's AgentHistoryList.

        Steps in self.steps: index 0 is the NAVIGATE step (captured by executor).
        Steps from browser_use history: index 0 is the first AGENT step.
        So agent history entry i maps to self.steps[i + 1].
        """
        try:
            history = getattr(result, "history", None) or []
            screenshots = []
            try:
                screenshots = result.screenshots() if hasattr(result, "screenshots") else []
            except Exception as e:
                logger.warning("Failed to retrieve screenshots from browser_use result: %s", e)

            logger.info(
                "Enriching steps: history=%d screenshots=%d existing_steps=%d",
                len(history), len(screenshots), len(self.steps),
            )

            action_names = []
            try:
                action_names = result.action_names() if hasattr(result, "action_names") else []
            except Exception:
                pass

            for i, agent_step in enumerate(history):
                step_index = i + 1  # offset for navigate step
                if step_index >= len(self.steps):
                    # History has more entries than callback produced — append
                    self.steps.append(
                        StepData(
                            step_number=len(self.steps) + 1,
                            timestamp=datetime.now(timezone.utc),
                        )
                    )

                step = self.steps[step_index]

                # Screenshot — browser_use returns base64 strings, not bytes
                if i < len(screenshots) and screenshots[i]:
                    screenshot = screenshots[i]
                    if isinstance(screenshot, str):
                        screenshot = base64.b64decode(screenshot)
                    step.screenshot_bytes = screenshot

                # Action type from action_names()
                if i < len(action_names) and action_names[i]:
                    step.action_type = self._map_browser_use_action(action_names[i])

                # Success/failure from action results
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
                    parts = []
                    next_goal = getattr(mo, "next_goal", None)
                    if next_goal:
                        parts.append(str(next_goal))
                    eval_prev = getattr(mo, "evaluation_previous_goal", None)
                    if eval_prev:
                        parts.append(f"[eval: {eval_prev}]")
                    if parts:
                        step.description = " | ".join(parts)[:500]

                # Token counts from step metadata
                meta = getattr(agent_step, "metadata", None)
                if meta:
                    step.tokens_in = getattr(meta, "input_tokens", 0) or 0
                    step.tokens_out = getattr(meta, "output_tokens", 0) or 0
                    step_dur = getattr(meta, "step_duration", None)
                    if step_dur is not None:
                        step.duration_ms = int(step_dur * 1000)

            # Intent enrichment — pure Python, no page access, runs on all steps
            try:
                from workers.intelligence import enrich_step_context
                for step in self.steps:
                    if step.context is None:
                        ctx = enrich_step_context(
                            str(step.action_type), step.description or ""
                        )
                        if ctx:
                            step.context = ctx
            except Exception as enrich_exc:
                logger.debug("Step intent enrichment failed: %s", enrich_exc)

        except Exception as e:
            logger.warning("Failed to enrich steps from browser_use history: %s", e)

    def _calculate_cost_from_result(self, result: Any) -> float:
        """Extract total cost in cents from browser_use result.

        Tries result.total_cost() first (returns dollars), then falls back to
        summing token counts from enriched steps.
        """
        try:
            # browser_use provides total_cost() when calculate_cost=True
            if hasattr(result, "total_cost"):
                total_dollars = result.total_cost()
                if total_dollars and total_dollars > 0:
                    return total_dollars * 100  # dollars → cents
            # Fallback: sum from usage summary
            usage = getattr(result, "usage", None)
            if usage:
                total_cost = getattr(usage, "total_cost", 0.0) or 0.0
                if total_cost > 0:
                    return total_cost * 100
        except Exception:
            pass

        # Final fallback: compute from per-step token counts
        try:
            total_in = sum(s.tokens_in for s in self.steps)
            total_out = sum(s.tokens_out for s in self.steps)
            if total_in or total_out:
                return (total_in * _COST_PER_M_INPUT + total_out * _COST_PER_M_OUTPUT) / 1_000_000 * 100
        except Exception as e:
            logger.warning("Failed to calculate cost: %s", e)

        return 0.0

    @staticmethod
    def _map_browser_use_action(action_name: str) -> ActionType:
        """Map browser_use action name strings to ActionType enum."""
        mapping = {
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
        return mapping.get(action_name, ActionType.UNKNOWN)

    def _build_task_prompt(self, config: TaskConfig) -> str:
        """Build the task prompt passed to the browser_use Agent."""
        lines = [
            f"Go to {config.url} and complete the following task:",
            config.task,
        ]
        if config.output_schema:
            lines.append(f"Extract data matching this schema: {json.dumps(config.output_schema)}")
        if config.credentials:
            lines.append("When you encounter a login form, use the available credential injection tool.")
        return "\n\n".join(lines)

    async def _execute_tool(self, page: Any, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Dispatch a tool call to the corresponding Playwright action. Returns description."""
        if tool_name == "navigate":
            url = tool_input["url"]
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            captcha_msg = await self._auto_detect_captcha(page)
            suffix = f" {captcha_msg}" if captcha_msg else ""
            return f"Navigated to {url}{suffix}"

        elif tool_name == "click":
            selector = tool_input["selector"]
            await page.click(selector, timeout=5000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            captcha_msg = await self._auto_detect_captcha(page)
            suffix = f" {captcha_msg}" if captcha_msg else ""
            return f"Clicked {selector}{suffix}"

        elif tool_name == "type_text":
            selector = tool_input["selector"]
            text = tool_input["text"]
            await page.fill(selector, text, timeout=5000)
            return f"Typed into {selector}"

        elif tool_name == "scroll":
            direction = tool_input["direction"]
            pixels = tool_input.get("pixels", 300)
            delta = pixels if direction == "down" else -pixels
            await page.evaluate(f"window.scrollBy(0, {delta})")
            return f"Scrolled {direction} {pixels}px"

        elif tool_name == "wait":
            seconds = min(tool_input.get("seconds", 2), 10)
            await page.wait_for_timeout(int(seconds * 1000))
            return f"Waited {seconds}s"

        elif tool_name == "inject_credentials":
            injector = CredentialInjector()
            selectors: dict[str, str] | None = None
            u_sel = tool_input.get("username_selector")
            p_sel = tool_input.get("password_selector")
            if u_sel or p_sel:
                selectors = {}
                if u_sel:
                    selectors["username_selector"] = str(u_sel)
                if p_sel:
                    selectors["password_selector"] = str(p_sel)
            await injector.inject(page, self.config.credentials or {}, selectors=selectors)
            return "Credentials injected"

        elif tool_name == "solve_captcha":
            solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
            captcha_type = tool_input.get("captcha_type")
            result = await solver.solve(page, captcha_type)
            if result.solved:
                return f"Solved {result.captcha_type} captcha in {result.duration_ms}ms"
            raise RuntimeError(f"Failed to solve captcha: {result.error}")

        return f"Unknown tool: {tool_name}"

    async def _auto_detect_captcha(self, page: Any) -> str:
        """Check for CAPTCHA after navigation/click. Solve if found. Returns status message or empty."""
        try:
            solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
            captcha_type = await solver.detect_captcha(page)
            if captcha_type is None:
                return ""
            logger.info("captcha_auto_detected type=%s", captcha_type)
            result = await solver.solve(page, captcha_type)
            if result.solved:
                return f"(auto-solved {result.captcha_type} captcha in {result.duration_ms}ms)"
            return f"(captcha detected: {result.captcha_type}, solve failed: {result.error})"
        except Exception as exc:
            logger.warning("captcha_auto_detect_error error=%s", exc)
            return ""

    def _build_system_prompt(self, config: TaskConfig) -> str:
        """Build the system prompt. Credentials NEVER appear here."""
        target_domain = urlparse(config.url).netloc

        sections = [
            (
                "You are a browser automation agent. You observe screenshots of a web page "
                "and use the provided tools to complete the user's task. "
                "Choose one tool per turn."
            ),
            (
                f"SAFETY: Do not navigate to domains other than {target_domain} "
                "unless absolutely necessary. Do not download files, make purchases, "
                "or take irreversible actions."
            ),
            (
                "CREDENTIALS: When you encounter a login form, use the inject_credentials tool "
                "with the CSS selectors for the username and password fields. "
                "The system will inject credentials securely. Do NOT type credentials yourself."
            ),
            (
                "CAPTCHA: If you encounter a CAPTCHA challenge (reCAPTCHA, hCaptcha, Turnstile), "
                "use the solve_captcha tool. Auto-detects type if not specified."
            ),
        ]

        if config.output_schema:
            schema_str = json.dumps(config.output_schema, indent=2)
            sections.append(
                f'OUTPUT SCHEMA: When the task is complete, use the "done" tool and include '
                f'a "result" object matching this schema:\n{schema_str}'
            )

        sections.append(
            'When the task is fully complete, use the "done" tool. ' f"You have at most {config.max_steps} actions."
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Native executor (computer_20251124)
    # ------------------------------------------------------------------

    async def _execute_native(self, page: Any) -> NativeResult:
        """Run the native Claude computer_use agentic loop.

        Takes screenshots, sends them to Claude with the computer_20251124 tool,
        executes returned actions via Playwright, and repeats until Claude calls
        the ``done`` tool or max_steps is reached.
        """
        from workers.retry import retry_with_backoff

        # -- Screenshot scaling setup --
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        vp_width, vp_height = viewport["width"], viewport["height"]
        if vp_width > _MAX_SCREENSHOT_WIDTH:
            scale_factor = _MAX_SCREENSHOT_WIDTH / vp_width
            scaled_width = _MAX_SCREENSHOT_WIDTH
            scaled_height = int(vp_height * scale_factor)
        else:
            scale_factor = 1.0
            scaled_width = vp_width
            scaled_height = vp_height

        # -- Tool definitions --
        tools: List[Dict[str, Any]] = [
            {
                "type": "computer_20251124",
                "name": "computer",
                "display_width_px": scaled_width,
                "display_height_px": scaled_height,
                "display_number": 1,
            },
        ]
        if self.config.credentials:
            tools.append({
                "name": "inject_credentials",
                "description": (
                    "Inject stored login credentials into the current page. "
                    "Call this when you see a login form. The system will "
                    "securely fill in the credentials — do NOT type them yourself."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "The domain of the login page.",
                        },
                    },
                    "required": ["domain"],
                },
            })
        tools.append({
            "name": "solve_captcha",
            "description": (
                "Solve a CAPTCHA on the current page "
                "(reCAPTCHA v2, hCaptcha, or Cloudflare Turnstile)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "captcha_type": {
                        "type": "string",
                        "enum": ["recaptcha_v2", "hcaptcha", "turnstile"],
                        "description": "CAPTCHA type. Omit to auto-detect.",
                    },
                },
            },
        })
        tools.append({
            "name": "done",
            "description": (
                "Signal that the task is complete. Include extracted data "
                "if an output schema was specified."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "description": "Extracted data matching the output schema.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Completion summary.",
                    },
                },
            },
        })

        # -- System prompt --
        system_prompt = self._build_native_system_prompt()

        # -- Agentic loop state --
        messages: List[Dict[str, Any]] = []
        total_tokens_in = 0
        total_tokens_out = 0
        result_data: Optional[Dict[str, Any]] = None
        self._last_cursor_pos: Tuple[int, int] = (0, 0)

        for step_num in range(1, self.config.max_steps + 1):
            step_start = time.monotonic()

            # -- Check shutdown --
            if self.shutdown_check and self.shutdown_check():
                logger.warning("Shutdown detected at native step %d", step_num)
                break

            # -- Take screenshot --
            raw_screenshot = await page.screenshot(type="png")
            scaled_screenshot, _ = _scale_screenshot(raw_screenshot, _MAX_SCREENSHOT_WIDTH)
            screenshot_b64 = base64.b64encode(scaled_screenshot).decode()

            # -- Build user message with tool results or initial task --
            if not messages:
                # First turn: include task instruction
                user_content: List[Dict[str, Any]] = [
                    {"type": "text", "text": f"Complete this task: {self.config.task}"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                ]
                messages.append({"role": "user", "content": user_content})

            # -- Context window management --
            if len(messages) > _MAX_CONTEXT_MESSAGES:
                trimmed = len(messages) - _MAX_CONTEXT_MESSAGES
                messages = [messages[0]] + messages[-(_MAX_CONTEXT_MESSAGES - 1):]
                logger.debug("Trimmed %d messages from context", trimmed)
                try:
                    from workers.metrics import celery_native_context_trim_total

                    celery_native_context_trim_total.labels(
                        task_name="computeruse.execute_task",
                    ).inc()
                except Exception:
                    pass

            # -- Call Claude --
            try:
                from anthropic import APIStatusError
                _retriable = (APIStatusError, ConnectionError, TimeoutError)
            except ImportError:
                _retriable = (ConnectionError, TimeoutError)

            response = await retry_with_backoff(
                self.llm_client.beta.messages.create,
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
                betas=["computer-use-2025-11-24"],
                retriable_exceptions=_retriable,
            )

            # -- Track tokens --
            step_tokens_in = getattr(response.usage, "input_tokens", 0)
            step_tokens_out = getattr(response.usage, "output_tokens", 0)
            total_tokens_in += step_tokens_in
            total_tokens_out += step_tokens_out

            # -- Budget enforcement --
            if self._budget_monitor is not None:
                try:
                    from computeruse.budget import BudgetExceededError
                    self._budget_monitor.record_step_cost(step_tokens_in, step_tokens_out)
                except Exception as budget_exc:
                    logger.warning("Budget limit reached at native step %d: %s", step_num, budget_exc)
                    break

            # -- Append assistant response --
            messages.append({"role": "assistant", "content": response.content})

            # -- Process tool calls --
            tool_results: List[Dict[str, Any]] = []
            done = False

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                action_description = ""
                action_type = ActionType.UNKNOWN
                step_success = True
                step_error: Optional[str] = None
                step_screenshot: Optional[bytes] = None

                try:
                    if tool_name == "computer":
                        action_description, action_type = await self._execute_computer_action(
                            page, tool_input, scale_factor,
                        )
                        # Take post-action screenshot for tool result
                        post_screenshot = await page.screenshot(type="png")
                        scaled_post, _ = _scale_screenshot(post_screenshot, _MAX_SCREENSHOT_WIDTH)
                        post_b64 = base64.b64encode(scaled_post).decode()
                        step_screenshot = post_screenshot

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": post_b64,
                                },
                            }],
                        })

                    elif tool_name == "inject_credentials":
                        injector = CredentialInjector()
                        await injector.inject(page, self.config.credentials or {})
                        action_description = "Credentials injected"
                        action_type = ActionType.INJECT_CREDENTIALS
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": "Credentials injected successfully."}],
                        })

                    elif tool_name == "solve_captcha":
                        solver = CaptchaSolver(worker_settings.TWOCAPTCHA_API_KEY)
                        captcha_type = tool_input.get("captcha_type")
                        captcha_result = await solver.solve(page, captcha_type)
                        action_type = ActionType.SOLVE_CAPTCHA
                        if captcha_result.solved:
                            action_description = f"Solved {captcha_result.captcha_type} captcha"
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": [{
                                    "type": "text",
                                    "text": f"CAPTCHA solved ({captcha_result.captcha_type}).",
                                }],
                            })
                        else:
                            step_success = False
                            step_error = f"CAPTCHA solve failed: {captcha_result.error}"
                            action_description = step_error
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": [{"type": "text", "text": step_error}],
                                "is_error": True,
                            })

                    elif tool_name == "done":
                        raw_result = tool_input.get("result")
                        action_description = tool_input.get("message", "Task completed")
                        action_type = ActionType.EXTRACT

                        # Validate against output_schema if provided
                        if raw_result and self.config.output_schema:
                            try:
                                raw_result = self._output_validator.validate(
                                    raw_result, self.config.output_schema,
                                )
                            except ValidationError as val_err:
                                schema_str = self._output_validator.format_schema(
                                    self.config.output_schema,
                                )
                                logger.warning(
                                    "Output validation failed (will retry): %s",
                                    val_err.message,
                                )
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": [{
                                        "type": "text",
                                        "text": (
                                            f"Validation failed: {val_err.message}\n"
                                            f"Please call done again with a result "
                                            f"matching: {schema_str}"
                                        ),
                                    }],
                                    "is_error": True,
                                })
                                continue  # don't set done=True, let LLM retry

                        result_data = raw_result
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": "Task marked as done."}],
                        })
                        done = True

                    else:
                        action_description = f"Unknown tool: {tool_name}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                            "is_error": True,
                        })

                except Exception as exc:
                    step_success = False
                    step_error = str(exc)
                    action_description = f"Error: {exc}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "is_error": True,
                    })

                # -- Record step --
                step = StepData(
                    step_number=len(self.steps) + 1,
                    timestamp=datetime.now(timezone.utc),
                    action_type=action_type,
                    description=action_description,
                    screenshot_bytes=step_screenshot or raw_screenshot,
                    tokens_in=step_tokens_in,
                    tokens_out=step_tokens_out,
                    duration_ms=int((time.monotonic() - step_start) * 1000),
                    success=step_success,
                    error=step_error,
                )
                self.steps.append(step)

                try:
                    from workers.metrics import celery_native_step_total

                    celery_native_step_total.labels(
                        task_name="computeruse.execute_task",
                        action_type=str(action_type),
                    ).inc()
                except Exception:
                    pass

            if done:
                break

            # -- Append tool results as next user message --
            if tool_results:
                messages.append({"role": "user", "content": tool_results})

            # -- Stuck detection (periodic) --
            if step_num % _STUCK_CHECK_INTERVAL == 0:
                stuck_signal = self._stuck_detector.analyze_full_history(self.steps)
                if stuck_signal.detected:
                    logger.warning(
                        "Native executor stuck: reason=%s step=%d details=%s",
                        stuck_signal.reason,
                        stuck_signal.step_number,
                        stuck_signal.details,
                    )
                    # Inject recovery hint into the next turn
                    recovery_hint = (
                        f"You appear stuck ({stuck_signal.reason}: {stuck_signal.details}). "
                        "Try a completely different approach to accomplish the task."
                    )
                    if messages and messages[-1]["role"] == "user":
                        content = messages[-1]["content"]
                        if isinstance(content, list):
                            content.append({"type": "text", "text": recovery_hint})
                        else:
                            messages[-1]["content"] = [
                                {"type": "text", "text": str(content)},
                                {"type": "text", "text": recovery_hint},
                            ]
                    try:
                        from workers.metrics import celery_native_stuck_recovery_total

                        celery_native_stuck_recovery_total.labels(
                            task_name="computeruse.execute_task",
                        ).inc()
                    except Exception:
                        pass

            # If response has stop_reason == "end_turn" with no tool calls, we're done
            if response.stop_reason == "end_turn" and not tool_results:
                break

        # -- Calculate cost --
        cost_cents = (
            total_tokens_in * _COST_PER_M_INPUT
            + total_tokens_out * _COST_PER_M_OUTPUT
        ) / 1_000_000 * 100

        return NativeResult(
            result_data=result_data,
            cost_cents=cost_cents,
            total_tokens_in=total_tokens_in,
            total_tokens_out=total_tokens_out,
        )

    async def _execute_computer_action(
        self,
        page: Any,
        action_input: Dict[str, Any],
        scale_factor: float,
    ) -> Tuple[str, ActionType]:
        """Map a computer_20251124 tool action to a Playwright call.

        Returns (description, action_type).
        """
        action = action_input.get("action", "")

        # Coordinate remapping helper
        def _remap(coord: List[int]) -> Tuple[int, int]:
            rx = int(coord[0] / scale_factor) if scale_factor != 1.0 else coord[0]
            ry = int(coord[1] / scale_factor) if scale_factor != 1.0 else coord[1]
            self._last_cursor_pos = (rx, ry)
            return rx, ry

        if action == "key":
            key = action_input.get("text", "")
            mapped = _KEY_MAP.get(key, key)
            await page.keyboard.press(mapped)
            return f"Pressed key: {key}", ActionType.KEY_PRESS

        elif action == "type":
            text = action_input.get("text", "")
            await page.keyboard.type(text)
            return f"Typed: {text[:50]}", ActionType.TYPE

        elif action == "cursor_position":
            x, y = self._last_cursor_pos
            return f"Cursor at ({x}, {y})", ActionType.MOUSE_MOVE

        elif action == "mouse_move":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.move(rx, ry)
            return f"Moved mouse to ({rx}, {ry})", ActionType.MOUSE_MOVE

        elif action == "left_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry)
            return f"Clicked at ({rx}, {ry})", ActionType.CLICK

        elif action == "left_click_drag":
            start_coord = action_input.get("start_coordinate", [0, 0])
            end_coord = action_input.get("coordinate", [0, 0])
            sx, sy = _remap(start_coord)
            ex, ey = _remap(end_coord)
            await page.mouse.move(sx, sy)
            await page.mouse.down()
            await page.mouse.move(ex, ey)
            await page.mouse.up()
            return f"Dragged from ({sx}, {sy}) to ({ex}, {ey})", ActionType.DRAG

        elif action == "right_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry, button="right")
            return f"Right-clicked at ({rx}, {ry})", ActionType.RIGHT_CLICK

        elif action == "double_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.dblclick(rx, ry)
            return f"Double-clicked at ({rx}, {ry})", ActionType.DOUBLE_CLICK

        elif action == "triple_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry, click_count=3)
            return f"Triple-clicked at ({rx}, {ry})", ActionType.TRIPLE_CLICK

        elif action == "middle_click":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            await page.mouse.click(rx, ry, button="middle")
            return f"Middle-clicked at ({rx}, {ry})", ActionType.MIDDLE_CLICK

        elif action == "screenshot":
            # Claude requests a fresh screenshot — already handled by the loop
            return "Took screenshot", ActionType.SCREENSHOT

        elif action == "scroll":
            coord = action_input.get("coordinate", [0, 0])
            rx, ry = _remap(coord)
            direction = action_input.get("direction", "down")
            amount = action_input.get("amount", 3)
            delta_y = amount * 100 * (1 if direction == "down" else -1)
            await page.mouse.move(rx, ry)
            await page.mouse.wheel(0, delta_y)
            return f"Scrolled {direction} {amount} clicks at ({rx}, {ry})", ActionType.SCROLL

        elif action == "wait":
            seconds = min(action_input.get("duration", 2), 10)
            await asyncio.sleep(seconds)
            return f"Waited {seconds}s", ActionType.WAIT

        elif action == "hold_key":
            keys = action_input.get("key", "")
            mapped_key = _KEY_MAP.get(keys, keys)
            await page.keyboard.down(mapped_key)
            try:
                # Execute the nested action if present
                nested = action_input.get("action")
                if nested and isinstance(nested, dict):
                    await self._execute_computer_action(page, nested, scale_factor)
            finally:
                await page.keyboard.up(mapped_key)
            return f"Held key: {keys}", ActionType.KEY_PRESS

        elif action == "zoom":
            amount = action_input.get("amount", 1)
            zoom_key = "+" if amount > 0 else "-"
            for _ in range(abs(amount)):
                await page.keyboard.press(f"Control+{zoom_key}")
            return f"Zoomed {'in' if amount > 0 else 'out'} {abs(amount)}x", ActionType.ZOOM

        return f"Unknown action: {action}", ActionType.UNKNOWN

    def _build_native_system_prompt(self) -> str:
        """Build the system prompt for native computer_use mode."""
        config = self.config
        target_domain = urlparse(config.url).netloc

        sections = [
            (
                "You are a browser automation agent. You see screenshots of a web page "
                "and use the computer tool to interact with it via mouse clicks, keyboard "
                "input, and scrolling. You work with pixel coordinates from the screenshots."
            ),
            f"TARGET URL: {config.url}",
            f"TASK: {config.task}",
            (
                f"SAFETY: Stay on {target_domain} unless absolutely necessary. "
                "Do not download files, make purchases, or take irreversible actions."
            ),
        ]

        if config.credentials:
            sections.append(
                "CREDENTIALS: When you see a login form, use the inject_credentials tool. "
                "The system will fill in the credentials securely. Do NOT type passwords yourself."
            )

        sections.append(
            "CAPTCHA: If you encounter a CAPTCHA challenge, use the solve_captcha tool."
        )

        if config.output_schema:
            schema_str = json.dumps(config.output_schema, indent=2)
            sections.append(
                'OUTPUT SCHEMA: When the task is complete, call the "done" tool with a '
                f'"result" object matching this schema:\n{schema_str}'
            )

        sections.append(
            f'When the task is complete, call the "done" tool. '
            f"You have at most {config.max_steps} actions."
        )

        return "\n\n".join(sections)


def _scale_screenshot(png_bytes: bytes, max_width: int = _MAX_SCREENSHOT_WIDTH) -> Tuple[bytes, float]:
    """Scale a PNG screenshot to fit within *max_width*.

    Returns (scaled_png_bytes, scale_factor).  scale_factor is 1.0 when
    no scaling was needed.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    if width <= max_width:
        return png_bytes, 1.0

    scale = max_width / width
    new_width = max_width
    new_height = int(height * scale)
    img_resized = img.resize((new_width, new_height), Image.LANCZOS)

    buf = io.BytesIO()
    img_resized.save(buf, format="PNG")
    return buf.getvalue(), scale


def _tool_to_action_type(tool_name: str) -> ActionType:
    """Map a tool name to its corresponding ActionType."""
    mapping = {
        "navigate": ActionType.NAVIGATE,
        "click": ActionType.CLICK,
        "type_text": ActionType.TYPE,
        "scroll": ActionType.SCROLL,
        "wait": ActionType.WAIT,
        "inject_credentials": ActionType.INJECT_CREDENTIALS,
        "solve_captcha": ActionType.SOLVE_CAPTCHA,
        "done": ActionType.EXTRACT,
    }
    return mapping.get(tool_name, ActionType.UNKNOWN)
