"""
workers/pav/planner.py — LLM-powered plan decomposition and step generation.

Planner decomposes a high-level goal into verifiable SubGoals, then
generates concrete StepIntents for each SubGoal when not in delegation mode.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from workers.shared_types import Observation, StepIntent, ValidatorOutcome
from workers.pav.types import PlanState, SubGoal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default system prompt for the planner LLM
# ---------------------------------------------------------------------------

DEFAULT_PLANNER_PROMPT = """\
You are a browser automation planner. Your job is to decompose a user's goal
into a short sequence of verifiable subgoals that a browser agent can execute.

RULES:
- Prefer FEWER subgoals (3-7 is ideal). Each subgoal should be a meaningful
  milestone, not a single click.
- Each subgoal MUST have clear success_criteria that can be checked by
  observing the browser state (URL pattern, page title, element presence, etc).
- Set delegation_mode=true for complex subgoals that require multi-step
  interaction (e.g. "fill out the form and submit"). Set it to false for
  simple single-action subgoals (e.g. "click the login button").
- Use sequential IDs: "sg_1", "sg_2", etc.

OUTPUT FORMAT — respond with ONLY a JSON array, no markdown, no explanation:
[
  {
    "id": "sg_1",
    "description": "Navigate to the target page",
    "success_criteria": "URL contains '/target'",
    "delegation_mode": false
  },
  {
    "id": "sg_2",
    "description": "Extract the pricing table data",
    "success_criteria": "Extracted data contains at least one price value",
    "delegation_mode": true
  }
]

EXAMPLE — Goal: "Log in and extract the dashboard metrics"
[
  {"id": "sg_1", "description": "Navigate to login page", "success_criteria": "URL contains '/login' or page has a password field", "delegation_mode": false},
  {"id": "sg_2", "description": "Enter credentials and submit login form", "success_criteria": "URL no longer contains '/login' and no password field visible", "delegation_mode": true},
  {"id": "sg_3", "description": "Extract dashboard metrics", "success_criteria": "Extracted data contains metric values", "delegation_mode": true}
]
"""

_NEXT_INTENT_PROMPT = """\
You are a browser automation agent deciding the SINGLE next action.

Current page:
- URL: {url}
- Title: {title}

Your subgoal: {description}

What is the single next browser action to take? Respond with ONLY JSON:
{{
  "action": "click" | "type" | "navigate" | "scroll" | "wait" | "extract",
  "target": {{
    "strategy": "css_selector" | "text_match" | "aria_label" | "xpath" | "coordinate",
    "value": "<selector or text>"
  }},
  "value": "<text to type, URL to navigate to, or empty string>",
  "description": "<what this action does>"
}}
"""

_REPLAN_PROMPT = """\
You are a browser automation planner. A subgoal failed and you need to adjust the plan.

Failed subgoal: {description}
Success criteria: {criteria}
Failure verdict: {verdict}
Failure message: {message}
{failure_class_line}
Attempts so far: {attempts}/{max_attempts}

Should we:
1. RETRY — same subgoal, maybe different approach
2. INSERT — add a precondition subgoal before retrying (e.g. dismiss a popup)
3. SKIP — this subgoal is not achievable, skip it

Respond with ONLY JSON:
{{
  "action": "retry" | "insert" | "skip",
  "reason": "<brief explanation>",
  "new_subgoal": {{
    "id": "<id>",
    "description": "<description>",
    "success_criteria": "<criteria>",
    "delegation_mode": true | false
  }}
}}

Only include "new_subgoal" if action is "insert".
"""


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class Planner:
    """LLM-powered plan decomposition and step generation.

    The LLM client is duck-typed: any object with an async
    ``create(model, max_tokens, system, messages)`` method works.
    In practice this is ``anthropic.AsyncAnthropic().messages``.
    """

    def __init__(
        self,
        llm_client: Any,
        system_prompt_override: str | None = None,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self.llm = llm_client
        self._system_prompt = system_prompt_override or DEFAULT_PLANNER_PROMPT
        self._model = model

    async def create_plan(
        self, goal: str, observation: Observation,
    ) -> PlanState:
        """Decompose *goal* into SubGoals using the LLM.

        Includes current page context (URL, title) so the LLM can tailor
        the plan to the starting state.
        """
        page_context = ""
        if observation.url:
            page_context = f"\nCurrent page: {observation.url}"
            if observation.page_title:
                page_context += f" ({observation.page_title})"

        user_msg = f"Goal: {goal}{page_context}"

        raw = await self._call_llm(
            system=self._system_prompt,
            user_message=user_msg,
            max_tokens=1024,
        )

        subgoals = self._parse_subgoals(raw)
        return PlanState(task_goal=goal, subgoals=subgoals)

    async def next_intent(
        self, subgoal: SubGoal, observation: Observation,
    ) -> StepIntent:
        """Produce the next concrete action for a non-delegation subgoal.

        Only used when ``subgoal.delegation_mode is False``.
        """
        prompt = _NEXT_INTENT_PROMPT.format(
            url=observation.url or "(unknown)",
            title=observation.page_title or "(unknown)",
            description=subgoal.description,
        )

        raw = await self._call_llm(
            system="You are a browser automation agent.",
            user_message=prompt,
            max_tokens=512,
        )

        return self._parse_intent(raw, observation)

    async def replan(
        self,
        plan: PlanState,
        subgoal: SubGoal,
        outcome: ValidatorOutcome,
        failure_class: str | None = None,
    ) -> None:
        """Modify *plan* in-place based on a failure.

        May reorder subgoals, add precondition subgoals, or skip.
        """
        failure_class_line = (
            f"Failure class: {failure_class}" if failure_class else ""
        )
        prompt = _REPLAN_PROMPT.format(
            description=subgoal.description,
            criteria=subgoal.success_criteria,
            verdict=outcome.verdict.value,
            message=outcome.message,
            failure_class_line=failure_class_line,
            attempts=subgoal.attempts,
            max_attempts=subgoal.max_attempts,
        )

        raw = await self._call_llm(
            system="You are a browser automation planner.",
            user_message=prompt,
            max_tokens=512,
        )

        self._apply_replan(plan, subgoal, raw)

    # ------------------------------------------------------------------
    # LLM call helper
    # ------------------------------------------------------------------

    async def _call_llm(
        self, *, system: str, user_message: str, max_tokens: int,
    ) -> str:
        """Call the LLM and return the text content."""
        response = await self.llm.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract text from Anthropic Messages API response
        text = ""
        for block in getattr(response, "content", []):
            if getattr(block, "type", "") == "text":
                text += getattr(block, "text", "")
        return text.strip()

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_subgoals(self, raw: str) -> list[SubGoal]:
        """Parse LLM JSON array into SubGoal objects."""
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse subgoals JSON: %s", cleaned[:200])
            return []

        if not isinstance(parsed, list):
            logger.warning("Expected JSON array for subgoals, got %s", type(parsed).__name__)
            return []

        subgoals: list[SubGoal] = []
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            subgoals.append(SubGoal(
                id=item.get("id", f"sg_{i + 1}"),
                description=item.get("description", ""),
                success_criteria=item.get("success_criteria", ""),
                delegation_mode=bool(item.get("delegation_mode", False)),
            ))
        return subgoals

    def _parse_intent(self, raw: str, observation: Observation) -> StepIntent:
        """Parse LLM JSON into a StepIntent."""
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse intent JSON: %s", cleaned[:200])
            return StepIntent(description=raw[:200])

        if not isinstance(parsed, dict):
            return StepIntent(description=raw[:200])

        # Map strategy to GroundingRung
        from workers.shared_types import GroundingRung
        target = parsed.get("target", {})
        strategy = target.get("strategy", "heuristic")
        grounding_map = {
            "css_selector": GroundingRung.CSS_SELECTOR,
            "text_match": GroundingRung.TEXT_MATCH,
            "aria_label": GroundingRung.ARIA_LABEL,
            "xpath": GroundingRung.XPATH,
            "coordinate": GroundingRung.COORDINATE,
        }
        grounding = grounding_map.get(strategy, GroundingRung.HEURISTIC)

        return StepIntent(
            action_type=parsed.get("action", ""),
            target_selector=target.get("value", ""),
            input_value=parsed.get("value", ""),
            grounding=grounding,
            description=parsed.get("description", "")[:500],
            url_before=observation.url,
        )

    def _apply_replan(
        self, plan: PlanState, subgoal: SubGoal, raw: str,
    ) -> None:
        """Apply replan LLM response to the plan state."""
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse replan JSON: %s", cleaned[:200])
            return

        if not isinstance(parsed, dict):
            return

        action = parsed.get("action", "retry")

        if action == "skip":
            subgoal.status = "skipped"
            plan.current_index += 1

        elif action == "insert":
            new_sg_data = parsed.get("new_subgoal", {})
            if new_sg_data and isinstance(new_sg_data, dict):
                new_sg = SubGoal(
                    id=new_sg_data.get("id", f"sg_ins_{plan.current_index}"),
                    description=new_sg_data.get("description", ""),
                    success_criteria=new_sg_data.get("success_criteria", ""),
                    delegation_mode=bool(new_sg_data.get("delegation_mode", False)),
                )
                # Insert before current subgoal
                plan.subgoals.insert(plan.current_index, new_sg)
                # Reset the failed subgoal's status for re-attempt
                subgoal.status = "pending"

        elif action == "retry":
            # Reset status so the loop retries it
            subgoal.status = "pending"
