# SDK Intelligence Integration — TODO

## Status: COMPLETE

All parts implemented. See git history for details.

## Parts Completed

### Part 0: SDK importable from workers
- `infra/Dockerfile.worker`: Added `COPY sdk/ ./sdk/` + `pip install ./sdk` in builder stage

### Part 1: workers/intelligence.py (new file)
- `run_failure_analysis()` — sync wrapper around `FailureAnalyzer.analyze()`
- `plan_recovery()` — sync wrapper around `RecoveryRouter.plan_recovery()`
- `build_analysis_json()` — assembles `RunAnalysis`-compatible dict
- `enrich_step_context()` — wraps `infer_intent_from_step()`

### Part 2: StepData.context field
- `workers/models.py`: Added `context: Optional[Dict[str, Any]] = None` to `StepData`

### Part 3: executor.py — BudgetMonitor + step enrichment
- `__init__`: `BudgetMonitor` initialized when `config.max_cost_cents` is set
- `_execute_native`: Budget enforcement after each step via `record_step_cost()`
- `_enrich_steps_from_history` (end of try block): Calls `enrich_step_context()` for each step

### Part 4: workers/tasks.py — analysis_json, step context, adaptive retry
- `_persist_result`: `context=step.context` added to `TaskStep` constructor
- `_maybe_auto_retry`: Full rewrite with failure analysis, recovery planning, retry_memory threading
- Call sites updated to pass `step_data=` and `error_message=` to `_maybe_auto_retry`

### Part 5: Dashboard UI
- `step-timeline.tsx`: Added "enrichment" type renderer in `StepContext`
- `retry-chain.tsx`: Added `extend_system_message` badge (5a), savings estimate (5b)
- `retry-stats-card.tsx`: Added category breakdown bar chart + diagnosis cost cell (5c)
- `dashboard/src/lib/types.ts`: Added `category_counts?` and `total_diagnosis_cost_cents?` to `RetryStatsResponse`
- `api/schemas/analytics.py`: Added optional `category_counts` and `total_diagnosis_cost_cents` to `RetryStats`
- `api/routes/analytics.py`: Added GROUP BY `error_category` query + `analysis_json` cost aggregation

## Verification Checklist

- [ ] Worker starts without import errors (SDK ImportError is graceful)
- [ ] Failed task triggers `run_failure_analysis()` → `task.analysis_json` populated
- [ ] Retry task carries `retry_memory` in `config_dict`
- [ ] Dashboard task detail shows analysis section
- [ ] Step detail panel shows "Intent" block when enrichment context present
- [ ] Health page retry stats shows category breakdown bars
- [ ] Health page shows diagnosis cost cell
- [ ] Inline retry chain shows "system prompt modified" badge
- [ ] Inline retry chain shows estimated savings line
