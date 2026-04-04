"""Tests for computeruse.recovery_router — failure-to-recovery mapping."""

from computeruse.failure_analyzer import FailureCategory, FailureDiagnosis
from computeruse.recovery_router import RecoveryRouter
from computeruse.retry_memory import AttemptRecord, RetryMemory


def _make_diagnosis(
    category: FailureCategory = FailureCategory.NAVIGATION,
    subcategory: str = "timeout",
    is_retryable: bool = True,
    root_cause: str = "Page timed out",
    retry_hint: str = "Increase the timeout",
    progress_achieved: str = "Loaded homepage",
    wait_seconds: int = 0,
    environment_changes: list[str] | None = None,
    failed_action: str = "",
    should_change_approach: bool = False,
) -> FailureDiagnosis:
    return FailureDiagnosis(
        category=category,
        subcategory=subcategory,
        is_retryable=is_retryable,
        root_cause=root_cause,
        retry_hint=retry_hint,
        progress_achieved=progress_achieved,
        wait_seconds=wait_seconds,
        environment_changes=environment_changes or [],
        confidence=0.85,
        analysis_method="rule_based",
        failed_action=failed_action,
        should_change_approach=should_change_approach,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_element_failure_modifies_task():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.ELEMENT_INTERACTION,
        subcategory="element_missing",
        root_cause="Button not found on page",
        retry_hint="Use text-based selectors instead of CSS",
    )
    plan = router.plan_recovery(
        original_task="Click the submit button",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert plan.diagnosis_category == "element_interaction"
    assert "Click the submit button" in plan.modified_task
    assert "Button not found on page" in plan.modified_task
    assert "text-based selectors" in plan.modified_task
    assert "text-based selectors" in plan.extend_system_message


def test_captcha_not_retryable():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.ANTI_BOT,
        subcategory="captcha",
        is_retryable=False,
    )
    plan = router.plan_recovery(
        original_task="Scrape pricing page",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is False
    assert plan.diagnosis_category == "anti_bot"


def test_anti_bot_uses_fresh_browser():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.ANTI_BOT,
        subcategory="rate_limited",
        wait_seconds=60,
    )
    plan = router.plan_recovery(
        original_task="Check prices",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert plan.fresh_browser is True
    assert plan.stealth_mode is True
    assert plan.wait_seconds >= 30


def test_auth_clears_cookies():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.AUTHENTICATION,
        subcategory="session_expired",
    )
    plan = router.plan_recovery(
        original_task="Download report",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert plan.diagnosis_category == "authentication"
    assert plan.clear_cookies is True
    assert plan.fresh_browser is True


def test_agent_loop_reduces_max_actions():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.AGENT_LOOP,
        subcategory="repeated_action",
        root_cause="Agent repeated 'click' action 3 times",
    )
    plan = router.plan_recovery(
        original_task="Fill out form",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert plan.diagnosis_category == "agent_loop"
    assert plan.reduce_max_actions is True
    assert "COMPLETELY DIFFERENT approach" in plan.extend_system_message


def test_memory_context_included():
    router = RecoveryRouter()
    memory = RetryMemory(max_entries=3)
    memory.record(AttemptRecord(
        attempt_number=1,
        category="navigation",
        root_cause="Timeout on first load",
        retry_hint="Increase timeout",
        progress_achieved="Nothing loaded",
        failed_actions=["navigate"],
    ))

    diagnosis = _make_diagnosis(
        category=FailureCategory.ELEMENT_INTERACTION,
        subcategory="element_missing",
        root_cause="Submit button not found",
        retry_hint="Use text selector",
        failed_action="click",
    )
    plan = router.plan_recovery(
        original_task="Submit the form",
        diagnosis=diagnosis,
        attempt_number=2,
        max_attempts=4,
        memory=memory,
    )

    assert plan.should_retry is True
    assert "EARLIER ATTEMPTS" in plan.modified_task
    assert "Timeout on first load" in plan.modified_task
    assert "Do NOT repeat these failed actions" in plan.modified_task
    assert "click" in plan.modified_task
    assert "navigate" in plan.modified_task


def test_give_up_after_3_same_category():
    router = RecoveryRouter()
    memory = RetryMemory(max_entries=3)
    for i in range(1, 4):
        memory.record(AttemptRecord(
            attempt_number=i,
            category="element_interaction",
            root_cause=f"Element missing attempt {i}",
            retry_hint="Try different selector",
            progress_achieved="",
        ))

    diagnosis = _make_diagnosis(
        category=FailureCategory.ELEMENT_INTERACTION,
        subcategory="element_missing",
    )
    plan = router.plan_recovery(
        original_task="Click button",
        diagnosis=diagnosis,
        attempt_number=4,
        max_attempts=5,
        memory=memory,
    )

    assert plan.should_retry is False
    assert plan.diagnosis_category == "element_interaction"


def test_give_up_when_max_attempts_reached():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(is_retryable=True)
    plan = router.plan_recovery(
        original_task="Navigate to page",
        diagnosis=diagnosis,
        attempt_number=3,
        max_attempts=3,
    )

    assert plan.should_retry is False
    assert plan.diagnosis_category == "navigation"


def test_captcha_give_up_even_when_marked_retryable():
    """Defense-in-depth: captcha should never retry even if LLM marks it retryable."""
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.ANTI_BOT,
        subcategory="captcha",
        is_retryable=True,
    )
    plan = router.plan_recovery(
        original_task="Scrape page",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is False
    assert plan.diagnosis_category == "anti_bot"


def test_agent_reasoning_system_message():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.AGENT_REASONING,
        subcategory="exhausted_steps",
    )
    plan = router.plan_recovery(
        original_task="Complete checkout",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert plan.reduce_max_actions is True
    assert "Re-read the task carefully" in plan.extend_system_message


def test_content_mismatch_system_message():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.CONTENT_MISMATCH,
        subcategory="content_missing",
    )
    plan = router.plan_recovery(
        original_task="Extract pricing",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    assert plan.should_retry is True
    assert "verify you are on the correct page" in plan.extend_system_message


def test_wait_seconds_from_diagnosis():
    router = RecoveryRouter()
    diagnosis = _make_diagnosis(
        category=FailureCategory.ANTI_BOT,
        subcategory="rate_limited",
        wait_seconds=60,
    )
    plan = router.plan_recovery(
        original_task="Fetch data",
        diagnosis=diagnosis,
        attempt_number=1,
        max_attempts=3,
    )

    # Should use max(category_default=30, diagnosis=60) = 60
    assert plan.wait_seconds == 60
