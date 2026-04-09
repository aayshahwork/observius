"""
tests/unit/test_shared_types.py — Tests for workers/shared_types/.
"""

from __future__ import annotations

import pytest

from workers.shared_types import (
    Budget,
    FailureClass,
    GroundingRung,
    Observation,
    StepIntent,
    StepResult,
    ValidatorOutcome,
    ValidatorVerdict,
)


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


class TestObservation:
    def test_defaults(self):
        obs = Observation()
        assert obs.url == ""
        assert obs.page_title == ""
        assert obs.screenshot_b64 is None
        assert obs.dom_snippet == ""
        assert obs.timestamp_ms == 0
        assert obs.viewport_width == 1280
        assert obs.viewport_height == 720
        assert obs.tab_count == 1
        assert obs.error_text == ""
        assert obs.console_errors == []

    def test_has_screenshot_false(self):
        obs = Observation()
        assert obs.has_screenshot is False

    def test_has_screenshot_true(self):
        obs = Observation(screenshot_b64="iVBOR...")
        assert obs.has_screenshot is True

    def test_has_screenshot_empty_string(self):
        obs = Observation(screenshot_b64="")
        assert obs.has_screenshot is False

    def test_has_error_false(self):
        obs = Observation()
        assert obs.has_error is False

    def test_has_error_with_error_text(self):
        obs = Observation(error_text="404 Not Found")
        assert obs.has_error is True

    def test_has_error_with_console_errors(self):
        obs = Observation(console_errors=["Uncaught TypeError"])
        assert obs.has_error is True

    def test_truncated_dom_short(self):
        obs = Observation(dom_snippet="<div>Hello</div>")
        assert obs.truncated_dom() == "<div>Hello</div>"

    def test_truncated_dom_long(self):
        obs = Observation(dom_snippet="x" * 5000)
        result = obs.truncated_dom(max_chars=100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_truncated_dom_exact_boundary(self):
        obs = Observation(dom_snippet="x" * 2000)
        assert obs.truncated_dom(max_chars=2000) == "x" * 2000

    def test_full_construction(self):
        obs = Observation(
            url="https://example.com",
            page_title="Example",
            screenshot_b64="abc123",
            dom_snippet="<html>...</html>",
            timestamp_ms=1700000000000,
            viewport_width=1920,
            viewport_height=1080,
            tab_count=3,
            error_text="",
            console_errors=[],
        )
        assert obs.url == "https://example.com"
        assert obs.page_title == "Example"
        assert obs.has_screenshot is True
        assert obs.tab_count == 3


# ---------------------------------------------------------------------------
# GroundingRung
# ---------------------------------------------------------------------------


class TestGroundingRung:
    def test_all_values(self):
        expected = {"css_selector", "aria_label", "text_match", "xpath", "coordinate", "heuristic"}
        assert {g.value for g in GroundingRung} == expected

    def test_str_enum(self):
        assert str(GroundingRung.CSS_SELECTOR) == "css_selector"
        assert GroundingRung("text_match") == GroundingRung.TEXT_MATCH

    def test_comparison(self):
        # StrEnum allows string comparison
        assert GroundingRung.CSS_SELECTOR == "css_selector"


# ---------------------------------------------------------------------------
# StepIntent
# ---------------------------------------------------------------------------


class TestStepIntent:
    def test_defaults(self):
        intent = StepIntent()
        assert intent.action_type == ""
        assert intent.target_selector == ""
        assert intent.target_text == ""
        assert intent.input_value == ""
        assert intent.grounding == GroundingRung.HEURISTIC
        assert intent.description == ""
        assert intent.expected_outcome == ""
        assert intent.url_before == ""

    def test_description_truncation(self):
        intent = StepIntent(description="x" * 600)
        assert len(intent.description) == 500

    def test_description_no_truncation(self):
        intent = StepIntent(description="Click the submit button")
        assert intent.description == "Click the submit button"

    def test_full_construction(self):
        intent = StepIntent(
            action_type="click",
            target_selector="#submit-btn",
            target_text="Submit",
            grounding=GroundingRung.CSS_SELECTOR,
            description="Click the submit button",
            expected_outcome="Form submitted successfully",
            url_before="https://example.com/form",
        )
        assert intent.action_type == "click"
        assert intent.grounding == GroundingRung.CSS_SELECTOR


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------


class TestStepResult:
    def test_defaults(self):
        result = StepResult()
        assert result.success is True
        assert result.error is None
        assert result.duration_ms == 0
        assert result.tokens_in == 0
        assert result.tokens_out == 0
        assert result.observation is None
        assert result.verification_passed is None
        assert result.side_effects == []

    def test_cost_cents(self):
        result = StepResult(tokens_in=1000, tokens_out=500)
        # (1000 * 3.0 + 500 * 15.0) / 1_000_000 * 100
        expected = (3000 + 7500) / 1_000_000 * 100
        assert abs(result.cost_cents - expected) < 0.0001

    def test_cost_cents_zero(self):
        result = StepResult()
        assert result.cost_cents == 0.0

    def test_has_observation(self):
        result = StepResult()
        assert result.has_observation is False
        result.observation = Observation(url="https://example.com")
        assert result.has_observation is True

    def test_with_error(self):
        result = StepResult(success=False, error="Element not found")
        assert result.success is False
        assert result.error == "Element not found"


# ---------------------------------------------------------------------------
# ValidatorVerdict
# ---------------------------------------------------------------------------


class TestValidatorVerdict:
    def test_all_values(self):
        expected = {"pass", "fail", "warn", "skip", "uncertain"}
        assert {v.value for v in ValidatorVerdict} == expected

    def test_str_enum(self):
        assert ValidatorVerdict("pass") == ValidatorVerdict.PASS
        assert str(ValidatorVerdict.FAIL) == "fail"


# ---------------------------------------------------------------------------
# ValidatorOutcome
# ---------------------------------------------------------------------------


class TestValidatorOutcome:
    def test_defaults(self):
        outcome = ValidatorOutcome()
        assert outcome.verdict == ValidatorVerdict.SKIP
        assert outcome.check_name == ""
        assert outcome.expected == ""
        assert outcome.actual == ""
        assert outcome.message == ""
        assert outcome.is_critical is False
        assert outcome.metadata == {}

    def test_passed_for_pass(self):
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.PASS)
        assert outcome.passed is True
        assert outcome.failed is False

    def test_passed_for_skip(self):
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.SKIP)
        assert outcome.passed is True
        assert outcome.failed is False

    def test_failed_for_fail(self):
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.FAIL)
        assert outcome.passed is False
        assert outcome.failed is True

    def test_warn_not_failed(self):
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.WARN)
        assert outcome.passed is False  # warn is not passed
        assert outcome.failed is False  # warn is not failed either

    def test_to_dict(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="url_pattern",
            expected=".*dashboard.*",
            actual="https://example.com/login",
            message="URL did not match expected pattern",
            is_critical=True,
            metadata={"extra": "info"},
        )
        d = outcome.to_dict()
        assert d["verdict"] == "fail"
        assert d["check_name"] == "url_pattern"
        assert d["expected"] == ".*dashboard.*"
        assert d["actual"] == "https://example.com/login"
        assert d["is_critical"] is True
        # metadata not in to_dict (intentional — only serializes core fields)
        assert "metadata" not in d

    def test_full_construction(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.PASS,
            check_name="element_presence",
            expected="#submit-btn",
            actual="found",
            message="Element found on page",
            is_critical=False,
            metadata={"selector_type": "id"},
        )
        assert outcome.passed is True
        assert outcome.metadata["selector_type"] == "id"


# ---------------------------------------------------------------------------
# FailureClass
# ---------------------------------------------------------------------------


class TestFailureClass:
    def test_exactly_22_values(self):
        assert len(FailureClass) == 22

    def test_all_values(self):
        expected = {
            "llm_overloaded", "llm_rate_limited", "llm_auth_failed",
            "llm_bad_request", "llm_context_overflow",
            "browser_crash", "browser_timeout", "browser_navigation",
            "browser_element_missing", "browser_element_blocked", "browser_click_intercepted",
            "network_timeout", "network_dns", "network_connection",
            "anti_bot_captcha", "anti_bot_rate_limited", "anti_bot_blocked",
            "auth_required", "auth_session_expired",
            "agent_loop", "agent_exhausted_steps",
            "unknown",
        }
        assert {fc.value for fc in FailureClass} == expected

    def test_retriable_transients(self):
        assert FailureClass.LLM_OVERLOADED.is_retriable is True
        assert FailureClass.LLM_RATE_LIMITED.is_retriable is True
        assert FailureClass.BROWSER_TIMEOUT.is_retriable is True
        assert FailureClass.NETWORK_TIMEOUT.is_retriable is True
        assert FailureClass.AGENT_LOOP.is_retriable is True

    def test_non_retriable_permanents(self):
        assert FailureClass.LLM_AUTH_FAILED.is_retriable is False
        assert FailureClass.LLM_BAD_REQUEST.is_retriable is False
        assert FailureClass.BROWSER_CRASH.is_retriable is False
        assert FailureClass.ANTI_BOT_CAPTCHA.is_retriable is False
        assert FailureClass.AUTH_REQUIRED.is_retriable is False

    def test_unknown_is_retriable(self):
        # Default to retriable so caller can decide based on retry count
        assert FailureClass.UNKNOWN.is_retriable is True

    def test_group_llm(self):
        assert FailureClass.LLM_OVERLOADED.group == "llm"
        assert FailureClass.LLM_CONTEXT_OVERFLOW.group == "llm"

    def test_group_browser(self):
        assert FailureClass.BROWSER_CRASH.group == "browser"
        assert FailureClass.BROWSER_CLICK_INTERCEPTED.group == "browser"

    def test_group_network(self):
        assert FailureClass.NETWORK_DNS.group == "network"

    def test_group_anti_bot(self):
        assert FailureClass.ANTI_BOT_CAPTCHA.group == "anti_bot"
        assert FailureClass.ANTI_BOT_BLOCKED.group == "anti_bot"

    def test_group_auth(self):
        assert FailureClass.AUTH_REQUIRED.group == "auth"

    def test_group_agent(self):
        assert FailureClass.AGENT_LOOP.group == "agent"

    def test_group_unknown(self):
        assert FailureClass.UNKNOWN.group == "unknown"

    def test_str_enum_construction(self):
        assert FailureClass("browser_crash") == FailureClass.BROWSER_CRASH
        assert str(FailureClass.AGENT_LOOP) == "agent_loop"


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class TestBudget:
    def test_defaults(self):
        b = Budget()
        assert b.max_cost_cents == 0.0
        assert b.spent_cents == 0.0
        assert b.max_steps == 50
        assert b.steps_used == 0

    def test_has_remaining_initial(self):
        b = Budget(max_cost_cents=100.0, max_steps=50)
        assert b.has_remaining() is True

    def test_has_remaining_no_cost_limit(self):
        b = Budget(max_cost_cents=0, max_steps=50, steps_used=10)
        assert b.has_remaining() is True

    def test_has_remaining_steps_exhausted(self):
        b = Budget(max_steps=50, steps_used=50)
        assert b.has_remaining() is False

    def test_has_remaining_cost_exhausted(self):
        b = Budget(max_cost_cents=100.0, spent_cents=100.0)
        assert b.has_remaining() is False

    def test_has_remaining_cost_exceeded(self):
        b = Budget(max_cost_cents=100.0, spent_cents=150.0)
        assert b.has_remaining() is False

    def test_remaining_steps(self):
        b = Budget(max_steps=50, steps_used=30)
        assert b.remaining_steps == 20

    def test_remaining_steps_zero(self):
        b = Budget(max_steps=50, steps_used=60)
        assert b.remaining_steps == 0

    def test_remaining_cost_cents(self):
        b = Budget(max_cost_cents=100.0, spent_cents=40.0)
        assert b.remaining_cost_cents == 60.0

    def test_remaining_cost_no_limit(self):
        b = Budget(max_cost_cents=0)
        assert b.remaining_cost_cents == float("inf")

    def test_remaining_cost_exceeded(self):
        b = Budget(max_cost_cents=100.0, spent_cents=150.0)
        assert b.remaining_cost_cents == 0.0

    def test_cost_utilization(self):
        b = Budget(max_cost_cents=100.0, spent_cents=75.0)
        assert b.cost_utilization == 0.75

    def test_cost_utilization_no_limit(self):
        b = Budget(max_cost_cents=0)
        assert b.cost_utilization == 0.0

    def test_step_utilization(self):
        b = Budget(max_steps=50, steps_used=25)
        assert b.step_utilization == 0.5

    def test_step_utilization_zero_max(self):
        b = Budget(max_steps=0)
        assert b.step_utilization == 0.0

    def test_record_step(self):
        b = Budget(max_cost_cents=100.0, max_steps=50)
        b.record_step(cost_cents=2.5)
        assert b.steps_used == 1
        assert b.spent_cents == 2.5

    def test_record_step_multiple(self):
        b = Budget(max_cost_cents=100.0, max_steps=50)
        for _ in range(10):
            b.record_step(cost_cents=1.0)
        assert b.steps_used == 10
        assert abs(b.spent_cents - 10.0) < 0.001
        assert b.has_remaining() is True

    def test_record_step_until_exhausted(self):
        b = Budget(max_cost_cents=5.0, max_steps=100)
        for _ in range(5):
            b.record_step(cost_cents=1.0)
        assert b.has_remaining() is False  # 5.0 >= 5.0

    def test_record_step_no_cost(self):
        b = Budget(max_steps=3)
        b.record_step()
        b.record_step()
        assert b.steps_used == 2
        assert b.spent_cents == 0.0
        assert b.has_remaining() is True
        b.record_step()
        assert b.has_remaining() is False


# ---------------------------------------------------------------------------
# Cross-module integration
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_step_result_with_observation(self):
        """StepResult can hold an Observation."""
        obs = Observation(url="https://example.com/result", page_title="Results")
        result = StepResult(
            success=True,
            duration_ms=1200,
            tokens_in=500,
            tokens_out=100,
            observation=obs,
        )
        assert result.has_observation is True
        assert result.observation.url == "https://example.com/result"

    def test_intent_result_pair(self):
        """StepIntent and StepResult pair naturally."""
        intent = StepIntent(
            action_type="click",
            target_selector="#btn",
            grounding=GroundingRung.CSS_SELECTOR,
        )
        result = StepResult(success=True, duration_ms=150)
        # In real usage these are stored as a pair
        assert intent.action_type == "click"
        assert result.success is True

    def test_failure_class_in_validator_outcome(self):
        """ValidatorOutcome metadata can reference FailureClass."""
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="url_pattern",
            metadata={"failure_class": FailureClass.BROWSER_NAVIGATION.value},
        )
        assert outcome.metadata["failure_class"] == "browser_navigation"

    def test_budget_tracks_step_results(self):
        """Budget can track costs from StepResult."""
        budget = Budget(max_cost_cents=10.0, max_steps=5)
        for _ in range(3):
            result = StepResult(tokens_in=1000, tokens_out=200)
            budget.record_step(cost_cents=result.cost_cents)
        assert budget.steps_used == 3
        assert budget.spent_cents > 0
        assert budget.has_remaining() is True


# ---------------------------------------------------------------------------
# Budget.max_seconds
# ---------------------------------------------------------------------------


class TestBudgetMaxSeconds:
    def test_default_zero(self):
        b = Budget()
        assert b.max_seconds == 0.0

    def test_custom_max_seconds(self):
        b = Budget(max_seconds=300.0)
        assert b.max_seconds == 300.0

    def test_has_remaining_unaffected(self):
        """max_seconds doesn't change has_remaining() behavior."""
        b = Budget(max_steps=50, max_seconds=120.0)
        assert b.has_remaining() is True


# ---------------------------------------------------------------------------
# ValidatorOutcome — failure_class and patch_applied
# ---------------------------------------------------------------------------


class TestValidatorOutcomeRepairFields:
    def test_defaults_none(self):
        outcome = ValidatorOutcome()
        assert outcome.failure_class is None
        assert outcome.patch_applied is None

    def test_settable(self):
        outcome = ValidatorOutcome()
        outcome.failure_class = "browser_timeout"
        outcome.patch_applied = "wait_and_retry"
        assert outcome.failure_class == "browser_timeout"
        assert outcome.patch_applied == "wait_and_retry"

    def test_to_dict_includes_when_set(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL,
            check_name="step_error",
        )
        outcome.failure_class = "browser_timeout"
        outcome.patch_applied = "wait_and_retry"
        d = outcome.to_dict()
        assert d["failure_class"] == "browser_timeout"
        assert d["patch_applied"] == "wait_and_retry"

    def test_to_dict_excludes_when_none(self):
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.PASS)
        d = outcome.to_dict()
        assert "failure_class" not in d
        assert "patch_applied" not in d
