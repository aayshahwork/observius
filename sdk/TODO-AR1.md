# TODO-AR1: Failure Analyzer Module (Adaptive Retry)

## Phase 1: Task Tracking
- [x] Create sdk/TODO-AR1.md

## Phase 2: Data Structures
- [x] Define FailureCategory StrEnum (9 members)
- [x] Define FailureDiagnosis dataclass with to_dict()

## Phase 3: Tier 1 Rule Engine
- [x] Define 15 regex rule patterns (IGNORECASE)
- [x] Implement _analyze_rules() — regex matching on error message
- [x] Implement step-history heuristics (agent_loop, agent_reasoning, nav fail)
- [x] Implement _summarize_progress() helper
- [x] Implement _format_steps_for_prompt() helper

## Phase 4: Tier 2 LLM Diagnostic
- [x] Implement _call_haiku_sync() — synchronous urllib POST to Anthropic API
- [x] Implement _analyze_llm() — async wrapper via asyncio.to_thread()
- [x] Haiku cost tracking: (input * 1.0 + output * 5.0) / 1_000_000 * 100

## Phase 5: Orchestrator
- [x] Implement FailureAnalyzer class with async analyze()
- [x] Tier 1 first, escalate to Tier 2 if confidence < 0.7

## Phase 6: Tests
- [x] Create sdk/tests/test_failure_analyzer.py (17 tests)
- [x] test_element_not_found_diagnosis
- [x] test_captcha_not_retryable
- [x] test_rate_limit_has_wait
- [x] test_agent_loop_from_step_history
- [x] test_overlay_blocking
- [x] test_session_expired
- [x] test_unknown_falls_through
- [x] test_llm_disabled_returns_rule_result
- [x] test_format_steps_compact

## Phase 7: Verification
- [x] All existing tests pass (690 unit tests, 0 regressions)
- [x] New tests pass: 17/17 passed
- [x] Import check succeeds
- [x] Rule-based smoke test succeeds
