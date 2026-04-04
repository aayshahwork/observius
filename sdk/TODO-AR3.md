# AR3: Adaptive Retry Integration into wrap.py

## Status: COMPLETE

## Overview
Replace the dumb retry loop in wrap.py with an adaptive retry system that
diagnoses failures (FailureAnalyzer), plans recovery (RecoveryRouter),
remembers previous attempts (RetryMemory), and injects modified context
into the browser_use Agent before each retry.

## Checklist

### Phase 1: Models + Config
- [x] Add `attempt_number` field to StepData (models.py)
- [x] Add `adaptive_retry`, `diagnostic_model`, `diagnostic_api_key` to WrapConfig

### Phase 2: WrappedAgent Init
- [x] Add `_attempt_history: list[dict]`
- [x] Add `_consecutive_step_failures: int`
- [x] Add `attempt_history` property

### Phase 3: Helper Methods
- [x] `_get_last_url()` — extract URL from last step
- [x] `_enrich_steps_partial()` — extract step data from failed runs

### Phase 4: Recovery Methods
- [x] `_apply_recovery_environment(plan)` — browser close, cookies, timeout, max_actions
- [x] `_inject_recovery_context(plan)` — 3-tier context injection

### Phase 5: Core Retry Loop
- [x] Replace retry loop in `run()` with adaptive branch + dumb fallback
- [x] Wire FailureAnalyzer, RecoveryRouter, RetryMemory into the loop
- [x] Re-wire `calculate_cost=True` on new agent in Strategy 1

### Phase 6: Mid-Run Intervention
- [x] Extend `_on_step_end()` with consecutive failure tracking + hint injection

### Phase 7: Metadata
- [x] Add `attempts`, `total_attempts`, `adaptive_retry_used` to `_save_run_metadata`

### Phase 8: Exports
- [x] Update `__init__.py` with AR module exports

### Phase 9: Verification
- [x] All existing unit tests pass
- [x] Manual smoke test: WrapConfig(adaptive_retry=True) works
- [x] Manual smoke test: attempt_history property exists

## API Validation (browser_use)
- `Agent.state` accessible on instance: YES
- `Agent.add_new_task(str)`: YES
- `Agent.message_manager`: YES (instance attr)
- `extend_system_message` constructor param: YES
- `injected_agent_state` constructor param: YES
- `AgentState` fields: stopped, paused, consecutive_failures, n_steps, follow_up_task

## Spec Corrections Applied
1. AttemptRecord field: `analysis_method` (not `diagnosis_method`)
2. FailureAnalyzer already has `model` param — no change needed
3. _enrich_steps_partial uses inline getattr (no helper methods to extract)
4. AgentState reset: also set follow_up_task=True, reset n_steps
5. Re-wire calculate_cost=True on new agent in Strategy 1
