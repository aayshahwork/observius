"""
workers/pav/loop.py — Plan-Act-Validate orchestration loop.

Decomposes a task into verifiable subgoals, executes them through
a CUABackend, validates outcomes, and replans on failure.

Two execution modes per subgoal:
1. DELEGATED  — backend.execute_goal() for Browser Use / Skyvern.
2. FINE-GRAINED — backend.execute_step() per action for Native Anthropic.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional

from workers.backends.protocol import BackendCapabilities, CUABackend
from workers.models import ActionType, StepData, TaskConfig, TaskResult
from workers.pav.planner import Planner
from workers.pav.types import PlanState, SubGoal
from workers.pav.validator import Validator
from workers.shared_types import (
    Budget,
    Observation,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_pav_loop(
    task_config: TaskConfig,
    backend: CUABackend,
    planner: Planner,
    validator: Validator,
    budget: Budget,
    repair_fn: Optional[Callable[..., Awaitable[bool]]] = None,
    on_step: Optional[Callable[[StepResult], None]] = None,
) -> TaskResult:
    """Plan-Act-Validate orchestration loop.

    Args:
        task_config: The task configuration (URL, task description, etc.).
        backend: CUABackend implementation (browser_use, native, skyvern).
        planner: LLM-powered plan decomposition.
        validator: Two-phase step validation.
        budget: Step and cost budget envelope.
        repair_fn: Optional repair callback.
            Signature: ``async (outcome, subgoal, backend, planner, validator) -> bool``.
            Returns True if repaired, False to fall through to replan.
        on_step: Callback fired after each step for real-time persistence.

    Returns:
        TaskResult compatible with workers/tasks.py persistence.
    """
    task_id = str(uuid.uuid4())
    start_time = time.monotonic()
    all_step_results: List[StepResult] = []

    try:
        # -- Initialize backend --
        backend_config = _build_backend_config(task_config)
        await backend.initialize(backend_config)

        # -- Get initial observation --
        initial_obs = await backend.get_observation()

        # -- Create plan (include URL so planner generates URL-aware subgoals).
        # When an output_schema is provided, inject a JSON-return directive so
        # the delegated agent emits structured data (browser-use honors it).
        schema_directive = ""
        if getattr(task_config, "output_schema", None):
            import json as _json
            schema_directive = (
                "\n\nIMPORTANT: Return the final result as a SINGLE JSON object "
                "matching this exact schema (no markdown, no prose outside the "
                f"JSON):\n{_json.dumps(task_config.output_schema, indent=2)}"
            )

        goal_text = task_config.task + schema_directive
        goal_with_url = (
            f"Start URL: {task_config.url}\nTask: {goal_text}"
            if task_config.url
            else goal_text
        )
        plan = await planner.create_plan(goal_with_url, initial_obs)

        # Fallback: if planner returned no subgoals, create a single
        # delegation subgoal for the entire task.
        if not plan.subgoals:
            plan.subgoals.append(SubGoal(
                id="sg_1",
                description=goal_text,
                success_criteria="Task completed successfully",
                delegation_mode=True,
            ))

        logger.info(
            "PAV plan created: goal=%r subgoals=%d",
            task_config.task,
            len(plan.subgoals),
        )

        # -- Main loop --
        while not plan.is_complete() and budget.has_remaining():
            subgoal = plan.current_subgoal()
            if subgoal is None:
                break

            subgoal.status = "active"
            subgoal.attempts += 1

            # Rate-limit guard: space out API calls to avoid 429s.
            # Each iteration fires 2-3 LLM calls (planner + backend +
            # validator) in rapid succession; without a pause between
            # iterations the next burst arrives before the rate window
            # resets, triggering retries that waste 15-30s each.
            await asyncio.sleep(1.0)

            outcome = await _execute_subgoal(
                subgoal=subgoal,
                backend=backend,
                planner=planner,
                validator=validator,
                budget=budget,
                all_step_results=all_step_results,
                on_step=on_step,
            )

            # -- Stamp validator verdict on last step --
            if all_step_results:
                all_step_results[-1].side_effects.append(
                    f"validator_verdict:{outcome.verdict.value}"
                )

            # -- Handle outcome --
            if outcome.passed:
                plan.advance()
            elif outcome.failed:
                if subgoal.attempts >= subgoal.max_attempts:
                    plan.mark_failed(subgoal)
                    plan.current_index += 1  # Move past failed subgoal
                elif repair_fn is not None:
                    try:
                        repaired = await repair_fn(
                            outcome, subgoal, backend, planner, validator,
                        )
                    except Exception as exc:
                        logger.debug("repair_fn failed: %s", exc)
                        repaired = False

                    # Stamp failure_class / patch_applied from repair_fn
                    if all_step_results and outcome.failure_class:
                        all_step_results[-1].side_effects.append(
                            f"failure_class:{outcome.failure_class}"
                        )
                        if outcome.patch_applied:
                            all_step_results[-1].side_effects.append(
                                f"patch_applied:{outcome.patch_applied}"
                            )

                    if not repaired:
                        await planner.replan(plan, subgoal, outcome)
                else:
                    await planner.replan(plan, subgoal, outcome)
            else:
                # WARN or UNCERTAIN — treat as soft pass, advance
                plan.advance()

        # -- Build result --
        return _build_task_result(
            task_id=task_id,
            plan=plan,
            budget=budget,
            config=task_config,
            step_results=all_step_results,
            start_time=start_time,
        )

    except Exception as exc:
        logger.exception("PAV loop failed: %s", exc)
        return TaskResult(
            task_id=task_id,
            status="failed",
            success=False,
            error=str(exc),
            steps=len(all_step_results),
            duration_ms=int((time.monotonic() - start_time) * 1000),
            cost_cents=budget.spent_cents,
            total_tokens_in=sum(sr.tokens_in for sr in all_step_results),
            total_tokens_out=sum(sr.tokens_out for sr in all_step_results),
            step_data=_convert_step_results(all_step_results),
        )

    finally:
        try:
            await backend.teardown()
        except Exception as exc:
            logger.debug("Backend teardown failed: %s", exc)


# ---------------------------------------------------------------------------
# Subgoal execution dispatch
# ---------------------------------------------------------------------------


async def _execute_subgoal(
    subgoal: SubGoal,
    backend: CUABackend,
    planner: Planner,
    validator: Validator,
    budget: Budget,
    all_step_results: List[StepResult],
    on_step: Optional[Callable[[StepResult], None]],
) -> ValidatorOutcome:
    """Execute a single subgoal, choosing delegation or fine-grained mode."""
    caps: BackendCapabilities = getattr(
        backend, "capabilities", BackendCapabilities(),
    )

    # Use delegation when: (a) subgoal wants it and backend supports it,
    # or (b) backend doesn't support single-step at all.
    use_delegation = (
        (subgoal.delegation_mode and caps.supports_goal_delegation)
        or not caps.supports_single_step
    )

    if use_delegation:
        return await _execute_delegated(
            subgoal, backend, validator, budget, all_step_results, on_step,
        )
    else:
        return await _execute_fine_grained(
            subgoal, backend, planner, validator, budget,
            all_step_results, on_step,
        )


# ---------------------------------------------------------------------------
# Delegated execution (browser_use, skyvern)
# ---------------------------------------------------------------------------


async def _execute_delegated(
    subgoal: SubGoal,
    backend: CUABackend,
    validator: Validator,
    budget: Budget,
    all_step_results: List[StepResult],
    on_step: Optional[Callable[[StepResult], None]],
) -> ValidatorOutcome:
    """Delegate an entire subgoal to the backend's agentic loop."""
    # -- Live streaming --
    # Long-running delegations (browser-use Agent.run) are opaque: if the
    # worker hits Celery's soft_time_limit mid-run, the outer exception
    # handler sees zero steps. Wire a streaming callback so we accumulate
    # partial StepResults as the Agent emits them, and dedupe them against
    # the final return list below.
    streamed: list[int] = [0]  # boxed so inner closure can mutate

    def _stream(sr: StepResult) -> None:
        all_step_results.append(sr)
        budget.record_step(cost_cents=sr.cost_cents)
        streamed[0] += 1
        if on_step:
            try:
                on_step(sr)
            except Exception as exc:
                logger.debug("PAV streaming on_step failed: %s", exc)

    prev_stream_cb = getattr(backend, "_live_step_callback", None)
    try:
        setattr(backend, "_live_step_callback", _stream)
    except Exception:
        pass

    try:
        step_results = await backend.execute_goal(
            subgoal.description,
            max_steps=budget.remaining_steps,
        )
    except Exception as exc:
        # Streamed steps are already in all_step_results. Just append one
        # terminal error marker so the validator sees a failure.
        error_result = StepResult(success=False, error=str(exc))
        all_step_results.append(error_result)
        budget.record_step()
        if on_step:
            on_step(error_result)
        return ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="delegation_error",
            message=f"Backend delegation failed: {exc}",
            is_critical=True,
        )
    finally:
        try:
            setattr(backend, "_live_step_callback", prev_stream_cb)
        except Exception:
            pass

    # Reconcile streamed vs returned results.
    #
    # (a) Replace the streamed tail of all_step_results with the canonical
    #     step_results[:streamed_count]. This matters because browser-use
    #     only populates `history.usage` (aggregate tokens) when the full
    #     history is retrieved at end-of-run. Partial histories observed
    #     during on_step_end streaming have tokens_in=0, tokens_out=0.
    #     The canonical list has aggregate tokens attributed to the last
    #     step, so the task-level total ends up correct.
    #
    # (b) Append any remaining trailing step_results the callback didn't
    #     observe (e.g. the final `done` step emitted after the last
    #     on_step_end hook). This prevents both double-counting and
    #     under-counting.
    streamed_count = streamed[0]
    if streamed_count and step_results:
        canonical_head = step_results[:streamed_count]
        del all_step_results[-streamed_count:]
        all_step_results.extend(canonical_head)

    trailing = step_results[streamed_count:]
    for sr in trailing:
        budget.record_step(cost_cents=sr.cost_cents)
        all_step_results.append(sr)
        if on_step:
            on_step(sr)

    # Validate final state
    obs = await backend.get_observation()
    last_result = step_results[-1] if step_results else StepResult(observation=obs)

    # Ensure the last result has an observation for validation
    if last_result.observation is None:
        last_result = StepResult(
            success=last_result.success,
            error=last_result.error,
            observation=obs,
        )

    intent = StepIntent(
        action_type="delegate",
        description=subgoal.description,
    )
    return await validator.validate(subgoal, intent, last_result)


# ---------------------------------------------------------------------------
# Fine-grained execution (native anthropic)
# ---------------------------------------------------------------------------


async def _execute_fine_grained(
    subgoal: SubGoal,
    backend: CUABackend,
    planner: Planner,
    validator: Validator,
    budget: Budget,
    all_step_results: List[StepResult],
    on_step: Optional[Callable[[StepResult], None]],
) -> ValidatorOutcome:
    """Execute a subgoal one step at a time with per-step validation."""
    obs = await backend.get_observation()
    intent = await planner.next_intent(subgoal, obs)

    try:
        result = await backend.execute_step(intent)
    except Exception as exc:
        error_result = StepResult(success=False, error=str(exc))
        all_step_results.append(error_result)
        budget.record_step()
        if on_step:
            on_step(error_result)
        return ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_execution_error",
            message=f"Step execution failed: {exc}",
            is_critical=True,
        )

    budget.record_step(cost_cents=result.cost_cents)
    all_step_results.append(result)
    if on_step:
        on_step(result)

    return await validator.validate(subgoal, intent, result)


# ---------------------------------------------------------------------------
# Result building
# ---------------------------------------------------------------------------


def _build_backend_config(config: TaskConfig) -> dict:
    """Build config dict for backend.initialize() from TaskConfig."""
    return {
        "model": "claude-sonnet-4-6",
        "headless": True,
        "url": config.url,
        "task": config.task,
        "executor_mode": config.executor_mode,
        "use_vision": getattr(config, "use_vision", True),
        "output_schema": getattr(config, "output_schema", None),
    }


def _build_task_result(
    task_id: str,
    plan: PlanState,
    budget: Budget,
    config: TaskConfig,
    step_results: List[StepResult],
    start_time: float,
) -> TaskResult:
    """Convert PAV plan + results into a TaskResult for tasks.py."""
    success = plan.is_complete() and not any(
        sg.status == "failed" for sg in plan.subgoals
    )

    # Extract final result data from the last step's side effects and
    # coerce it against the task's output_schema when one is provided.
    result_data = _extract_result_data(step_results)
    if result_data is not None and getattr(config, "output_schema", None):
        result_data = _coerce_to_schema(result_data, config.output_schema)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Derive cost directly from step_results tokens via StepResult.cost_cents
    # property. budget.spent_cents can be stale when streaming attributes
    # zero tokens during partial-history conversion — we fix that for
    # all_step_results in _execute_delegated but budget isn't retroactively
    # updated. Summing the property keeps cost consistent with tokens.
    return TaskResult(
        task_id=task_id,
        status="completed" if success else "failed",
        success=success,
        result=result_data,
        error=_collect_errors(plan) if not success else None,
        steps=len(step_results),
        duration_ms=duration_ms,
        cost_cents=sum(sr.cost_cents for sr in step_results),
        total_tokens_in=sum(sr.tokens_in for sr in step_results),
        total_tokens_out=sum(sr.tokens_out for sr in step_results),
        step_data=_convert_step_results(step_results),
    )


def _extract_result_data(step_results: List[StepResult]) -> Optional[dict]:
    """Try to extract structured result data from step side effects.

    Accepts either a JSON object or a string with JSON embedded in it
    (e.g. ```json ... ``` code block). Falls back to ``{"raw": str}``.
    """
    import json
    import re

    for sr in reversed(step_results):
        for se in sr.side_effects:
            if not se.startswith("final_result:"):
                continue
            raw = se[len("final_result:"):]

            # 1) Straight JSON parse
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {"value": parsed}
            except (json.JSONDecodeError, ValueError):
                pass

            # 2) JSON embedded in a fenced code block
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except (json.JSONDecodeError, ValueError):
                    pass

            # 3) Bare JSON object anywhere in the string
            m = re.search(r"(\{[\s\S]*\})", raw)
            if m:
                try:
                    return json.loads(m.group(1))
                except (json.JSONDecodeError, ValueError):
                    pass

            # 4) Fallback — preserve the raw text
            return {"raw": raw}
    return None


def _coerce_to_schema(data: dict, schema: dict) -> dict:
    """Best-effort coercion of extracted data into ``schema`` shape.

    Uses :class:`workers.output_validator.OutputValidator` which handles
    type coercion (``list[str]``, ``int``, etc.) and raises on hard
    mismatches. On failure, returns the original data unchanged so
    users still see *something* in the dashboard.
    """
    try:
        from workers.output_validator import OutputValidator
        return OutputValidator().validate(data, schema)
    except Exception as exc:
        logger.debug("schema coercion failed: %s", exc)
        return data


def _collect_errors(plan: PlanState) -> str:
    """Collect error messages from failed subgoals."""
    errors = []
    for sg in plan.subgoals:
        if sg.status == "failed":
            errors.append(f"{sg.id}: {sg.description}")
    return "; ".join(errors) if errors else "Plan execution failed"


# ---------------------------------------------------------------------------
# StepResult → StepData conversion
# ---------------------------------------------------------------------------


def _convert_step_results(step_results: List[StepResult]) -> List[StepData]:
    """Convert shared_types StepResult list to workers.models StepData list."""
    step_data: List[StepData] = []
    for i, sr in enumerate(step_results):
        step_data.append(step_result_to_step_data(sr, i + 1))
    return step_data


def step_result_to_step_data(sr: StepResult, step_number: int) -> StepData:
    """Convert a single StepResult to StepData.

    Public so the executor's on_step callback can use it.
    """
    # Decode screenshot from base64
    screenshot_bytes = None
    if sr.observation and sr.observation.screenshot_b64:
        try:
            screenshot_bytes = base64.b64decode(sr.observation.screenshot_b64)
        except Exception:
            pass

    # Extract action type from side effects
    action_type = ActionType.UNKNOWN
    for se in sr.side_effects:
        if se.startswith("action:"):
            action_name = se[len("action:"):]
            action_type = _map_action_name(action_name)
            break

    # Extract description and new fields from side effects
    description = ""
    validator_verdict = None
    failure_class = None
    patch_applied = None
    for se in sr.side_effects:
        if se.startswith("goal:"):
            description = se[len("goal:"):]
        elif se.startswith("validator_verdict:"):
            validator_verdict = se[len("validator_verdict:"):]
        elif se.startswith("failure_class:"):
            failure_class = se[len("failure_class:"):]
        elif se.startswith("patch_applied:"):
            patch_applied = se[len("patch_applied:"):]

    return StepData(
        step_number=step_number,
        timestamp=datetime.now(timezone.utc),
        action_type=action_type,
        description=description[:500] if description else "",
        screenshot_bytes=screenshot_bytes,
        tokens_in=sr.tokens_in,
        tokens_out=sr.tokens_out,
        duration_ms=sr.duration_ms,
        success=sr.success,
        error=sr.error,
        validator_verdict=validator_verdict,
        failure_class=failure_class,
        patch_applied=patch_applied,
    )


def _map_action_name(name: str) -> ActionType:
    """Map browser-use action names to ActionType."""
    mapping = {
        "GoToUrlAction": ActionType.NAVIGATE,
        "ClickElementAction": ActionType.CLICK,
        "InputTextAction": ActionType.TYPE,
        "ScrollAction": ActionType.SCROLL,
        "ExtractPageContentAction": ActionType.EXTRACT,
        "WaitAction": ActionType.WAIT,
        "DoneAction": ActionType.EXTRACT,
        "go_to_url": ActionType.NAVIGATE,
        "click_element": ActionType.CLICK,
        "input_text": ActionType.TYPE,
        "scroll": ActionType.SCROLL,
        "extract_content": ActionType.EXTRACT,
        "wait": ActionType.WAIT,
        "done": ActionType.EXTRACT,
    }
    return mapping.get(name, ActionType.UNKNOWN)
