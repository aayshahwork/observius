"""
workers/executor.py — Core task execution engine.

The TaskExecutor acquires a browser, navigates to a URL, runs an LLM-driven
agent loop, captures step data, generates a replay, and returns structured
results.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from workers.browser_manager import BrowserManager
from workers.models import ActionType, StepData, TaskConfig, TaskResult

logger = logging.getLogger(__name__)

# Anthropic Claude Sonnet pricing (per million tokens)
_COST_PER_M_INPUT = 3.00   # $3 / 1M input tokens
_COST_PER_M_OUTPUT = 15.00  # $15 / 1M output tokens


class AuthFormUnrecognizedError(Exception):
    """Raised when the credential injection heuristic cannot find the login form."""


class TaskExecutor:
    """Core orchestration engine for browser automation tasks.

    Drives a screenshot-based LLM agent loop that captures step data and
    generates a replay artifact.
    """

    def __init__(
        self,
        config: TaskConfig,
        browser_manager: BrowserManager,
        llm_client: Any,
        use_cloud: bool = False,
    ) -> None:
        self.config = config
        self.browser_manager = browser_manager
        self.llm_client = llm_client
        self.use_cloud = use_cloud

    async def execute(self) -> TaskResult:
        """Execute the task end-to-end.

        Step 1: Generate task_id. Record start_time.
        Step 2: Acquire browser.
        Step 3: Create page, set viewport 1280x720, apply stealth.
        Step 4: Navigate to config.url.
        Step 5: Build system prompt.
        Step 6: Agent loop.
        Step 7: Cleanup browser.
        Step 8: Return TaskResult.
        """
        # Step 1: Generate task_id
        if self.use_cloud:
            task_id = str(uuid.uuid7()) if hasattr(uuid, "uuid7") else str(uuid.uuid4())
        else:
            task_id = str(uuid.uuid4())

        start_time = time.monotonic()
        steps: List[StepData] = []
        cumulative_cost_cents = 0.0
        browser = None

        try:
            # Step 2: Acquire browser
            browser = await self.browser_manager.get_browser(self.use_cloud)

            # Step 3: Create page, viewport, stealth
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await self.browser_manager.apply_stealth(page, task_id)

            # Step 4: Navigate
            step_start = time.monotonic()
            await page.goto(
                self.config.url,
                wait_until="networkidle",
                timeout=30_000,
            )
            screenshot_bytes = await page.screenshot(
                type="jpeg", quality=85,
            )
            nav_duration = int((time.monotonic() - step_start) * 1000)

            steps.append(StepData(
                step_number=1,
                timestamp=datetime.now(timezone.utc),
                action_type=ActionType.NAVIGATE,
                description=f"Navigated to {self.config.url}",
                screenshot_bytes=screenshot_bytes,
                duration_ms=nav_duration,
                success=True,
            ))

            # Step 5: Build system prompt
            system_prompt = self._build_system_prompt(self.config)

            # Step 6: Agent loop
            conversation_history: List[Dict[str, Any]] = []
            target_domain = urlparse(self.config.url).netloc

            for step_num in range(2, self.config.max_steps + 1):
                step_start = time.monotonic()

                # a) Capture screenshot
                screenshot_bytes = await page.screenshot(
                    type="jpeg", quality=85,
                )
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode("ascii")

                # Build context from last 3 steps
                recent_context = "\n".join(
                    f"Step {s.step_number}: [{s.action_type}] {s.description}"
                    for s in steps[-3:]
                )

                # b) Send to Anthropic API
                user_content: List[Dict[str, Any]] = [
                    {
                        "type": "text",
                        "text": (
                            f"Current page screenshot is attached. "
                            f"Task: {self.config.task}\n\n"
                            f"Recent actions:\n{recent_context}\n\n"
                            f"What action should I take next? Respond with a JSON object:\n"
                            f'{{"action_type": "<navigate|click|type|scroll|extract|wait|inject_credentials|done>", '
                            f'"description": "<what you are doing>", '
                            f'"selector": "<CSS selector if applicable>", '
                            f'"value": "<value to type if applicable>", '
                            f'"url": "<URL if navigate>", '
                            f'"username_selector": "<CSS selector for username field if inject_credentials>", '
                            f'"password_selector": "<CSS selector for password field if inject_credentials>", '
                            f'"result": {{<extracted data if action_type is done>}}}}'
                        ),
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": screenshot_b64,
                        },
                    },
                ]

                llm_prompt_text = user_content[0]["text"]

                try:
                    response = self.llm_client.messages.create(
                        model="claude-sonnet-4-5-20250514",
                        max_tokens=1024,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_content}],
                    )
                except Exception as exc:
                    logger.error("LLM API call failed at step %d: %s", step_num, exc)
                    steps.append(StepData(
                        step_number=step_num,
                        timestamp=datetime.now(timezone.utc),
                        action_type=ActionType.UNKNOWN,
                        description=f"LLM API call failed: {exc}",
                        screenshot_bytes=screenshot_bytes,
                        llm_prompt=llm_prompt_text,
                        duration_ms=int((time.monotonic() - step_start) * 1000),
                        success=False,
                        error=str(exc),
                    ))
                    break

                # Extract token usage
                tokens_in = getattr(response.usage, "input_tokens", 0)
                tokens_out = getattr(response.usage, "output_tokens", 0)

                # Calculate cost
                step_cost = (
                    (tokens_in / 1_000_000) * _COST_PER_M_INPUT
                    + (tokens_out / 1_000_000) * _COST_PER_M_OUTPUT
                ) * 100  # convert to cents
                cumulative_cost_cents += step_cost

                # c) Parse LLM response
                llm_response_text = response.content[0].text
                action = self._parse_action(llm_response_text)
                action_type_str = action.get("action_type", "unknown")

                try:
                    action_type = ActionType(action_type_str)
                except ValueError:
                    action_type = ActionType.UNKNOWN

                description = action.get("description", "")[:500]

                # Check if agent signals completion
                if action_type_str == "done":
                    step_duration = int((time.monotonic() - step_start) * 1000)
                    steps.append(StepData(
                        step_number=step_num,
                        timestamp=datetime.now(timezone.utc),
                        action_type=ActionType.EXTRACT,
                        description=description or "Task completed",
                        screenshot_bytes=screenshot_bytes,
                        llm_prompt=llm_prompt_text,
                        llm_response=llm_response_text,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        duration_ms=step_duration,
                        success=True,
                    ))

                    total_duration = int((time.monotonic() - start_time) * 1000)
                    return TaskResult(
                        task_id=task_id,
                        status="completed",
                        success=True,
                        result=action.get("result"),
                        steps=len(steps),
                        duration_ms=total_duration,
                        step_data=steps,
                        cumulative_cost_cents=cumulative_cost_cents,
                    )

                # d) Execute action
                action_error: Optional[str] = None
                action_success = True

                try:
                    if action_type == ActionType.INJECT_CREDENTIALS:
                        await self._inject_credentials(
                            page,
                            action.get("username_selector"),
                            action.get("password_selector"),
                        )
                    elif action_type == ActionType.CLICK:
                        selector = action.get("selector", "")
                        if selector:
                            await page.click(selector, timeout=5000)
                    elif action_type == ActionType.TYPE:
                        selector = action.get("selector", "")
                        value = action.get("value", "")
                        if selector and value:
                            await page.fill(selector, value, timeout=5000)
                    elif action_type == ActionType.SCROLL:
                        await page.evaluate("window.scrollBy(0, 300)")
                    elif action_type == ActionType.NAVIGATE:
                        url = action.get("url", "")
                        if url:
                            await page.goto(url, wait_until="networkidle", timeout=30_000)
                    elif action_type == ActionType.WAIT:
                        await page.wait_for_timeout(2000)
                    elif action_type == ActionType.EXTRACT:
                        pass  # extraction handled by LLM in response
                except Exception as exc:
                    action_error = str(exc)
                    action_success = False
                    logger.warning("Action failed at step %d: %s", step_num, exc)

                # f) Wait for network idle (max 5s)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass  # non-fatal

                step_duration = int((time.monotonic() - step_start) * 1000)

                # g) Record StepData
                steps.append(StepData(
                    step_number=step_num,
                    timestamp=datetime.now(timezone.utc),
                    action_type=action_type,
                    description=description,
                    screenshot_bytes=screenshot_bytes,
                    llm_prompt=llm_prompt_text,
                    llm_response=llm_response_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    duration_ms=step_duration,
                    success=action_success,
                    error=action_error,
                ))

                # h) Check cost limit
                if (
                    self.config.max_cost_cents
                    and cumulative_cost_cents > self.config.max_cost_cents
                ):
                    logger.warning(
                        "Cost limit exceeded: %.2f cents > %d cents",
                        cumulative_cost_cents,
                        self.config.max_cost_cents,
                    )
                    total_duration = int((time.monotonic() - start_time) * 1000)
                    return TaskResult(
                        task_id=task_id,
                        status="failed",
                        success=False,
                        error="COST_LIMIT_EXCEEDED",
                        steps=len(steps),
                        duration_ms=total_duration,
                        step_data=steps,
                        cumulative_cost_cents=cumulative_cost_cents,
                    )

            # Max steps reached
            total_duration = int((time.monotonic() - start_time) * 1000)
            return TaskResult(
                task_id=task_id,
                status="completed",
                success=True,
                steps=len(steps),
                duration_ms=total_duration,
                step_data=steps,
                cumulative_cost_cents=cumulative_cost_cents,
            )

        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            total_duration = int((time.monotonic() - start_time) * 1000)
            return TaskResult(
                task_id=task_id,
                status="failed",
                success=False,
                error=str(exc),
                steps=len(steps),
                duration_ms=total_duration,
                step_data=steps,
                cumulative_cost_cents=cumulative_cost_cents,
            )

        finally:
            # Step 7: Close browser
            if browser is not None:
                try:
                    await self.browser_manager.release_browser(browser)
                except Exception as exc:
                    logger.warning("Error releasing browser: %s", exc)

    async def _inject_credentials(
        self,
        page: Any,
        username_selector: Optional[str] = None,
        password_selector: Optional[str] = None,
    ) -> None:
        """Inject credentials into login form fields.

        Uses provided CSS selectors or falls back to heuristic detection:
        find input[type=password], then find the nearest preceding
        input[type=text] or input[type=email].

        Credentials are NEVER sent to the LLM.
        """
        if not self.config.credentials:
            raise AuthFormUnrecognizedError("No credentials configured")

        username = self.config.credentials.get("username", "")
        password = self.config.credentials.get("password", "")

        if not username_selector or not password_selector:
            # Heuristic: find password field and nearest preceding text/email input
            password_selector = await self._find_selector(
                page, 'input[type="password"]'
            )
            if not password_selector:
                raise AuthFormUnrecognizedError(
                    "Could not find password input on page"
                )

            username_selector = await self._find_selector(
                page,
                'input[type="email"], input[type="text"][name*="user"], '
                'input[type="text"][name*="email"], input[type="text"][name*="login"], '
                'input[type="text"]',
            )
            if not username_selector:
                raise AuthFormUnrecognizedError(
                    "Could not find username/email input on page"
                )

        await page.fill(username_selector, username)
        await page.fill(password_selector, password)
        logger.info("Credentials injected via selectors")

    async def _find_selector(self, page: Any, selector: str) -> Optional[str]:
        """Return the selector if an element matching it exists, else None."""
        try:
            element = await page.query_selector(selector)
            return selector if element else None
        except Exception:
            return None

    def _build_system_prompt(self, config: TaskConfig) -> str:
        """Build the system prompt for the LLM agent.

        Includes:
        - Agent role preamble
        - Safety guardrails (domain restriction)
        - Credential injection instruction
        - Output schema instruction (if provided)
        - NO credential values
        """
        target_domain = urlparse(config.url).netloc

        sections = [
            (
                "You are a browser automation agent. You observe screenshots of a web page "
                "and decide what actions to take to complete the user's task. "
                "You respond with a single JSON object describing the next action."
            ),
            (
                f"SAFETY: Do not navigate to domains other than {target_domain} "
                f"unless absolutely necessary to complete the task. "
                f"Do not download files, make purchases, or take irreversible actions."
            ),
            (
                "CREDENTIAL INJECTION: When you see a login form, respond with "
                'action_type="inject_credentials" and provide "username_selector" and '
                '"password_selector" CSS selectors pointing to the username and password '
                "input fields. Do NOT type credentials yourself — the system will inject "
                "them securely."
            ),
        ]

        if config.output_schema:
            schema_str = json.dumps(config.output_schema, indent=2)
            sections.append(
                f"OUTPUT SCHEMA: When the task is complete, respond with "
                f'action_type="done" and include a "result" object matching '
                f"this schema:\n{schema_str}"
            )

        sections.append(
            'When the task is fully complete, respond with action_type="done". '
            "Respond with ONLY a JSON object, no additional text."
        )

        return "\n\n".join(sections)

    def _parse_action(self, llm_response: str) -> Dict[str, Any]:
        """Parse a JSON action from the LLM response text."""
        text = llm_response.strip()

        # Try to extract JSON from markdown code blocks
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    try:
                        return json.loads(part)
                    except json.JSONDecodeError:
                        continue

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse JSON from LLM response")
        return {"action_type": "unknown", "description": text[:500]}
