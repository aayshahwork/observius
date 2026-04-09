"""
tests/unit/test_pav.py — Tests for workers/pav/ (Plan-Act-Validate).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.shared_types import (
    Observation,
    GroundingRung,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)
from workers.pav.types import PlanState, SubGoal
from workers.pav.planner import Planner
from workers.pav.validator import Validator


# ---------------------------------------------------------------------------
# SubGoal
# ---------------------------------------------------------------------------


class TestSubGoal:
    def test_defaults(self):
        sg = SubGoal(id="sg_1", description="Do something", success_criteria="Done")
        assert sg.status == "pending"
        assert sg.attempts == 0
        assert sg.max_attempts == 3
        assert sg.delegation_mode is False

    def test_custom_fields(self):
        sg = SubGoal(
            id="sg_2",
            description="Fill form",
            success_criteria="Form submitted",
            delegation_mode=True,
            max_attempts=5,
        )
        assert sg.delegation_mode is True
        assert sg.max_attempts == 5

    def test_mutable_status(self):
        sg = SubGoal(id="sg_1", description="x", success_criteria="y")
        sg.status = "active"
        assert sg.status == "active"
        sg.status = "done"
        assert sg.status == "done"


# ---------------------------------------------------------------------------
# PlanState
# ---------------------------------------------------------------------------


class TestPlanState:
    def _make_plan(self, n: int = 3) -> PlanState:
        subgoals = [
            SubGoal(id=f"sg_{i+1}", description=f"Step {i+1}", success_criteria=f"Done {i+1}")
            for i in range(n)
        ]
        return PlanState(task_goal="Test goal", subgoals=subgoals)

    def test_current_subgoal(self):
        plan = self._make_plan()
        assert plan.current_subgoal().id == "sg_1"

    def test_current_subgoal_none_when_exhausted(self):
        plan = self._make_plan(0)
        assert plan.current_subgoal() is None

    def test_advance(self):
        plan = self._make_plan()
        plan.advance()
        assert plan.current_index == 1
        assert plan.subgoals[0].status == "done"
        assert plan.current_subgoal().id == "sg_2"

    def test_advance_all(self):
        plan = self._make_plan(2)
        plan.advance()
        plan.advance()
        assert plan.current_index == 2
        assert plan.current_subgoal() is None

    def test_advance_past_end(self):
        plan = self._make_plan(1)
        plan.advance()
        # Advancing again when cursor is past end should be safe
        plan.advance()
        assert plan.current_index == 1

    def test_is_complete_initial(self):
        plan = self._make_plan()
        assert plan.is_complete() is False

    def test_is_complete_after_all_done(self):
        plan = self._make_plan(2)
        plan.advance()
        plan.advance()
        assert plan.is_complete() is True

    def test_is_complete_with_skipped(self):
        plan = self._make_plan(2)
        plan.subgoals[0].status = "skipped"
        plan.subgoals[1].status = "done"
        assert plan.is_complete() is True

    def test_is_complete_mixed_pending(self):
        plan = self._make_plan(2)
        plan.subgoals[0].status = "done"
        # subgoals[1] still pending
        assert plan.is_complete() is False

    def test_mark_failed(self):
        plan = self._make_plan()
        sg = plan.current_subgoal()
        plan.mark_failed(sg)
        assert sg.status == "failed"

    def test_context_default_empty(self):
        plan = self._make_plan()
        assert plan.context == {}

    def test_context_mutable(self):
        plan = self._make_plan()
        plan.context["key"] = "value"
        assert plan.context["key"] == "value"

    def test_empty_plan_is_complete(self):
        plan = PlanState(task_goal="Empty", subgoals=[])
        assert plan.is_complete() is True


# ---------------------------------------------------------------------------
# Validator — Deterministic checks
# ---------------------------------------------------------------------------


class TestValidatorDeterministic:
    def _make_validator(self):
        return Validator(llm_client=None)

    def _make_subgoal(self):
        return SubGoal(id="sg_1", description="Click button", success_criteria="Button clicked")

    def test_step_error_fails(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        result = StepResult(success=False, error="Element not found")

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.FAIL
        assert outcome.check_name == "step_error"
        assert outcome.is_critical is True

    def test_no_observation_uncertain(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        result = StepResult(success=True, observation=None)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.UNCERTAIN
        assert outcome.check_name == "no_observation"

    def test_auth_redirect_fails(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click", url_before="https://app.example.com/dashboard")
        obs = Observation(url="https://app.example.com/login")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.FAIL
        assert outcome.check_name == "auth_redirect"
        assert outcome.is_critical is True

    def test_auth_redirect_ignored_if_already_on_login(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click", url_before="https://app.example.com/login")
        obs = Observation(url="https://app.example.com/login/step2")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        # Should NOT fail because we were already on a login page
        assert outcome.verdict != ValidatorVerdict.FAIL or outcome.check_name != "auth_redirect"

    def test_auth_redirect_ignored_no_url_before(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click", url_before="")
        obs = Observation(url="https://app.example.com/login")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        # No url_before means we can't compare, should not flag auth_redirect
        assert outcome.check_name != "auth_redirect"

    def test_error_page_url_fails(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        obs = Observation(url="https://example.com/404")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.FAIL
        assert outcome.check_name == "error_page_url"

    def test_error_page_url_500(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        obs = Observation(url="https://example.com/500")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.FAIL
        assert outcome.check_name == "error_page_url"

    def test_error_page_title_warns(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        obs = Observation(url="https://example.com/page", page_title="Page Not Found")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.WARN
        assert outcome.check_name == "error_page_title"

    def test_page_errors_warns(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        obs = Observation(
            url="https://example.com/page",
            error_text="Something went wrong",
        )
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.WARN
        assert outcome.check_name == "page_errors"

    def test_page_console_errors_warns(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        obs = Observation(
            url="https://example.com/page",
            console_errors=["Uncaught TypeError: undefined"],
        )
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.WARN
        assert outcome.check_name == "page_errors"

    def test_url_unchanged_after_navigate_warns(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(
            action_type="navigate",
            url_before="https://example.com/page",
        )
        obs = Observation(url="https://example.com/page")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.WARN
        assert outcome.check_name == "url_unchanged"

    def test_url_unchanged_not_navigate_passes(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(
            action_type="click",
            url_before="https://example.com/page",
        )
        obs = Observation(url="https://example.com/page")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        # click action with unchanged URL should pass, not warn
        assert outcome.verdict == ValidatorVerdict.PASS

    def test_success_no_errors_passes(self):
        v = self._make_validator()
        sg = self._make_subgoal()
        intent = StepIntent(action_type="click")
        obs = Observation(url="https://example.com/dashboard")
        result = StepResult(success=True, observation=obs)

        outcome = v._check_deterministic(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.PASS
        assert outcome.check_name == "deterministic_pass"

    def test_various_login_patterns(self):
        """Auth redirect detection covers common login URL patterns."""
        v = self._make_validator()
        sg = self._make_subgoal()

        login_urls = [
            "https://example.com/signin",
            "https://example.com/sign-in",
            "https://example.com/auth",
            "https://example.com/sso",
            "https://example.com/oauth",
            "https://example.com/accounts/login",
        ]

        for login_url in login_urls:
            intent = StepIntent(action_type="click", url_before="https://example.com/dashboard")
            obs = Observation(url=login_url)
            result = StepResult(success=True, observation=obs)

            outcome = v._check_deterministic(sg, intent, result)
            assert outcome.verdict == ValidatorVerdict.FAIL, f"Failed for {login_url}"
            assert outcome.check_name == "auth_redirect", f"Failed for {login_url}"

    def test_various_error_urls(self):
        """Error page detection covers common error URL patterns."""
        v = self._make_validator()
        sg = self._make_subgoal()

        error_urls = [
            "https://example.com/error",
            "https://example.com/not-found",
            "https://example.com/access-denied",
            "https://example.com/unauthorized",
            "https://example.com/403",
        ]

        for error_url in error_urls:
            intent = StepIntent(action_type="click")
            obs = Observation(url=error_url)
            result = StepResult(success=True, observation=obs)

            outcome = v._check_deterministic(sg, intent, result)
            assert outcome.verdict == ValidatorVerdict.FAIL, f"Failed for {error_url}"
            assert outcome.check_name == "error_page_url", f"Failed for {error_url}"


# ---------------------------------------------------------------------------
# Validator — LLM-based validation
# ---------------------------------------------------------------------------


class TestValidatorLLM:
    def _make_llm_response(self, verdict: str, evidence: str = "", message: str = ""):
        """Create a mock LLM response."""
        payload = json.dumps({"verdict": verdict, "evidence": evidence, "message": message})
        block = MagicMock()
        block.type = "text"
        block.text = payload
        response = MagicMock()
        response.content = [block]
        return response

    def test_parse_llm_verdict_pass(self):
        v = Validator()
        outcome = v._parse_llm_verdict('{"verdict": "pass", "evidence": "ok", "message": "all good"}')
        assert outcome.verdict == ValidatorVerdict.PASS
        assert outcome.check_name == "llm_judgment"
        assert outcome.metadata["evidence"] == "ok"

    def test_parse_llm_verdict_fail(self):
        v = Validator()
        outcome = v._parse_llm_verdict('{"verdict": "fail", "evidence": "broken", "message": "nope"}')
        assert outcome.verdict == ValidatorVerdict.FAIL

    def test_parse_llm_verdict_warn(self):
        v = Validator()
        outcome = v._parse_llm_verdict('{"verdict": "warn", "evidence": "maybe", "message": "hmm"}')
        assert outcome.verdict == ValidatorVerdict.WARN

    def test_parse_llm_verdict_unknown_maps_uncertain(self):
        v = Validator()
        outcome = v._parse_llm_verdict('{"verdict": "unknown"}')
        assert outcome.verdict == ValidatorVerdict.UNCERTAIN

    def test_parse_llm_verdict_markdown_fences(self):
        v = Validator()
        raw = '```json\n{"verdict": "pass", "evidence": "ok", "message": "fine"}\n```'
        outcome = v._parse_llm_verdict(raw)
        assert outcome.verdict == ValidatorVerdict.PASS

    def test_parse_llm_verdict_invalid_json(self):
        v = Validator()
        outcome = v._parse_llm_verdict("not json at all")
        assert outcome.verdict == ValidatorVerdict.UNCERTAIN
        assert outcome.check_name == "llm_parse_error"

    def test_parse_llm_verdict_non_object(self):
        v = Validator()
        outcome = v._parse_llm_verdict("[1, 2, 3]")
        assert outcome.verdict == ValidatorVerdict.UNCERTAIN
        assert outcome.check_name == "llm_parse_error"

    async def test_validate_calls_llm_when_uncertain(self):
        """When deterministic checks are inconclusive, LLM is called."""
        mock_llm = AsyncMock()
        mock_llm.create.return_value = MagicMock(
            content=[MagicMock(type="text", text='{"verdict": "pass", "evidence": "ok", "message": "fine"}')]
        )
        v = Validator(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Do thing", success_criteria="Thing done")
        intent = StepIntent(action_type="click")
        # success=False with no error and no observation → uncertain
        result = StepResult(success=False, error=None, observation=Observation(url="https://example.com"))

        outcome = await v.validate(sg, intent, result)
        mock_llm.create.assert_called_once()

    async def test_validate_skips_llm_when_deterministic(self):
        """When deterministic checks are conclusive, LLM is not called."""
        mock_llm = AsyncMock()
        v = Validator(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Do thing", success_criteria="Thing done")
        intent = StepIntent(action_type="click")
        result = StepResult(success=False, error="Element not found")

        outcome = await v.validate(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.FAIL
        mock_llm.create.assert_not_called()

    async def test_validate_no_llm_returns_uncertain(self):
        """When no LLM and deterministic is inconclusive, returns UNCERTAIN."""
        v = Validator(llm_client=None)
        sg = SubGoal(id="sg_1", description="Do thing", success_criteria="Thing done")
        intent = StepIntent(action_type="click")
        result = StepResult(success=False, error=None, observation=Observation(url="https://example.com"))

        outcome = await v.validate(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.UNCERTAIN

    async def test_validate_llm_error_returns_uncertain(self):
        """When LLM call fails, returns UNCERTAIN."""
        mock_llm = AsyncMock()
        mock_llm.create.side_effect = RuntimeError("LLM down")
        v = Validator(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Do thing", success_criteria="Thing done")
        intent = StepIntent(action_type="click")
        result = StepResult(success=False, error=None, observation=Observation(url="https://example.com"))

        outcome = await v.validate(sg, intent, result)
        assert outcome.verdict == ValidatorVerdict.UNCERTAIN
        assert outcome.check_name == "llm_error"


# ---------------------------------------------------------------------------
# Planner — create_plan
# ---------------------------------------------------------------------------


class TestPlannerCreatePlan:
    def _make_llm_response(self, text: str):
        block = MagicMock()
        block.type = "text"
        block.text = text
        response = MagicMock()
        response.content = [block]
        return response

    async def test_create_plan_basic(self):
        subgoals_json = json.dumps([
            {"id": "sg_1", "description": "Navigate to page", "success_criteria": "URL matches", "delegation_mode": False},
            {"id": "sg_2", "description": "Extract data", "success_criteria": "Data found", "delegation_mode": True},
        ])
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(subgoals_json)

        planner = Planner(llm_client=mock_llm)
        obs = Observation(url="https://example.com", page_title="Example")
        plan = await planner.create_plan("Extract data from example.com", obs)

        assert plan.task_goal == "Extract data from example.com"
        assert len(plan.subgoals) == 2
        assert plan.subgoals[0].id == "sg_1"
        assert plan.subgoals[0].delegation_mode is False
        assert plan.subgoals[1].id == "sg_2"
        assert plan.subgoals[1].delegation_mode is True
        assert plan.current_index == 0

    async def test_create_plan_with_markdown_fences(self):
        subgoals_json = '```json\n[{"id": "sg_1", "description": "Do thing", "success_criteria": "Done"}]\n```'
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(subgoals_json)

        planner = Planner(llm_client=mock_llm)
        plan = await planner.create_plan("Test", Observation())

        assert len(plan.subgoals) == 1

    async def test_create_plan_invalid_json(self):
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response("not valid json")

        planner = Planner(llm_client=mock_llm)
        plan = await planner.create_plan("Test", Observation())

        assert len(plan.subgoals) == 0

    async def test_create_plan_not_array(self):
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response('{"id": "sg_1"}')

        planner = Planner(llm_client=mock_llm)
        plan = await planner.create_plan("Test", Observation())

        assert len(plan.subgoals) == 0

    async def test_create_plan_skips_non_dict_items(self):
        raw = json.dumps([
            {"id": "sg_1", "description": "Step 1", "success_criteria": "Done"},
            "not a dict",
            42,
            {"id": "sg_3", "description": "Step 3", "success_criteria": "Done"},
        ])
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        plan = await planner.create_plan("Test", Observation())

        assert len(plan.subgoals) == 2
        assert plan.subgoals[0].id == "sg_1"
        assert plan.subgoals[1].id == "sg_3"

    async def test_create_plan_auto_ids(self):
        """When items lack 'id', auto-generates sg_N."""
        raw = json.dumps([
            {"description": "Step 1", "success_criteria": "Done"},
            {"description": "Step 2", "success_criteria": "Done"},
        ])
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        plan = await planner.create_plan("Test", Observation())

        assert plan.subgoals[0].id == "sg_1"
        assert plan.subgoals[1].id == "sg_2"

    async def test_create_plan_includes_page_context(self):
        """LLM call includes current page URL and title."""
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response("[]")

        planner = Planner(llm_client=mock_llm)
        obs = Observation(url="https://example.com/start", page_title="Start Page")
        await planner.create_plan("Do something", obs)

        call_args = mock_llm.create.call_args
        user_msg = call_args[1]["messages"][0]["content"]
        assert "https://example.com/start" in user_msg
        assert "Start Page" in user_msg


# ---------------------------------------------------------------------------
# Planner — next_intent
# ---------------------------------------------------------------------------


class TestPlannerNextIntent:
    def _make_llm_response(self, text: str):
        block = MagicMock()
        block.type = "text"
        block.text = text
        response = MagicMock()
        response.content = [block]
        return response

    async def test_next_intent_click(self):
        raw = json.dumps({
            "action": "click",
            "target": {"strategy": "css_selector", "value": "#submit-btn"},
            "value": "",
            "description": "Click submit button",
        })
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Submit form", success_criteria="Form submitted")
        obs = Observation(url="https://example.com/form", page_title="Form")

        intent = await planner.next_intent(sg, obs)
        assert intent.action_type == "click"
        assert intent.target_selector == "#submit-btn"
        assert intent.grounding == GroundingRung.CSS_SELECTOR
        assert intent.url_before == "https://example.com/form"

    async def test_next_intent_type(self):
        raw = json.dumps({
            "action": "type",
            "target": {"strategy": "aria_label", "value": "Email input"},
            "value": "test@example.com",
            "description": "Type email",
        })
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Enter email", success_criteria="Email entered")
        obs = Observation(url="https://example.com/login")

        intent = await planner.next_intent(sg, obs)
        assert intent.action_type == "type"
        assert intent.input_value == "test@example.com"
        assert intent.grounding == GroundingRung.ARIA_LABEL

    async def test_next_intent_invalid_json(self):
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response("Click the button")

        planner = Planner(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Click", success_criteria="Clicked")
        obs = Observation()

        intent = await planner.next_intent(sg, obs)
        # Falls back to raw text as description
        assert intent.description == "Click the button"

    async def test_next_intent_unknown_strategy(self):
        raw = json.dumps({
            "action": "click",
            "target": {"strategy": "magic", "value": "foo"},
            "value": "",
            "description": "Magic click",
        })
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        sg = SubGoal(id="sg_1", description="Click", success_criteria="Clicked")
        obs = Observation()

        intent = await planner.next_intent(sg, obs)
        assert intent.grounding == GroundingRung.HEURISTIC


# ---------------------------------------------------------------------------
# Planner — replan
# ---------------------------------------------------------------------------


class TestPlannerReplan:
    def _make_llm_response(self, text: str):
        block = MagicMock()
        block.type = "text"
        block.text = text
        response = MagicMock()
        response.content = [block]
        return response

    def _make_plan(self) -> PlanState:
        return PlanState(
            task_goal="Test",
            subgoals=[
                SubGoal(id="sg_1", description="Step 1", success_criteria="Done 1", status="active", attempts=1),
                SubGoal(id="sg_2", description="Step 2", success_criteria="Done 2"),
            ],
        )

    async def test_replan_retry(self):
        raw = json.dumps({"action": "retry", "reason": "Try again"})
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        plan = self._make_plan()
        sg = plan.subgoals[0]
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.FAIL, message="Failed")

        await planner.replan(plan, sg, outcome)
        assert sg.status == "pending"  # Reset for retry
        assert len(plan.subgoals) == 2  # No new subgoals

    async def test_replan_skip(self):
        raw = json.dumps({"action": "skip", "reason": "Not achievable"})
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        plan = self._make_plan()
        sg = plan.subgoals[0]
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.FAIL, message="Failed")

        await planner.replan(plan, sg, outcome)
        assert sg.status == "skipped"
        assert plan.current_index == 1

    async def test_replan_insert(self):
        raw = json.dumps({
            "action": "insert",
            "reason": "Need to dismiss popup first",
            "new_subgoal": {
                "id": "sg_ins_0",
                "description": "Dismiss cookie popup",
                "success_criteria": "Popup gone",
                "delegation_mode": False,
            },
        })
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        plan = self._make_plan()
        sg = plan.subgoals[0]
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.FAIL, message="Blocked by popup")

        await planner.replan(plan, sg, outcome)
        assert len(plan.subgoals) == 3  # Inserted one
        assert plan.subgoals[0].id == "sg_ins_0"
        assert plan.subgoals[0].description == "Dismiss cookie popup"
        assert sg.status == "pending"  # Reset for retry after insert

    async def test_replan_invalid_json(self):
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response("not json")

        planner = Planner(llm_client=mock_llm)
        plan = self._make_plan()
        sg = plan.subgoals[0]
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.FAIL, message="Failed")

        # Should not crash
        await planner.replan(plan, sg, outcome)
        assert len(plan.subgoals) == 2  # No change

    async def test_replan_with_failure_class(self):
        """Failure class is included in the prompt when provided."""
        raw = json.dumps({"action": "retry", "reason": "Transient"})
        mock_llm = AsyncMock()
        mock_llm.create.return_value = self._make_llm_response(raw)

        planner = Planner(llm_client=mock_llm)
        plan = self._make_plan()
        sg = plan.subgoals[0]
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.FAIL, message="Timeout")

        await planner.replan(plan, sg, outcome, failure_class="browser_timeout")

        call_args = mock_llm.create.call_args
        user_msg = call_args[1]["messages"][0]["content"]
        assert "browser_timeout" in user_msg


# ---------------------------------------------------------------------------
# Planner — configuration
# ---------------------------------------------------------------------------


class TestPlannerConfig:
    def test_default_model(self):
        planner = Planner(llm_client=MagicMock())
        assert planner._model == "claude-haiku-4-5-20251001"

    def test_custom_model(self):
        planner = Planner(llm_client=MagicMock(), model="claude-sonnet-4-5")
        assert planner._model == "claude-sonnet-4-5"

    def test_custom_system_prompt(self):
        planner = Planner(llm_client=MagicMock(), system_prompt_override="Custom prompt")
        assert planner._system_prompt == "Custom prompt"

    def test_default_system_prompt(self):
        planner = Planner(llm_client=MagicMock())
        assert "browser automation planner" in planner._system_prompt


# ---------------------------------------------------------------------------
# Validator — configuration
# ---------------------------------------------------------------------------


class TestValidatorConfig:
    def test_default_no_llm(self):
        v = Validator()
        assert v.llm is None
        assert v._model == "claude-haiku-4-5-20251001"

    def test_custom_llm_and_model(self):
        mock = MagicMock()
        v = Validator(llm_client=mock, model="claude-sonnet-4-5")
        assert v.llm is mock
        assert v._model == "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Integration: PlanState + SubGoal lifecycle
# ---------------------------------------------------------------------------


class TestPlanLifecycle:
    def test_full_success_lifecycle(self):
        """Walk through a plan where all subgoals succeed."""
        plan = PlanState(
            task_goal="Login and extract data",
            subgoals=[
                SubGoal(id="sg_1", description="Navigate", success_criteria="URL matches"),
                SubGoal(id="sg_2", description="Login", success_criteria="Logged in", delegation_mode=True),
                SubGoal(id="sg_3", description="Extract", success_criteria="Data found", delegation_mode=True),
            ],
        )

        assert not plan.is_complete()
        assert plan.current_subgoal().id == "sg_1"

        plan.advance()
        assert plan.current_subgoal().id == "sg_2"
        assert plan.subgoals[0].status == "done"

        plan.advance()
        assert plan.current_subgoal().id == "sg_3"

        plan.advance()
        assert plan.current_subgoal() is None
        assert plan.is_complete()

    def test_failure_and_skip_lifecycle(self):
        """Walk through a plan where a subgoal fails and is skipped."""
        plan = PlanState(
            task_goal="Test",
            subgoals=[
                SubGoal(id="sg_1", description="Step 1", success_criteria="Done"),
                SubGoal(id="sg_2", description="Step 2", success_criteria="Done"),
                SubGoal(id="sg_3", description="Step 3", success_criteria="Done"),
            ],
        )

        plan.advance()  # sg_1 done
        plan.mark_failed(plan.subgoals[1])  # sg_2 failed
        plan.subgoals[1].status = "skipped"  # Replanner decided to skip
        plan.current_index = 2  # Move past skipped
        plan.advance()  # sg_3 done

        assert plan.is_complete()
        assert plan.subgoals[0].status == "done"
        assert plan.subgoals[1].status == "skipped"
        assert plan.subgoals[2].status == "done"

    def test_attempt_tracking(self):
        """SubGoal tracks attempt count."""
        sg = SubGoal(id="sg_1", description="Flaky step", success_criteria="Done")
        assert sg.attempts == 0

        sg.attempts += 1
        assert sg.attempts == 1
        assert sg.attempts < sg.max_attempts

        sg.attempts += 1
        sg.attempts += 1
        assert sg.attempts == sg.max_attempts


# ---------------------------------------------------------------------------
# PAV loop — repair_fn 5-arg signature and side-effect stamping
# ---------------------------------------------------------------------------


class TestPAVRepairIntegration:
    """Tests for repair_fn wiring and side-effect stamping in run_pav_loop."""

    async def test_repair_fn_receives_5_args(self):
        """repair_fn is called with (outcome, subgoal, backend, planner, validator)."""
        from unittest.mock import patch
        from workers.pav.loop import run_pav_loop
        from workers.models import TaskConfig
        from workers.shared_types import Budget

        captured_args = []

        async def mock_repair_fn(outcome, subgoal, backend, planner, validator):
            captured_args.append((outcome, subgoal, backend, planner, validator))
            return False  # fall through to replan

        config = TaskConfig(url="https://example.com", task="Test", max_steps=5)
        backend = AsyncMock()
        backend.capabilities = MagicMock(supports_single_step=False, supports_goal_delegation=True)
        # execute_goal returns one step result
        step_result = StepResult(success=False, error="Element not found")
        backend.execute_goal = AsyncMock(return_value=[step_result])
        backend.get_observation = AsyncMock(return_value=Observation(url="https://example.com"))
        backend.initialize = AsyncMock()
        backend.teardown = AsyncMock()

        planner = AsyncMock()
        plan_state = MagicMock()
        plan_state.subgoals = [
            SubGoal(id="sg_1", description="Click", success_criteria="Clicked", max_attempts=2),
        ]
        plan_state.current_index = 0
        plan_state.is_complete = MagicMock(side_effect=[False, False, True])
        plan_state.current_subgoal = MagicMock(side_effect=[plan_state.subgoals[0], plan_state.subgoals[0], None])
        plan_state.context = {}
        planner.create_plan = AsyncMock(return_value=plan_state)
        planner.replan = AsyncMock()

        validator = AsyncMock()
        validator.validate = AsyncMock(return_value=ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
            message="Element not found",
        ))

        budget = Budget(max_steps=10)

        await run_pav_loop(
            task_config=config,
            backend=backend,
            planner=planner,
            validator=validator,
            budget=budget,
            repair_fn=mock_repair_fn,
        )

        # repair_fn should have been called with 5 args
        assert len(captured_args) >= 1
        outcome, subgoal, be, pl, va = captured_args[0]
        assert isinstance(outcome, ValidatorOutcome)
        assert subgoal.id == "sg_1"
        assert be is backend
        assert pl is planner
        assert va is validator

    async def test_validator_verdict_stamped_on_step(self):
        """After validation, the last step gets a validator_verdict side effect."""
        from workers.pav.loop import step_result_to_step_data

        sr = StepResult(success=True)
        sr.side_effects.append("validator_verdict:pass")

        step_data = step_result_to_step_data(sr, 1)
        assert step_data.validator_verdict == "pass"

    async def test_failure_class_stamped_on_step(self):
        """After repair_fn, failure_class is stamped on the last step."""
        from workers.pav.loop import step_result_to_step_data

        sr = StepResult(success=False, error="timeout")
        sr.side_effects.append("validator_verdict:fail")
        sr.side_effects.append("failure_class:browser_timeout")
        sr.side_effects.append("patch_applied:wait_and_retry")

        step_data = step_result_to_step_data(sr, 1)
        assert step_data.validator_verdict == "fail"
        assert step_data.failure_class == "browser_timeout"
        assert step_data.patch_applied == "wait_and_retry"

    async def test_step_data_without_new_fields(self):
        """StepData fields are None when side effects don't contain them."""
        from workers.pav.loop import step_result_to_step_data

        sr = StepResult(success=True)
        step_data = step_result_to_step_data(sr, 1)
        assert step_data.validator_verdict is None
        assert step_data.failure_class is None
        assert step_data.patch_applied is None
