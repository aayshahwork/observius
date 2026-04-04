# TODO-A3: CLI Commands, Dashboard Workflows Tab, README, Examples, PyPI Prep

## Phase 1: TODO doc
- [x] Create sdk/TODO-A3.md

## Phase 2: Update __init__.py exports
- [x] Uncomment WorkflowCompiler import
- [x] Add ReplayExecutor, ReplayConfig, ReplayResult, CompilationError, ReplayStepError imports
- [x] Add all to __all__

## Phase 3: CLI commands
- [x] Rename existing `replay` command to `open`
- [x] Add `compile` command (WorkflowCompiler)
- [x] Add `replay` command (ReplayExecutor, async bridge via asyncio.run)

## Phase 4: Dashboard API
- [x] GET /api/workflows — list from {data_dir}/workflows/*.json
- [x] GET /api/workflows/{name} — single workflow detail

## Phase 5: Dashboard UI
- [x] Add "Workflows" tab to tab bar
- [x] Workflow list view: name, steps, parameters, compiled_at
- [x] Workflow detail view: step table with intents/selectors, parameter list, cost comparison

## Phase 6: Update README
- [x] Add explore-to-replay quickstart (run → compile → replay)
- [x] Add post-action verification section
- [x] Add cost circuit breakers section
- [x] Update feature list
- [x] Update architecture diagram
- [x] Update CLI reference table

## Phase 7: Examples
- [x] Create sdk/examples/compile_workflow.py
- [x] Create sdk/examples/replay_workflow.py
- (browser_use_basic.py and playwright_basic.py already existed)

## Phase 8: PyPI prep
- [x] Bump version to 0.2.0 in pyproject.toml
- [x] Bump version to 0.2.0 in __init__.py
- [x] Verify python3 -m build succeeds (pokant-0.2.0-py3-none-any.whl)

## Phase 9: Verify
- [x] 511 passed, 3 pre-existing failures (stale enum count, UUID format, event loop)
- [x] `computeruse compile --help` works
- [x] `computeruse replay --help` works
- [x] `computeruse open --help` works

## Notes
- Renamed existing `replay` (opens HTML file) to `open` — `replay` now executes compiled workflows
- Pre-existing test failures unchanged from A2 (test_all_values, test_auto_generated_run_id_format, test_steps_returns_copy)
