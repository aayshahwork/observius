"""Tests for computeruse.failure_analyzer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

from computeruse.failure_analyzer import (
    FailureAnalyzer,
    FailureCategory,
    FailureDiagnosis,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class MockStep:
    """Minimal step object for testing (mirrors StepData fields used by analyzer)."""

    step_number: int = 1
    action_type: str = "click"
    description: str = ""
    success: bool = True
    error: Optional[str] = None
    intent: str = ""


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tier 1: Rule-based analysis
# ---------------------------------------------------------------------------


class TestElementInteraction:
    def test_element_not_found_diagnosis(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Click the submit button",
            steps=[MockStep(
                action_type="click",
                description="click(#submit)",
                success=False,
                error="Element #submit not found",
            )],
            error="Element #submit not found",
        ))
        assert diagnosis.category == FailureCategory.ELEMENT_INTERACTION
        assert diagnosis.is_retryable is True
        assert "selector" in diagnosis.retry_hint.lower()
        assert diagnosis.confidence >= 0.7
        assert diagnosis.analysis_method == "rule_based"
        assert diagnosis.analysis_cost_cents == 0.0

    def test_overlay_blocking(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Accept terms",
            steps=[MockStep(success=False, error="cookie consent overlay blocking click")],
            error="cookie consent overlay blocking click",
        ))
        assert diagnosis.category == FailureCategory.ELEMENT_INTERACTION
        assert diagnosis.subcategory == "overlay_blocking"
        assert "dismiss" in diagnosis.retry_hint.lower()
        assert diagnosis.confidence >= 0.7

    def test_click_intercepted(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Submit form",
            steps=[MockStep(success=False, error="click intercepted by another element")],
            error="click intercepted by another element",
        ))
        assert diagnosis.category == FailureCategory.ELEMENT_INTERACTION
        assert diagnosis.subcategory == "click_intercepted"


class TestAntiBot:
    def test_captcha_not_retryable(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Login to site",
            steps=[MockStep(success=False, error="CAPTCHA challenge detected")],
            error="CAPTCHA challenge detected",
        ))
        assert diagnosis.category == FailureCategory.ANTI_BOT
        assert diagnosis.subcategory == "captcha"
        assert diagnosis.is_retryable is False
        assert diagnosis.confidence >= 0.9

    def test_rate_limit_has_wait(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Scrape data",
            steps=[MockStep(success=False, error="429 Too Many Requests")],
            error="429 Too Many Requests",
        ))
        assert diagnosis.category == FailureCategory.ANTI_BOT
        assert diagnosis.wait_seconds > 0
        assert diagnosis.is_retryable is True


class TestAuthentication:
    def test_session_expired(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Download report",
            steps=[MockStep(success=False, error="session expired, please log in again")],
            error="session expired, please log in again",
        ))
        assert diagnosis.category == FailureCategory.AUTHENTICATION
        assert diagnosis.subcategory == "session_expired"
        assert "fresh_browser" in diagnosis.environment_changes
        assert diagnosis.should_change_approach is True


class TestNavigation:
    def test_timeout(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Load page",
            steps=[MockStep(success=False, error="Navigation timeout after 30000ms")],
            error="Navigation timeout after 30000ms",
        ))
        assert diagnosis.category == FailureCategory.NAVIGATION
        assert diagnosis.wait_seconds > 0

    def test_network_error(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Open site",
            steps=[MockStep(success=False, error="net::ERR_CONNECTION_REFUSED")],
            error="net::ERR_CONNECTION_REFUSED",
        ))
        assert diagnosis.category == FailureCategory.NAVIGATION
        assert diagnosis.subcategory == "network_error"


class TestInfrastructure:
    def test_browser_crash(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Run task",
            steps=[MockStep(success=False, error="browser process crashed unexpectedly")],
            error="browser process crashed unexpectedly",
        ))
        assert diagnosis.category == FailureCategory.INFRASTRUCTURE
        assert "fresh_browser" in diagnosis.environment_changes


# ---------------------------------------------------------------------------
# Step-history heuristics
# ---------------------------------------------------------------------------


class TestStepHistory:
    def test_agent_loop_from_step_history(self):
        """5 identical steps should trigger AGENT_LOOP category."""
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [
            MockStep(step_number=i, action_type="click", description="click(#btn)", success=True)
            for i in range(1, 6)
        ]
        diagnosis = _run(analyzer.analyze(
            task_description="Submit form",
            steps=steps,
            error="Task did not complete",
        ))
        assert diagnosis.category == FailureCategory.AGENT_LOOP
        assert diagnosis.should_change_approach is True
        assert diagnosis.confidence >= 0.7

    def test_initial_navigation_failure(self):
        """First step navigate failure should be detected."""
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [MockStep(step_number=1, action_type="navigate", success=False, error="DNS failed")]
        diagnosis = _run(analyzer.analyze(
            task_description="Go to site",
            steps=steps,
            error="DNS failed",
        ))
        assert diagnosis.category == FailureCategory.NAVIGATION
        assert diagnosis.subcategory == "initial_navigation_failed"


# ---------------------------------------------------------------------------
# Fallback and LLM-disabled paths
# ---------------------------------------------------------------------------


class TestFallback:
    def test_unknown_falls_through(self):
        """Unrecognized error with LLM disabled should return UNKNOWN with low confidence."""
        analyzer = FailureAnalyzer(enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Do something",
            steps=[MockStep(success=False, error="xyzzy foobar glitch")],
            error="xyzzy foobar glitch",
        ))
        assert diagnosis.category == FailureCategory.UNKNOWN
        assert diagnosis.confidence < 0.7
        assert diagnosis.is_retryable is True  # unknown defaults to retryable
        assert diagnosis.analysis_method == "rule_based"

    def test_llm_disabled_returns_rule_result(self):
        """When enable_llm=False, should never call Haiku even if confidence < 0.7."""
        analyzer = FailureAnalyzer(api_key="sk-test", enable_llm=False)
        diagnosis = _run(analyzer.analyze(
            task_description="Do something",
            steps=[],
            error="mysterious failure xyz",
        ))
        assert diagnosis.analysis_method == "rule_based"
        assert diagnosis.analysis_cost_cents == 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_format_steps_compact(self):
        """Verify step formatting includes key fields and stays concise."""
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [
            MockStep(step_number=1, action_type="navigate", description="go to https://example.com", success=True),
            MockStep(step_number=2, action_type="click", description="click(#login-btn)", success=False, error="Element not found"),
        ]
        formatted = analyzer._format_steps_for_prompt(steps)
        assert "Step 1" in formatted
        assert "Step 2" in formatted
        assert "navigate" in formatted
        assert "click" in formatted
        assert "Element not found" in formatted
        # Check it's reasonably compact (< 500 chars for 2 steps)
        assert len(formatted) < 500

    def test_summarize_progress_with_successes(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [
            MockStep(step_number=1, description="navigated to page", success=True),
            MockStep(step_number=2, description="clicked login", success=True),
            MockStep(step_number=3, description="filled form", success=False, error="timeout"),
        ]
        summary = analyzer._summarize_progress(steps)
        assert "2/3" in summary
        assert "clicked login" in summary

    def test_summarize_progress_no_successes(self):
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [MockStep(success=False, error="failed")]
        summary = analyzer._summarize_progress(steps)
        assert "No actions completed" in summary

    def test_to_dict_serialization(self):
        diagnosis = FailureDiagnosis(
            category=FailureCategory.NAVIGATION,
            subcategory="timeout",
            confidence=0.8,
            analysis_method="rule_based",
        )
        d = diagnosis.to_dict()
        assert d["category"] == "navigation"
        assert d["subcategory"] == "timeout"
        assert isinstance(d["environment_changes"], list)


# ---------------------------------------------------------------------------
# Agent reasoning heuristic
# ---------------------------------------------------------------------------


class TestAgentReasoning:
    def test_exhausted_steps_with_max_steps(self):
        """When >80% of max_steps used without completing, detect agent_reasoning."""
        analyzer = FailureAnalyzer(enable_llm=False)
        # 42 successful steps out of max_steps=50 (84% > 80%)
        steps = [
            MockStep(step_number=i, action_type="click", description=f"action {i}", success=True)
            for i in range(1, 43)
        ]
        diagnosis = _run(analyzer.analyze(
            task_description="Complex multi-step task",
            steps=steps,
            error="Task did not complete in time",
            max_steps=50,
        ))
        assert diagnosis.category == FailureCategory.AGENT_REASONING
        assert diagnosis.subcategory == "exhausted_steps"
        assert diagnosis.should_change_approach is True
        assert diagnosis.confidence >= 0.7

    def test_no_agent_reasoning_below_threshold(self):
        """When steps < 80% of max_steps, should NOT fire agent_reasoning."""
        analyzer = FailureAnalyzer(enable_llm=False)
        # 10 steps out of max_steps=50 (20% < 80%)
        steps = [
            MockStep(step_number=i, action_type="click", description=f"action {i}", success=True)
            for i in range(1, 11)
        ]
        diagnosis = _run(analyzer.analyze(
            task_description="Simple task",
            steps=steps,
            error="unknown failure",
            max_steps=50,
        ))
        assert diagnosis.category != FailureCategory.AGENT_REASONING

    def test_no_agent_reasoning_without_max_steps_short_run(self):
        """Without max_steps, 15 steps should NOT trigger agent_reasoning (fallback threshold=40)."""
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [
            MockStep(step_number=i, action_type="click", description=f"action {i}", success=True)
            for i in range(1, 16)
        ]
        diagnosis = _run(analyzer.analyze(
            task_description="Task",
            steps=steps,
            error="unknown",
        ))
        assert diagnosis.category != FailureCategory.AGENT_REASONING


# ---------------------------------------------------------------------------
# LLM fallback path
# ---------------------------------------------------------------------------


class TestLLMFallback:
    def test_llm_failure_falls_back_to_rules(self):
        """When LLM call fails, should fall back to rule-based result."""
        analyzer = FailureAnalyzer(api_key="sk-test", enable_llm=True)
        with patch.object(analyzer, "_call_haiku_sync", side_effect=Exception("API down")):
            # Use an error with low confidence regex match so LLM is attempted
            diagnosis = _run(analyzer.analyze(
                task_description="Do something",
                steps=[MockStep(success=False, error="xyzzy mystery")],
                error="xyzzy mystery",
            ))
        # Should fall back to rule_based UNKNOWN, not crash
        assert diagnosis.analysis_method == "rule_based"
        assert diagnosis.analysis_cost_cents == 0.0

    def test_llm_returns_none_falls_back(self):
        """When LLM returns None (e.g. empty response), fall back to rules."""
        analyzer = FailureAnalyzer(api_key="sk-test", enable_llm=True)
        with patch.object(analyzer, "_call_haiku_sync", return_value=None):
            diagnosis = _run(analyzer.analyze(
                task_description="Do something",
                steps=[],
                error="mystery error",
            ))
        assert diagnosis.analysis_method == "rule_based"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_agent_loop_requires_nonempty_description(self):
        """Steps with empty descriptions should NOT trigger loop detection."""
        analyzer = FailureAnalyzer(enable_llm=False)
        steps = [
            MockStep(step_number=i, action_type="click", description="", success=True)
            for i in range(1, 4)
        ]
        diagnosis = _run(analyzer.analyze(
            task_description="Task",
            steps=steps,
            error="unknown",
        ))
        # Should NOT be AGENT_LOOP since descriptions are empty
        assert diagnosis.category != FailureCategory.AGENT_LOOP

    def test_access_blocked_regex_does_not_match_bare_blocked(self):
        """The 'access_blocked' rule should not match a bare 'blocked' in element errors."""
        analyzer = FailureAnalyzer(enable_llm=False)
        # "element blocked by overlay" should NOT match access_blocked
        # (it should match overlay_blocking or fall through)
        diagnosis = _run(analyzer.analyze(
            task_description="Click button",
            steps=[MockStep(success=False, error="overlay blocked the click target")],
            error="overlay blocked the click target",
        ))
        assert diagnosis.category == FailureCategory.ELEMENT_INTERACTION
