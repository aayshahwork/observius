"""computeruse/replay_executor.py — Execute compiled workflows with fallback.

Replays CompiledWorkflow objects against a live browser page with a 4-tier
fallback cascade for selector healing, AI-assisted recovery, and post-step
verification.

The caller must provide a Playwright page object.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .action_verifier import ActionVerifier
from .budget import BudgetExceededError, BudgetMonitor
from .compiler import CompilationError
from .models import CompiledStep, CompiledWorkflow
from .selector_healer import SelectorHealer


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ReplayStepError(Exception):
    """Raised when a step fails after all recovery tiers are exhausted."""

    def __init__(self, step_index: int, action_type: str, message: str) -> None:
        self.step_index = step_index
        self.action_type = action_type
        super().__init__(
            f"Step {step_index} ({action_type}) failed: {message}"
        )


# ---------------------------------------------------------------------------
# Configuration and result types
# ---------------------------------------------------------------------------


@dataclass
class ReplayConfig:
    """Configuration for replay execution."""

    headless: bool = True
    max_cost_cents: float = 50.0
    max_retries_per_step: int = 2
    fallback_model: str = "claude-haiku-4-5-20251001"
    full_fallback_model: str = ""  # Empty = skip Tier 3
    save_screenshots: bool = True
    output_dir: str = ".pokant"
    verify_actions: bool = True


@dataclass
class ReplayResult:
    """Result of executing a compiled workflow."""

    workflow_id: str
    success: bool
    steps_executed: int
    steps_total: int
    steps_deterministic: int = 0  # Tier 0
    steps_healed: int = 0  # Tier 1
    steps_ai_recovered: int = 0  # Tier 2
    steps_full_fallback: int = 0  # Tier 3
    cost_cents: float = 0.0
    duration_ms: int = 0
    error: Optional[str] = None
    failed_step: Optional[int] = None
    verification_failures: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class ReplayExecutor:
    """Executes compiled workflows with a 4-tier fallback cascade.

    Tier 0: Direct selector replay (deterministic).
    Tier 1: Selector healing via alternate selectors and text search.
    Tier 2: Single-shot AI call for selector recovery.
    Tier 3: Full AI fallback (stub — raises ReplayStepError).
    """

    def __init__(self, config: Optional[ReplayConfig] = None) -> None:
        self._config = config or ReplayConfig()
        self._healer = SelectorHealer()
        self._verifier = ActionVerifier()

    async def execute(
        self,
        workflow: CompiledWorkflow,
        params: Optional[Dict[str, str]] = None,
        page: Any = None,
    ) -> ReplayResult:
        """Execute a CompiledWorkflow against a live browser page.

        Args:
            workflow: The compiled workflow to execute.
            params: Parameter values for template substitution.
            page: A Playwright page object. Required — caller must provide.

        Raises:
            ValueError: If page is None.
        """
        if page is None:
            raise ValueError(
                "A Playwright page object is required. "
                "Auto-browser creation is not yet supported."
            )

        params = params or {}
        start = time.monotonic()

        # Fresh budget monitor per execution
        budget = BudgetMonitor(max_cost_cents=self._config.max_cost_cents)

        result = ReplayResult(
            workflow_id=workflow.source_task_id or workflow.name,
            success=False,
            steps_executed=0,
            steps_total=len(workflow.steps),
        )

        try:
            for i, step in enumerate(workflow.steps):
                tier_used, cost, vf = await self._execute_step(
                    page, step, i, params, budget
                )
                result.steps_executed += 1
                result.cost_cents += cost
                if vf:
                    result.verification_failures.append(vf)

                if tier_used == 0:
                    result.steps_deterministic += 1
                elif tier_used == 1:
                    result.steps_healed += 1
                elif tier_used == 2:
                    result.steps_ai_recovered += 1
                elif tier_used == 3:
                    result.steps_full_fallback += 1

            result.success = True

        except BudgetExceededError as exc:
            result.error = str(exc)
        except ReplayStepError as exc:
            result.error = str(exc)
            result.failed_step = exc.step_index
        except Exception as exc:
            result.error = f"Unexpected error: {exc}"

        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.cost_cents = round(result.cost_cents, 4)
        return result

    async def execute_from_file(
        self,
        workflow_path: str,
        params: Optional[Dict[str, str]] = None,
        page: Any = None,
    ) -> ReplayResult:
        """Load a workflow JSON file and execute it."""
        path = Path(workflow_path)
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            raise CompilationError(f"Workflow file not found: {workflow_path}")
        except (json.JSONDecodeError, OSError) as exc:
            raise CompilationError(
                f"Failed to read workflow file: {exc}"
            ) from exc

        workflow = _workflow_from_dict(data)
        return await self.execute(workflow, params=params, page=page)

    async def _execute_step(
        self,
        page: Any,
        step: CompiledStep,
        step_index: int,
        params: Dict[str, str],
        budget: BudgetMonitor,
    ) -> Tuple[int, float, Optional[Dict[str, Any]]]:
        """Execute a single step with the 4-tier cascade.

        Returns (tier_used, cost_cents, verification_failure_or_None).

        Raises:
            ReplayStepError: If all tiers fail.
            BudgetExceededError: If budget is exceeded.
        """
        fill_value = self._resolve_fill_value(step.fill_value_template, params)

        # For goto, the URL comes from pre_url, not from selectors
        if step.action_type == "goto":
            primary_selector = step.pre_url or ""
        elif step.selectors:
            primary_selector = self._healer._convert_selector(step.selectors[0])
        else:
            primary_selector = ""

        # -- Tier 0: Direct replay -------------------------------------------
        for attempt in range(self._config.max_retries_per_step + 1):
            try:
                await self._perform_action(
                    page, step.action_type, primary_selector, fill_value
                )
                vf = await self._post_step(page, step)
                return (0, 0.0, vf)
            except Exception:
                if attempt < self._config.max_retries_per_step:
                    await asyncio.sleep(0.5)
                    continue
                break  # Fall through to Tier 1

        # -- Tier 1: Selector healing ----------------------------------------
        healed = await self._healer.heal(page, step.selectors, failed_index=0)
        if healed is None:
            healed = await self._healer.heal_with_text_search(
                page,
                element_text=step.intent,
                element_role="",
                element_tag="",
            )
        if healed is not None:
            try:
                await self._perform_action(
                    page, step.action_type, healed, fill_value
                )
                vf = await self._post_step(page, step)
                return (1, 0.0, vf)
            except Exception:
                pass  # Fall through to Tier 2

        # -- Tier 2: AI-assisted recovery ------------------------------------
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and self._config.fallback_model:
            try:
                ai_selector, cost = await self._ai_recover(
                    page, step, api_key, budget
                )
                if ai_selector:
                    await self._perform_action(
                        page, step.action_type, ai_selector, fill_value
                    )
                    vf = await self._post_step(page, step)
                    return (2, cost, vf)
            except BudgetExceededError:
                raise
            except Exception:
                pass  # Fall through to Tier 3

        # -- Tier 3: Full AI fallback (stub) ---------------------------------
        if self._config.full_fallback_model:
            raise ReplayStepError(
                step_index,
                step.action_type,
                "Tier 3 full AI fallback not yet implemented",
            )

        raise ReplayStepError(
            step_index,
            step.action_type,
            "All recovery tiers exhausted",
        )

    async def _perform_action(
        self,
        page: Any,
        action_type: str,
        selector: str,
        fill_value: str,
    ) -> None:
        """Perform a single Playwright action on the page."""
        if action_type == "goto":
            await page.goto(selector or fill_value)
        elif action_type == "click":
            await page.click(selector)
        elif action_type == "fill":
            await page.fill(selector, fill_value)
        elif action_type == "select_option":
            await page.select_option(selector, fill_value)
        elif action_type == "press":
            await page.press(selector, fill_value or "Enter")
        elif action_type == "dblclick":
            await page.dblclick(selector)
        elif action_type == "right_click":
            await page.click(selector, button="right")
        elif action_type == "hover":
            await page.hover(selector)
        elif action_type == "scroll":
            await page.evaluate("window.scrollBy(0, 300)")
        elif action_type == "wait":
            await page.wait_for_timeout(500)
        elif action_type == "extract":
            await page.text_content(selector)
        else:
            await page.click(selector)

    async def _post_step(
        self, page: Any, step: CompiledStep
    ) -> Optional[Dict[str, Any]]:
        """Run post-step verification and wait. Returns failure dict or None."""
        failure_info = None
        if self._config.verify_actions:
            vr = await self._verifier.verify_action(
                page,
                action_type=step.action_type,
                expected_url_pattern=step.expected_url_pattern,
                expected_element=step.expected_element,
                expected_text=step.expected_text,
                pre_url=step.pre_url,
            )
            if not vr.passed:
                failure_info = {
                    "action_type": step.action_type,
                    "checks_run": vr.checks_run,
                    "checks_passed": vr.checks_passed,
                    "failures": vr.failures,
                    "warnings": vr.warnings,
                }

        if step.timeout_ms > 0:
            await asyncio.sleep(step.timeout_ms / 1000.0)

        return failure_info

    async def _ai_recover(
        self,
        page: Any,
        step: CompiledStep,
        api_key: str,
        budget: BudgetMonitor,
    ) -> Tuple[str, float]:
        """Single-shot AI call to recover a selector.

        Returns (selector, cost_cents).
        """
        # Get accessibility tree
        try:
            tree = await page.accessibility.snapshot()
            tree_str = json.dumps(tree, indent=2, default=str)[:4000]
        except Exception:
            tree_str = "(accessibility tree unavailable)"

        body = json.dumps({
            "model": self._config.fallback_model,
            "max_tokens": 256,
            "messages": [{
                "role": "user",
                "content": (
                    f"Given this accessibility tree:\n{tree_str}\n\n"
                    f"Find a CSS selector for: {step.intent}\n"
                    f"Return ONLY the CSS selector, nothing else."
                ),
            }],
        }).encode()

        headers = {
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
            "content-type": "application/json",
        }

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers=headers,
        )

        # Run blocking urllib in a thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        try:
            response_bytes = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=10).read(),
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"Anthropic API auth failed (HTTP {exc.code})"
                ) from exc
            raise

        data = json.loads(response_bytes)
        selector = data["content"][0]["text"].strip()
        tokens_in = data.get("usage", {}).get("input_tokens", 0)
        tokens_out = data.get("usage", {}).get("output_tokens", 0)

        cost = budget.record_step_cost(tokens_in, tokens_out)
        return (selector, cost)

    @staticmethod
    def _resolve_fill_value(template: str, params: Dict[str, str]) -> str:
        """Substitute {{param}} placeholders with actual values."""
        if not template:
            return ""
        result = template
        for key, value in params.items():
            result = result.replace("{{" + key + "}}", value)
        return result


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------


def _workflow_from_dict(data: Dict[str, Any]) -> CompiledWorkflow:
    """Reconstruct a CompiledWorkflow from a dict (e.g., loaded from JSON)."""
    raw_steps = data.get("steps", [])
    steps: List[CompiledStep] = []
    for i, s in enumerate(raw_steps):
        try:
            steps.append(CompiledStep(
                action_type=s["action_type"],
                selectors=s.get("selectors", []),
                fill_value_template=s.get("fill_value_template", ""),
                expected_url_pattern=s.get("expected_url_pattern", ""),
                expected_element=s.get("expected_element", ""),
                expected_text=s.get("expected_text", ""),
                intent=s.get("intent", ""),
                timeout_ms=s.get("timeout_ms", 200),
                pre_url=s.get("pre_url", ""),
            ))
        except KeyError as exc:
            raise CompilationError(
                f"Step {i} missing required field: {exc}"
            ) from exc

    return CompiledWorkflow(
        name=data.get("name", ""),
        steps=steps,
        start_url=data.get("start_url", ""),
        parameters=data.get("parameters", {}),
        source_task_id=data.get("source_task_id", ""),
        compiled_at=data.get("compiled_at", ""),
    )
