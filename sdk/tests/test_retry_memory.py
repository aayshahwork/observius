"""Tests for computeruse.retry_memory — sliding-window attempt buffer."""

from computeruse.retry_memory import AttemptRecord, RetryMemory


def _make_record(
    attempt_number: int = 1,
    category: str = "navigation",
    root_cause: str = "Page timed out",
    retry_hint: str = "Increase timeout",
    progress_achieved: str = "Loaded homepage",
    failed_actions: list[str] | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt_number=attempt_number,
        category=category,
        root_cause=root_cause,
        retry_hint=retry_hint,
        progress_achieved=progress_achieved,
        failed_actions=failed_actions or [],
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_record_and_retrieve():
    memory = RetryMemory(max_entries=3)
    memory.record(_make_record(attempt_number=1))
    memory.record(_make_record(attempt_number=2, category="anti_bot"))

    assert len(memory) == 2
    entries = memory.to_list()
    assert entries[0]["attempt_number"] == 1
    assert entries[1]["category"] == "anti_bot"


def test_sliding_window():
    memory = RetryMemory(max_entries=3)
    for i in range(1, 5):
        memory.record(_make_record(attempt_number=i))

    assert len(memory) == 3
    entries = memory.to_list()
    # First entry (attempt 1) should have been evicted
    assert entries[0]["attempt_number"] == 2
    assert entries[-1]["attempt_number"] == 4


def test_same_category_count():
    memory = RetryMemory(max_entries=3)
    memory.record(_make_record(category="element_interaction"))
    memory.record(_make_record(category="navigation"))
    memory.record(_make_record(category="element_interaction"))

    assert memory.same_category_count("element_interaction") == 2
    assert memory.same_category_count("navigation") == 1
    assert memory.same_category_count("unknown") == 0


def test_all_failed_actions():
    memory = RetryMemory(max_entries=3)
    memory.record(_make_record(failed_actions=["click", "type"]))
    memory.record(_make_record(failed_actions=["click", "scroll"]))

    actions = memory.all_failed_actions()
    assert actions == {"click", "type", "scroll"}


def test_get_context_for_prompt():
    memory = RetryMemory(max_entries=3)
    memory.record(_make_record(
        attempt_number=1,
        root_cause="Timeout on checkout",
        retry_hint="Increase timeout",
        failed_actions=["navigate"],
    ))
    memory.record(_make_record(
        attempt_number=2,
        root_cause="Element not found",
        retry_hint="Use text selector",
        failed_actions=["click"],
    ))

    prompt = memory.get_context_for_prompt()
    assert "EARLIER ATTEMPTS" in prompt
    assert "Attempt 1" in prompt
    assert "Timeout on checkout" in prompt
    assert "Strategy tried: Increase timeout" in prompt
    assert "Failed actions: navigate" in prompt
    assert "Attempt 2" in prompt
    assert "Element not found" in prompt


def test_empty_memory_returns_empty_string():
    memory = RetryMemory(max_entries=3)
    assert memory.get_context_for_prompt() == ""


def test_clear_resets():
    memory = RetryMemory(max_entries=3)
    memory.record(_make_record(attempt_number=1))
    memory.record(_make_record(attempt_number=2))
    assert len(memory) == 2

    memory.clear()
    assert len(memory) == 0
    assert memory.to_list() == []
    assert memory.get_context_for_prompt() == ""
