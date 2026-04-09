"""Tests for workers.shared_types — enums, dataclass defaults, budget logic."""

import time

from workers.shared_types.actions import GroundingRung, StepIntent, StepResult
from workers.shared_types.budget import Budget
from workers.shared_types.observations import Observation
from workers.shared_types.taxonomy import FailureClass
from workers.shared_types.validation import ValidatorOutcome, ValidatorVerdict


# ---------- StrEnum value tests ----------


class TestGroundingRung:
    def test_values(self):
        assert GroundingRung.ROLE == "role"
        assert GroundingRung.LABEL == "label"
        assert GroundingRung.TEXT == "text"
        assert GroundingRung.TESTID == "testid"
        assert GroundingRung.CSS_XPATH == "css_xpath"
        assert GroundingRung.VISION == "vision"
        assert GroundingRung.COORDINATES == "coordinates"

    def test_count(self):
        assert len(GroundingRung) == 7


class TestValidatorVerdict:
    def test_values(self):
        assert ValidatorVerdict.PASS == "pass"
        assert ValidatorVerdict.FAIL_UI == "fail_ui"
        assert ValidatorVerdict.FAIL_GOAL == "fail_goal"
        assert ValidatorVerdict.FAIL_NETWORK == "fail_network"
        assert ValidatorVerdict.FAIL_POLICY == "fail_policy"
        assert ValidatorVerdict.FAIL_STUCK == "fail_stuck"
        assert ValidatorVerdict.UNCERTAIN == "uncertain"

    def test_count(self):
        assert len(ValidatorVerdict) == 7


class TestFailureClass:
    EXPECTED = {
        # UI/Interaction
        "element_not_found",
        "element_not_clickable",
        "element_obscured",
        "wrong_frame",
        "stale_element",
        "unexpected_modal",
        "navigation_loop",
        # Task/Goal
        "goal_not_met",
        "false_success",
        "incomplete_execution",
        # Network/Infra
        "anti_bot_blocked",
        "proxy_failure",
        "network_timeout",
        "captcha_challenge",
        "auth_required",
        "session_expired",
        # Policy/Safety
        "policy_violation",
        "consent_required",
        "pii_exposure_risk",
        # Meta
        "budget_exceeded",
        "stuck",
        "unknown",
    }

    def test_all_values_present(self):
        actual = {member.value for member in FailureClass}
        assert actual == self.EXPECTED

    def test_count(self):
        assert len(FailureClass) == 22


# ---------- Dataclass defaults ----------


class TestObservation:
    def test_minimal_construction(self):
        obs = Observation(url="https://example.com", title="Example")
        assert obs.url == "https://example.com"
        assert obs.title == "Example"
        assert obs.ax_tree_summary is None
        assert obs.screenshot_ref is None
        assert obs.screenshot_b64 is None
        assert obs.open_tabs == []
        assert obs.error_signals == []
        assert obs.network_signals == []
        assert obs.dom_hash is None
        assert isinstance(obs.timestamp, float)
        assert obs.raw == {}

    def test_full_construction(self):
        obs = Observation(
            url="https://example.com",
            title="Example",
            ax_tree_summary="button Submit",
            screenshot_ref="s3://bucket/shot.png",
            screenshot_b64="abc123",
            open_tabs=["tab1", "tab2"],
            error_signals=["404"],
            network_signals=[{"status": 200, "url": "/api", "method": "GET"}],
            dom_hash="deadbeef",
            timestamp=1000.0,
            raw={"extra": True},
        )
        assert obs.open_tabs == ["tab1", "tab2"]
        assert obs.network_signals[0]["status"] == 200
        assert obs.dom_hash == "deadbeef"


class TestStepIntent:
    def test_minimal(self):
        intent = StepIntent(action="click", target={"strategy": "role", "role": "button", "name": "Submit"})
        assert intent.action == "click"
        assert intent.value is None
        assert intent.guardrails == []
        assert intent.metadata == {}

    def test_with_guardrails(self):
        intent = StepIntent(
            action="click",
            target={"x": 100, "y": 200},
            guardrails=["no_purchase"],
        )
        assert intent.guardrails == ["no_purchase"]


class TestStepResult:
    def test_minimal(self):
        obs = Observation(url="https://example.com", title="Example")
        result = StepResult(success=True, observation=obs)
        assert result.success is True
        assert result.artifacts == {}
        assert result.error is None
        assert result.error_code is None
        assert result.grounding_rung_used is None
        assert result.duration_ms == 0
        assert result.raw_backend_output is None

    def test_with_error(self):
        obs = Observation(url="https://example.com", title="Error Page")
        result = StepResult(
            success=False,
            observation=obs,
            error="Element not found",
            error_code="element_not_found",
            grounding_rung_used=GroundingRung.CSS_XPATH,
            duration_ms=1500,
        )
        assert result.success is False
        assert result.grounding_rung_used == GroundingRung.CSS_XPATH


class TestValidatorOutcome:
    def test_defaults(self):
        outcome = ValidatorOutcome(verdict=ValidatorVerdict.PASS)
        assert outcome.evidence == {}
        assert outcome.failure_class is None
        assert outcome.message == ""
        assert outcome.confidence == 1.0

    def test_failure(self):
        outcome = ValidatorOutcome(
            verdict=ValidatorVerdict.FAIL_UI,
            failure_class=FailureClass.ELEMENT_NOT_FOUND,
            message="Button not in DOM",
            confidence=0.9,
        )
        assert outcome.verdict == "fail_ui"
        assert outcome.failure_class == "element_not_found"
        assert outcome.confidence == 0.9


# ---------- Budget logic ----------


class TestBudget:
    def test_defaults(self):
        b = Budget()
        assert b.max_steps == 50
        assert b.max_seconds == 300
        assert b.max_llm_calls == 100
        assert b.steps_used == 0
        assert b.llm_calls_used == 0

    def test_has_remaining_fresh(self):
        b = Budget()
        assert b.has_remaining() is True

    def test_steps_exhausted(self):
        b = Budget(max_steps=2)
        b.record_step()
        b.record_step()
        assert b.has_remaining() is False
        assert b.remaining_steps() == 0

    def test_llm_calls_exhausted(self):
        b = Budget(max_llm_calls=1)
        b.record_llm_call()
        assert b.has_remaining() is False

    def test_time_exhausted(self):
        b = Budget(max_seconds=0)
        b._start_time = time.time() - 1  # started 1 second ago
        assert b.has_remaining() is False

    def test_remaining_steps(self):
        b = Budget(max_steps=10)
        b.record_step()
        b.record_step()
        b.record_step()
        assert b.remaining_steps() == 7

    def test_elapsed_seconds(self):
        b = Budget()
        b.start()
        # elapsed should be very small but non-negative
        assert b.elapsed_seconds() >= 0
        assert b.elapsed_seconds() < 1.0

    def test_start_sets_time(self):
        b = Budget()
        assert b._start_time is None
        b.start()
        assert b._start_time is not None
        assert abs(b._start_time - time.time()) < 1.0

    def test_has_remaining_without_start(self):
        """Budget without start() should not fail on time check."""
        b = Budget(max_seconds=10)
        assert b.has_remaining() is True
