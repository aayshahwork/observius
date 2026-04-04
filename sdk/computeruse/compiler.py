"""computeruse/compiler.py — Compile enriched runs into replayable workflows.

Converts successful enriched task runs into CompiledWorkflow objects that
can be saved as JSON, replayed by ReplayExecutor, or exported as standalone
Playwright scripts.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import CompiledStep, CompiledWorkflow
from .step_enrichment import infer_intent_from_step


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CompilationError(Exception):
    """Raised when a run cannot be compiled into a workflow."""


# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

# Map recorded action_type → Playwright method name for replay.
_COMPILE_ACTION_MAP: Dict[str, str] = {
    "navigate": "goto",
    "click": "click",
    "type": "fill",
    "fill": "fill",
    "scroll": "scroll",
    "select": "select_option",
    "select_option": "select_option",
    "key_press": "press",
    "press": "press",
    "wait": "wait",
    "extract": "extract",
    "double_click": "dblclick",
    "right_click": "right_click",
    "hover": "hover",
    # Desktop actions map to their closest browser equivalents
    "desktop_click": "click",
    "desktop_type": "fill",
}

_WAIT_AFTER_MS: Dict[str, int] = {
    "goto": 2000,
    "click": 500,
    "fill": 100,
    "select_option": 200,
    "press": 200,
    "scroll": 300,
    "wait": 1000,
    "extract": 0,
    "dblclick": 500,
    "hover": 200,
    "right_click": 500,
}

_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")

# Statuses that are safe to compile from.
_COMPILABLE_STATUSES = frozenset({"completed", "success", ""})


def _detect_params(fill_template: str) -> List[str]:
    """Extract parameter names from a fill_value_template string."""
    return _PARAM_RE.findall(fill_template)


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class WorkflowCompiler:
    """Compiles enriched task runs into replayable CompiledWorkflow objects."""

    def compile_from_run(
        self,
        run_path: str,
        name: Optional[str] = None,
        parameter_names: Optional[List[str]] = None,
    ) -> CompiledWorkflow:
        """Load run JSON, validate enrichment data exists, compile.

        Raises:
            CompilationError: If the run file is invalid or the run failed.
        """
        path = Path(run_path)
        if not path.exists():
            raise CompilationError(f"Run file not found: {run_path}")

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise CompilationError(f"Failed to read run file: {exc}") from exc

        status = data.get("status", "")
        if status and status not in _COMPILABLE_STATUSES:
            raise CompilationError(
                f"Cannot compile run with status={status!r}"
            )

        steps_raw = data.get("steps", [])
        if not steps_raw:
            raise CompilationError("Run contains no steps")

        task_id = data.get("task_id", "")
        workflow_name = name or task_id or path.stem

        return self.compile_from_steps(
            steps=steps_raw,
            start_url=data.get("start_url", ""),
            source_task_id=task_id,
            parameter_names=parameter_names,
            name=workflow_name,
        )

    def compile_from_steps(
        self,
        steps: List[Any],
        start_url: str = "",
        source_task_id: str = "",
        parameter_names: Optional[List[str]] = None,
        name: str = "",
    ) -> CompiledWorkflow:
        """Compile from step dicts or StepData objects.

        Raises:
            CompilationError: If no steps provided.
        """
        if not steps:
            raise CompilationError("No steps to compile")

        compiled_steps: List[CompiledStep] = []
        all_params: Dict[str, str] = {}

        for step in steps:
            s = self._normalize_step(step)
            cs = self._compile_one_step(s, parameter_names)
            compiled_steps.append(cs)
            for p in _detect_params(cs.fill_value_template):
                all_params.setdefault(p, "")

        return CompiledWorkflow(
            name=name or source_task_id or "workflow",
            steps=compiled_steps,
            start_url=start_url,
            parameters=all_params,
            source_task_id=source_task_id,
            compiled_at=datetime.now(timezone.utc).isoformat(),
        )

    def save_workflow(
        self,
        workflow: CompiledWorkflow,
        output_dir: str = ".pokant/workflows",
    ) -> str:
        """Save workflow as JSON. Returns file path."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        # Sanitize name to prevent path traversal
        safe_name = Path(workflow.name).name or "workflow"
        path = out / f"{safe_name}.json"
        path.write_text(json.dumps(asdict(workflow), indent=2, default=str))
        return str(path)

    def generate_playwright_script(
        self,
        workflow: CompiledWorkflow,
        output_path: Optional[str] = None,
    ) -> str:
        """Generate a human-readable Playwright Python script.

        Validates syntax via ast.parse() before returning.
        """
        # Sanitize user-controlled strings to prevent injection
        safe_name = workflow.name.replace('"""', "").replace("\n", " ")
        safe_start = workflow.start_url.replace('"""', "").replace("\n", " ")

        lines: List[str] = []
        lines.append('"""Auto-generated Playwright script.')
        lines.append(f"Workflow: {safe_name}")
        if safe_start:
            lines.append(f"Start URL: {safe_start}")
        lines.append('"""')
        lines.append("")
        lines.append("import asyncio")
        lines.append("from playwright.async_api import async_playwright")
        lines.append("")

        # PARAMS dict
        param_names = sorted(workflow.parameters.keys())
        lines.append("PARAMS = {")
        for p in param_names:
            lines.append(f'    "{p}": "",  # TODO: fill in')
        lines.append("}")
        lines.append("")
        lines.append("")

        # Main function
        lines.append("async def main():")
        lines.append("    async with async_playwright() as p:")
        lines.append("        browser = await p.chromium.launch(headless=False)")
        lines.append("        page = await browser.new_page()")
        lines.append("")

        for i, step in enumerate(workflow.steps):
            # Comment with intent (sanitize newlines)
            safe_intent = step.intent.replace("\n", " ")
            lines.append(f"        # Step {i + 1}: {safe_intent}")
            line = self._step_to_playwright_line(step)
            lines.append(f"        {line}")
            if step.timeout_ms > 0:
                lines.append(
                    f"        await page.wait_for_timeout({step.timeout_ms})"
                )
            lines.append("")

        lines.append("        await browser.close()")
        lines.append("")
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append("    asyncio.run(main())")
        lines.append("")

        script = "\n".join(lines)

        try:
            ast.parse(script)
        except SyntaxError as exc:
            raise CompilationError(
                f"Generated script has syntax error: {exc}"
            ) from exc

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(script)

        return script

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _normalize_step(step: Any) -> Dict[str, Any]:
        """Convert a StepData object or dict to a plain dict."""
        if isinstance(step, dict):
            return step
        if isinstance(step, (str, int, float, bool, type(None))):
            raise CompilationError(
                f"Step must be a dict or StepData, got {type(step).__name__}"
            )
        # StepData or similar — use model_dump if Pydantic, else getattr
        if hasattr(step, "model_dump"):
            d = step.model_dump()
        else:
            d = {
                "action_type": getattr(step, "action_type", "unknown"),
                "description": getattr(step, "description", ""),
            }
        # Copy enrichment fields that live as dynamic attrs
        for attr in (
            "selectors", "intent", "intent_detail",
            "pre_url", "post_url",
            "expected_url_pattern", "expected_element", "expected_text",
            "fill_value_template",
            "element_text", "element_tag", "element_role",
        ):
            val = getattr(step, attr, None)
            if val is not None and attr not in d:
                d[attr] = val
        return d

    @staticmethod
    def _compile_one_step(
        s: Dict[str, Any],
        parameter_names: Optional[List[str]],
    ) -> CompiledStep:
        """Compile a single step dict into a CompiledStep."""
        action_type_raw = s.get("action_type", "unknown")
        action_type = _COMPILE_ACTION_MAP.get(action_type_raw, action_type_raw)
        timeout = _WAIT_AFTER_MS.get(action_type, 200)

        # Selectors — sort by confidence descending
        selectors = list(s.get("selectors") or [])
        selectors.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        # Intent — use enrichment or infer
        intent = s.get("intent", "")
        intent_detail = s.get("intent_detail", "") or s.get("description", "")

        if not intent:
            element_meta = {
                "text": s.get("element_text", ""),
                "tag": s.get("element_tag", ""),
                "role": s.get("element_role", ""),
                "placeholder": "",
                "name": "",
            }
            inferred_intent, inferred_detail = infer_intent_from_step(
                action_type_raw, element_meta
            )
            intent = inferred_intent
            if not intent_detail:
                intent_detail = inferred_detail

        # Combine intent_detail into intent if both exist
        if intent_detail and intent_detail != intent:
            intent = f"{intent} — {intent_detail}"

        # Fill value template and parameter detection
        fill_template = s.get("fill_value_template", "")
        params_detected = _detect_params(fill_template)
        if parameter_names:
            params_detected = [
                p for p in params_detected if p in parameter_names
            ]

        return CompiledStep(
            action_type=action_type,
            selectors=selectors,
            fill_value_template=fill_template,
            expected_url_pattern=s.get("expected_url_pattern", ""),
            expected_element=s.get("expected_element", ""),
            expected_text=s.get("expected_text", ""),
            intent=intent,
            timeout_ms=timeout,
            pre_url=s.get("pre_url", ""),
        )

    @staticmethod
    def _step_to_playwright_line(step: CompiledStep) -> str:
        """Convert a CompiledStep to a single Playwright call string."""
        selector = ""
        if step.selectors:
            selector = step.selectors[0].get("value", "")

        if step.action_type == "goto":
            url = step.pre_url or "https://example.com"
            return f'await page.goto("{url}")'

        if step.action_type == "fill":
            value = step.fill_value_template
            params = _detect_params(value)
            if params:
                # Generate PARAMS["key"] lookups
                for param in params:
                    value = value.replace(
                        "{{" + param + "}}",
                        '{PARAMS["' + param + '"]}',
                    )
                return f'await page.fill("{selector}", f"{value}")'
            return f'await page.fill("{selector}", "{value}")'

        if step.action_type == "select_option":
            return f'await page.select_option("{selector}", "")'

        if step.action_type == "press":
            return f'await page.press("{selector}", "Enter")'

        if step.action_type == "scroll":
            return "await page.evaluate('window.scrollBy(0, 300)')"

        if step.action_type == "wait":
            return f"await page.wait_for_timeout({step.timeout_ms})"

        if step.action_type == "extract":
            return f'await page.text_content("{selector}")'

        if step.action_type == "dblclick":
            return f'await page.dblclick("{selector}")'

        if step.action_type == "right_click":
            return f'await page.click("{selector}", button="right")'

        # Default: click
        return f'await page.click("{selector}")'
