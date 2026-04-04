# Contributing to Pokant SDK

## Dev Setup

```bash
cd sdk
pip install -e ".[dashboard]"
pip install pytest pytest-asyncio
pytest
```

Install Playwright browsers if running integration tests:

```bash
playwright install chromium
```

## Project Structure

```
computeruse/
  __init__.py          # Public exports
  client.py            # ComputerUse client (run_task API)
  wrap.py              # wrap() — reliability layer for browser-use agents
  track.py             # track() — tracked Playwright page context manager
  cost.py              # Token cost calculation
  error_classifier.py  # Exception → error category mapping
  retry_policy.py      # Retry decision logic
  stuck_detector.py    # Detect looping agents
  replay_generator.py  # HTML replay from step data
  models.py            # StepData, TaskConfig, TaskResult, ActionType
  dashboard.py         # FastAPI app for local dashboard
  cli/main.py          # Click CLI (computeruse command)
  templates/           # HTML/CSS templates for replays
  static/              # Static assets for dashboard
```

## Adding a New Agent Integration

To wrap a new agent framework (not just browser-use):

1. The agent must have a callable `run()` method (sync or async).
2. Add action-name mappings to `_ACTION_MAP` in `wrap.py` if the framework uses different action names.
3. Update `_enrich_steps()` in `WrappedAgent` to extract step data from the framework's history format.
4. Add an example script in `examples/`.
5. Add tests in `tests/`.

The `track()` path works with any Playwright `Page` object and requires no framework-specific integration.

## Code Style

- Type hints on all function signatures.
- No external dependencies in core modules (`wrap.py`, `track.py`, `cost.py`, `error_classifier.py`, `retry_policy.py`, `stuck_detector.py`). These import only from `computeruse.*` and the standard library.
- Use `dataclass(frozen=True)` for configuration objects.
- Use `logging.getLogger(__name__)` for log output, never `print()`.

## Testing

- Mock everything. Tests must run without a browser, API keys, or network access.
- Use `pytest` with `asyncio_mode = "auto"` (already configured in `pyproject.toml`).
- Test files go in `sdk/tests/` for SDK-specific tests or `tests/unit/` for integration with the broader repo.

```bash
# Run SDK tests only
cd sdk && pytest tests/ -v

# Run from repo root
pytest tests/unit -x -v
```

## Pull Requests

1. Branch off `main`.
2. Keep changes focused — one feature or fix per PR.
3. Include tests for new functionality.
4. Run `ruff check computeruse/` before submitting.
5. Describe what changed and why in the PR description.
