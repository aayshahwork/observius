# Test Failure Triage & Fix Tracker

## Summary

20 test failures across 6 files. **All are our bugs** introduced in recent commits.

---

## Category A — OUR BUGS (must fix)

### Fix 1: conftest.py — missing `browser_use.llm.anthropic.chat` + `browser_use.browser.session` stubs
- [x] Add `browser_use.llm.anthropic` stub module
- [x] Add `browser_use.llm.anthropic.chat` stub module with `ChatAnthropic=MagicMock`
- [x] Add `browser_use.browser.session` stub module with `BrowserSession=MagicMock`

**Root cause:** `workers/executor.py:413` imports `from browser_use.llm.anthropic.chat import ChatAnthropic` and `:422` imports `from browser_use.browser.session import BrowserSession`. The conftest only stubs `browser_use.llm` flat — no nested modules.
**Introduced in:** commit `71dd0b7` (executor import) — conftest never updated.
**Fixes:** 7 tests (2 in test_step_enrichment.py + 5 in test_executor.py)

### Fix 2: test_api_routes.py + test_ingest.py — mock Task missing `analysis_json`
- [x] Add `analysis_json=None` to `_make_task()` in test_api_routes.py
- [x] Add `analysis_json=None` to `_make_task()` in test_ingest.py

**Root cause:** `_task_to_response()` now reads `task.analysis_json` (added in commit `8ec5eb5`). MagicMock auto-generates a MagicMock for unset attrs, which fails Pydantic validation (`analysis` expects `dict | None`).
**Also:** Step mocks missing `context=None` (same root cause — `StepResponse.context` added in our commits)
**Fixes:** 10 tests (2 in test_api_routes.py + 8 in test_ingest.py)

### Fix 3: test_r2_presign.py — hardcoded bucket name
- [x] Replace hardcoded `"computeruse-recordings"` with `settings.R2_BUCKET_NAME`

**Root cause:** Test hardcoded `Bucket="computeruse-recordings"` but SDK's `computeruse/config.py` calls `load_dotenv(.env, override=True)` at import time, which sets `R2_BUCKET_NAME=pokant-sessions` in os.environ. Using `settings.R2_BUCKET_NAME` makes the test env-agnostic.
**Fixes:** 2 tests in test_r2_presign.py

### Fix 4: test_sdk.py — wrong patch target for `TaskExecutor`
- [x] Change `@patch("computeruse.client.TaskExecutor")` to `@patch("computeruse.executor.TaskExecutor")`

**Root cause:** Commit `3bf0b40` moved `TaskExecutor` from module-level import in `client.py` to a lazy import inside `_run_local()`. `@patch` can't find it at module level anymore.
**Fixes:** 3 tests in test_sdk.py

---

## Verification

After fixes:
```bash
cd sdk && python3 -m pytest tests/ -v --tb=short 2>&1 | tail -30
python3 -m pytest tests/unit/test_step_enrichment.py tests/unit/test_executor.py tests/unit/test_ingest.py tests/unit/test_api_routes.py tests/unit/test_r2_presign.py tests/unit/test_sdk.py -v --tb=short
```

**Goal:** 0 new failures. All 22 tests should now pass.

## Result

**All 22 previously-failing tests now pass.**
- Unit tests: 687/687 passed
- SDK tests: 521/521 passed

### Changes made:
1. `tests/conftest.py` — Added 3 stub modules + 1 env default
2. `tests/unit/test_api_routes.py` — Added `analysis_json=None` to mock
3. `tests/unit/test_ingest.py` — Added `analysis_json=None` to task mock + `context=None` to 3 step mocks
4. `tests/unit/test_sdk.py` — Changed patch target from `computeruse.client.TaskExecutor` to `computeruse.executor.TaskExecutor`
