from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from browser_use import Agent
from browser_use.browser.browser import Browser, BrowserConfig
from langchain_anthropic import ChatAnthropic
from playwright.async_api import Page
from rich.console import Console

from computeruse.browser_manager import BrowserManager
from computeruse.config import settings
from computeruse.exceptions import TaskExecutionError, ValidationError
from computeruse.models import StepData, TaskConfig, TaskResult
from computeruse.retry import RetryHandler
from computeruse.session_manager import SessionManager
from computeruse.validator import OutputValidator

logger = logging.getLogger(__name__)
console = Console()


class TaskExecutor:
    """Core orchestration engine that drives a Browser Use agent to complete tasks.

    Ties together browser lifecycle management, session persistence, LLM-driven
    automation, structured output extraction, and replay generation into a single
    :meth:`execute` call.

    Typical usage::

        executor = TaskExecutor(model="claude-sonnet-4-5", headless=True)
        config = TaskConfig(
            url="https://example.com",
            task="Find the current price of item X",
            output_schema={"price": "float", "currency": "str"},
        )
        result = await executor.execute(config)
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        headless: bool = True,
        browserbase_api_key: Optional[str] = None,
    ) -> None:
        """
        Args:
            model:                Anthropic model ID to use for the Browser Use agent
                                  and for structured output extraction.
            headless:             Run the browser without a visible window.
            browserbase_api_key:  BrowserBase API key for cloud browser sessions.
                                  Falls back to ``settings.BROWSERBASE_API_KEY``.
        """
        self.model = model
        self.headless = headless

        self.anthropic = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.browser_manager = BrowserManager(
            headless=headless,
            browserbase_api_key=browserbase_api_key or settings.BROWSERBASE_API_KEY,
        )
        self.session_manager = SessionManager(storage_dir=settings.SESSION_DIR)
        self.retry_handler = RetryHandler(
            max_attempts=settings.DEFAULT_MAX_STEPS,
            base_delay=2.0,
        )
        self.validator = OutputValidator()
        self.steps: List[StepData] = []
        self._replay_dir = Path(settings.REPLAY_DIR)
        self._screenshot_dir = Path(settings.REPLAY_DIR) / "screenshots"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, config: TaskConfig) -> TaskResult:
        """Execute a browser automation task end-to-end.

        Orchestrates the full task lifecycle:

        1. Set up the browser (local or cloud).
        2. Restore a saved session for the target domain (if credentials are
           provided and a previous session exists).
        3. Navigate to ``config.url``.
        4. Run the Browser Use agent with a crafted prompt.
        5. Optionally extract and validate structured output.
        6. Persist the session for future runs.
        7. Generate a replay artifact.

        Args:
            config: Task configuration including URL, task description, optional
                    credentials, output schema, and execution limits.

        Returns:
            A :class:`TaskResult` with ``success=True`` on completion, or
            ``success=False`` with an ``error`` message on failure.
        """
        task_id = str(uuid.uuid4())
        start_time = time.monotonic()
        created_at = datetime.now(timezone.utc)

        self.steps = []
        self._replay_dir.mkdir(parents=True, exist_ok=True)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        console.rule(f"[bold blue]Task {task_id[:8]}…[/]")
        console.log(f"[cyan]URL:[/]  {config.url}")
        console.log(f"[cyan]Task:[/] {config.task}")

        browser: Optional[Browser] = None

        try:
            # ---------------------------------------------------------- #
            # 1. Browser setup                                             #
            # ---------------------------------------------------------- #
            browser = Browser(
                config=BrowserConfig(
                    headless=self.headless,
                    cdp_url=None,
                )
            )

            # ---------------------------------------------------------- #
            # 2. Session restore + navigation                              #
            # ---------------------------------------------------------- #
            async with await browser.new_context() as context:
                page: Page = await context.new_page()

                if config.credentials:
                    restored = await self.session_manager.load_session(
                        page, config.url
                    )
                    if restored:
                        console.log("[green]Session restored from cache[/]")

                await page.goto(config.url, wait_until="domcontentloaded")
                console.log(f"[dim]Navigated to {config.url}[/]")

                # ------------------------------------------------------ #
                # 3. Agent execution                                       #
                # ------------------------------------------------------ #
                await self.retry_handler.execute_with_timeout(
                    self._execute_with_agent,
                    config.timeout_seconds,
                    browser,
                    config,
                )

                # ------------------------------------------------------ #
                # 4. Structured output extraction + validation             #
                # ------------------------------------------------------ #
                extracted: Dict[str, Any] = {}
                if config.output_schema:
                    raw = await self._extract_output(page, config.output_schema)
                    extracted = self.validator.validate_output(
                        raw, config.output_schema
                    )
                    console.log(
                        f"[green]Output validated:[/] {list(extracted.keys())}"
                    )

                # ------------------------------------------------------ #
                # 5. Session persistence                                   #
                # ------------------------------------------------------ #
                if config.credentials:
                    await self.session_manager.save_session(page, config.url)
                    console.log("[dim]Session saved[/]")

            # ---------------------------------------------------------- #
            # 6. Replay generation                                         #
            # ---------------------------------------------------------- #
            replay_path = self._generate_replay(task_id, self.steps)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            console.log(
                f"[bold green]Task completed[/] in {duration_ms / 1000:.2f}s "
                f"({len(self.steps)} steps)"
            )

            return TaskResult(
                task_id=task_id,
                status="completed",
                success=True,
                result=extracted or None,
                replay_path=replay_path,
                steps=len(self.steps),
                duration_ms=duration_ms,
                created_at=created_at,
                completed_at=datetime.now(timezone.utc),
            )

        except ValidationError as exc:
            return self._failed_result(task_id, created_at, start_time, str(exc))

        except TaskExecutionError as exc:
            return self._failed_result(task_id, created_at, start_time, str(exc))

        except Exception as exc:
            logger.exception("Unexpected error during task %s", task_id)
            return self._failed_result(
                task_id, created_at, start_time, f"Unexpected error: {exc}"
            )

        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception as exc:
                    logger.warning("Error closing browser after task: %s", exc)

    # ------------------------------------------------------------------
    # Private: agent execution
    # ------------------------------------------------------------------

    async def _execute_with_agent(
        self, browser: Browser, config: TaskConfig
    ) -> Any:
        """Initialise and run a Browser Use :class:`Agent` for *config*.

        Builds the task prompt, wires up the step callback, and delegates
        execution to the agent.  The agent is given ``config.max_steps`` turns
        before the run is forcibly terminated.

        Args:
            browser: An already-launched :class:`Browser` instance.
            config:  The :class:`TaskConfig` driving this run.

        Returns:
            The raw result object returned by :meth:`Agent.run`.

        Raises:
            TaskExecutionError: If the agent raises an unhandled exception.
        """
        prompt = self._build_task_prompt(config)
        llm = ChatAnthropic(
            model=self.model,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            timeout=60,
            stop=None,
        )

        agent = Agent(
            task=prompt,
            llm=llm,
            browser=browser,
            max_actions_per_step=5,
        )

        # Register step callback
        agent.register_action("*", self._on_agent_step)

        try:
            result = await agent.run(max_steps=config.max_steps)
            return result
        except Exception as exc:
            raise TaskExecutionError(
                f"Browser Use agent failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Private: prompt building
    # ------------------------------------------------------------------

    def _build_task_prompt(self, config: TaskConfig) -> str:
        """Construct the full task prompt sent to the Browser Use agent.

        Combines the core task description with contextual sections for
        credentials and the expected output schema so the agent knows exactly
        what to do and what to return.

        Args:
            config: Source :class:`TaskConfig`.

        Returns:
            A multi-line string ready to be passed as the ``task`` argument
            to :class:`Agent`.
        """
        sections: list[str] = [
            f"TASK: {config.task}",
            f"STARTING URL: {config.url}",
        ]

        if config.credentials:
            cred_lines = "\n".join(
                f"  {k}: {v}" for k, v in config.credentials.items()
            )
            sections.append(f"CREDENTIALS (use exactly as provided):\n{cred_lines}")

        if config.output_schema:
            schema_str = self.validator.format_schema(config.output_schema)
            sections.append(
                "OUTPUT REQUIREMENTS:\n"
                "  When the task is complete, return the result as a JSON object "
                "with the following fields:\n"
                f"  {schema_str}\n"
                "  Ensure all fields are present and values match the specified types."
            )

        sections.append(
            "INSTRUCTIONS:\n"
            "  - Complete the task efficiently with as few steps as possible.\n"
            "  - If you encounter a CAPTCHA or bot-detection page, wait briefly "
            "and retry.\n"
            "  - If a login is required and credentials are provided above, use them.\n"
            "  - Do not navigate away from the target domain unless strictly necessary."
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Private: output extraction
    # ------------------------------------------------------------------

    async def _extract_output(
        self, page: Page, schema: Optional[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Use the LLM to extract structured data from the current page.

        Takes a snapshot of the visible page text, constructs a targeted
        extraction prompt, and asks the Anthropic model to return a JSON object
        conforming to *schema*.

        Args:
            page:   The Playwright :class:`Page` to extract data from.
            schema: Field-to-type mapping describing the expected output shape.
                    If ``None`` or empty an empty dict is returned immediately.

        Returns:
            A raw (unvalidated) dict parsed from the LLM's JSON response.

        Raises:
            TaskExecutionError: If the LLM call fails or no JSON can be parsed.
        """
        if not schema:
            return {}

        schema_str = self.validator.format_schema(schema)

        try:
            page_text = await page.evaluate(
                "() => document.body.innerText"
            )
        except Exception as exc:
            logger.warning("Could not read page text for extraction: %s", exc)
            page_text = "(page text unavailable)"

        # Truncate to avoid token overflows
        page_text = page_text[:8000]

        extraction_prompt = (
            "Extract the following structured data from the page content below.\n"
            f"Required fields: {schema_str}\n\n"
            "Return ONLY a valid JSON object with no additional commentary.\n\n"
            f"PAGE CONTENT:\n{page_text}"
        )

        try:
            message = self.anthropic.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": extraction_prompt}],
            )
            response_text: str = message.content[0].text
        except Exception as exc:
            raise TaskExecutionError(
                f"LLM extraction call failed: {exc}"
            ) from exc

        try:
            return self.validator.parse_llm_json(response_text)
        except ValueError as exc:
            raise TaskExecutionError(
                f"Could not parse JSON from extraction response: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Private: step callback
    # ------------------------------------------------------------------

    def _on_agent_step(self, step_info: Dict[str, Any]) -> None:
        """Callback invoked by the Browser Use agent after every action.

        Captures a :class:`StepData` record (including a screenshot path if
        screenshot bytes are provided) and appends it to :attr:`steps`.

        Args:
            step_info: Dict emitted by the agent.  Expected keys (all optional
                       except ``action_type``):

                       * ``action_type`` (str) — category of action taken.
                       * ``description`` (str) — human-readable summary.
                       * ``screenshot`` (bytes) — PNG screenshot data.
                       * ``dom_snapshot`` (str) — serialised DOM, if captured.
                       * ``success`` (bool) — whether the action succeeded.
                       * ``error`` (str) — error message if the action failed.
        """
        step_number = len(self.steps) + 1
        screenshot_data: Optional[bytes] = step_info.get("screenshot")
        screenshot_path = ""
        if screenshot_data:
            screenshot_path = self._save_screenshot(screenshot_data, step_number)

        step = StepData(
            step_number=step_number,
            action_type=step_info.get("action_type", "unknown"),
            description=step_info.get("description", ""),
            screenshot_path=screenshot_path,
            dom_snapshot=step_info.get("dom_snapshot"),
            success=step_info.get("success", True),
            error=step_info.get("error"),
            timestamp=datetime.now(timezone.utc),
        )

        self.steps.append(step)
        console.log(
            f"[dim]Step {step_number}:[/] [{('green' if step.success else 'red')}]"
            f"{step.action_type}[/] — {step.description[:80]}"
        )

    # ------------------------------------------------------------------
    # Private: screenshot + replay
    # ------------------------------------------------------------------

    def _save_screenshot(self, screenshot_data: bytes, step_number: int) -> str:
        """Write raw PNG *screenshot_data* to disk and return the relative path.

        Files are named ``step_<N>.png`` (zero-padded to four digits) and stored
        inside ``<replay_dir>/screenshots/``.

        Args:
            screenshot_data: Raw PNG bytes from Playwright.
            step_number:     1-based step index used in the filename.

        Returns:
            Relative path string (relative to the current working directory),
            e.g. ``"replays/screenshots/step_0001.png"``.
        """
        filename = f"step_{step_number:04d}.png"
        path = self._screenshot_dir / filename
        try:
            path.write_bytes(screenshot_data)
        except OSError as exc:
            logger.warning("Could not save screenshot %s: %s", filename, exc)
            return ""
        return str(path)

    def _generate_replay(self, task_id: str, steps: List[StepData]) -> str:
        """Serialise task steps to a JSON replay file and return its path.

        The replay data includes the task ID, total step count, and the full
        list of step records.  This JSON file acts as the source of truth for
        any downstream HTML replay renderer.

        Args:
            task_id: Unique identifier for this task run.
            steps:   Ordered list of :class:`StepData` captured during execution.

        Returns:
            Path string to the generated ``<task_id>.json`` replay file.
        """
        replay_data = {
            "task_id": task_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_steps": len(steps),
            "steps": [
                {
                    "step_number": s.step_number,
                    "action_type": s.action_type,
                    "description": s.description,
                    "screenshot_path": s.screenshot_path,
                    "success": s.success,
                    "error": s.error,
                    "timestamp": s.timestamp.isoformat(),
                }
                for s in steps
            ],
        }

        replay_path = self._replay_dir / f"{task_id}.json"
        try:
            replay_path.write_text(
                json.dumps(replay_data, indent=2), encoding="utf-8"
            )
            logger.info("Replay written to %s", replay_path)
        except OSError as exc:
            logger.warning("Could not write replay file: %s", exc)

        # ----------------------------------------------------------------
        # TODO: plug in HTML replay renderer here (Avi's code).
        # Expected interface:
        #   html_path = generate_html_replay(replay_data, output_dir=self._replay_dir)
        #   return html_path
        # ----------------------------------------------------------------

        return str(replay_path)

    # ------------------------------------------------------------------
    # Private: result helpers
    # ------------------------------------------------------------------

    def _failed_result(
        self,
        task_id: str,
        created_at: datetime,
        start_time: float,
        error: str,
    ) -> TaskResult:
        """Build a uniformly structured failed :class:`TaskResult`.

        Args:
            task_id:    The task's unique identifier.
            created_at: UTC timestamp when the task was created.
            start_time: ``time.monotonic()`` value recorded at task start,
                        used to compute ``duration_ms``.
            error:      Human-readable error description.

        Returns:
            A :class:`TaskResult` with ``success=False`` and ``status="failed"``.
        """
        duration_ms = int((time.monotonic() - start_time) * 1000)
        console.log(f"[bold red]Task failed:[/] {error}")
        return TaskResult(
            task_id=task_id,
            status="failed",
            success=False,
            error=error,
            steps=len(self.steps),
            duration_ms=duration_ms,
            created_at=created_at,
            completed_at=datetime.now(timezone.utc),
        )
