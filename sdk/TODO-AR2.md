# TODO-AR2: Recovery Router + Retry Memory (Adaptive Retry)

## Phase 1: Task Tracking
- [x] Create sdk/TODO-AR2.md

## Phase 2: Retry Memory Module
- [x] Define AttemptRecord dataclass (10 fields)
- [x] Implement RetryMemory class (deque maxlen=3)
- [x] record() — build AttemptRecord from FailureDiagnosis
- [x] same_category_count() — count entries matching a category
- [x] all_failed_actions() — deduplicated set across all entries
- [x] get_context_for_prompt() — formatted string for task injection (<200 tokens)
- [x] to_list(), __len__(), clear()

## Phase 3: Recovery Router Module
- [x] Define RecoveryPlan frozen dataclass (11 fields incl. diagnosis_category)
- [x] Define CATEGORY_DEFAULTS dict (9 categories → env flags)
- [x] Define SYSTEM_MESSAGE_OVERRIDES dict (4 categories → system prompt strings)
- [x] Implement RecoveryRouter.plan_recovery()
- [x] Give-up logic: not retryable, max attempts, 3+ same category, captcha
- [x] _build_modified_task() — task rewriting with failure context + memory
- [x] Environment change routing: category defaults + diagnosis overrides
- [x] System message population from SYSTEM_MESSAGE_OVERRIDES

## Phase 4: Retry Memory Tests (7 tests)
- [x] test_record_and_retrieve
- [x] test_sliding_window
- [x] test_same_category_count
- [x] test_all_failed_actions
- [x] test_get_context_for_prompt
- [x] test_empty_memory_returns_empty_string
- [x] test_clear_resets

## Phase 5: Recovery Router Tests (8 tests)
- [x] test_element_failure_modifies_task
- [x] test_captcha_not_retryable
- [x] test_anti_bot_uses_fresh_browser
- [x] test_auth_clears_cookies
- [x] test_agent_loop_reduces_max_actions
- [x] test_memory_context_included
- [x] test_give_up_after_3_same_category
- [x] test_wait_seconds_from_diagnosis

## Phase 6: Verification
- [x] All existing tests pass (690 unit + 24 analyzer, 0 regressions)
- [x] New tests pass: 15/15
- [x] Import check succeeds
