# AR5: Adaptive Retry Integration Tests

## Status: COMPLETE

## Overview
Integration tests for the adaptive retry system (AR3). All tests use mock
agents — no real browser or API calls. Verifies that FailureAnalyzer,
RecoveryRouter, and RetryMemory work correctly through wrap.py's retry loop.

## Checklist

### Shared Fixtures
- [x] MockResult, mock step builders, base WrapConfig helper
- [x] asyncio.sleep patch (autouse fixture)
- [x] _make_transient_error for dumb-retry path

### Tests
- [x] Test 1: Adaptive retry diagnoses and modifies task
- [x] Test 2: Dumb retry when adaptive is disabled
- [x] Test 3: Give up on non-retryable failure (CAPTCHA)
- [x] Test 4: Attempt history is saved
- [x] Test 5: Memory prevents repeating same approach
- [x] Test 6: Rule-based vs LLM diagnosis (zero cost)
- [x] Test 7: Run metadata JSON includes retry data
- [x] Test 8: Mid-run intervention injects hints
- [x] Test 9: Environment changes are applied (anti-bot)
- [x] Test 10: extend_system_message populated for agent_loop
- [x] Test 11: Partial enrichment on failure

### Verification
- [x] All 11 new tests pass
- [x] Existing sdk/tests/ tests still pass (169 passed)
