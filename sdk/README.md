# Pokant

Reliability and observability for browser automation agents.

**Before:**

```python
agent = Agent(task="Extract pricing", llm=llm, browser=browser)
result = await agent.run()
# Crashes on API errors. Loops forever. No idea what happened.
```

**After:**

```python
from computeruse import wrap
result = await wrap(agent).run()
# Auto-retry. Stuck detection. Screenshots. Cost tracking. Local dashboard.
```

Works with [browser-use](https://github.com/browser-use/browser-use) agents, [Stagehand](https://github.com/browserbase/stagehand) sessions, raw Playwright scripts, and desktop automation tools (PyAutoGUI, pywinauto, Claude computer_use). One import, zero config changes.

## Install

```bash
pip install pokant
```

## Quick Start: browser-use

```python
import asyncio
from browser_use import Agent
from langchain_anthropic import ChatAnthropic
from computeruse import wrap

async def main():
    llm = ChatAnthropic(model="claude-sonnet-4-5-20250514")
    agent = Agent(task="Find the top story on Hacker News", llm=llm)
    wrapped = wrap(agent)
    result = await wrapped.run()

    print(f"Cost: ${wrapped.cost_cents / 100:.4f}")
    print(f"Steps: {len(wrapped.steps)}")
    if wrapped.replay_path:
        print(f"Replay: {wrapped.replay_path}")

asyncio.run(main())
```

## Quick Start: Playwright

```python
import asyncio
from playwright.async_api import async_playwright
from computeruse import track

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        async with track(page) as t:
            await t.goto("https://example.com")
            await t.click("a")

        print(f"Steps: {len(t.steps)}")
        t.save_replay()
        await browser.close()

asyncio.run(main())
```

## Quick Start: Stagehand

```python
import asyncio
from playwright.async_api import async_playwright
from stagehand import AsyncStagehand
from computeruse import observe_stagehand

async def main():
    async with AsyncStagehand() as client:
        session = await client.sessions.start(
            model_name="anthropic/claude-sonnet-4-6",
        )

        # Connect Playwright for screenshots
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(session.data.cdp_url)
        page = browser.contexts[0].pages[0]

        async with observe_stagehand(session, page=page) as t:
            await t.navigate("https://example.com")
            await t.act("click the first link")
            data = await t.extract("get the page title", schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
            })

        print(f"Steps: {len(t.steps)}")
        t.save_replay()
        await session.end()
        await browser.close()
        await pw.stop()

asyncio.run(main())
```

Install Stagehand separately: `pip install pokant stagehand` (or `pip install pokant[stagehand]`).

## Quick Start: Explore-to-Replay

Run a task once with AI, compile it into a deterministic workflow, then replay it for free:

```bash
# 1. Run a task (AI-driven, costs tokens)
computeruse run --url https://example.com --task "Log in and export the report"

# 2. Compile the successful run into a replayable workflow
computeruse compile abc123 --name "export-report" --params "email,password"

# 3. Replay deterministically (no AI, no tokens, ~$0.00)
computeruse replay export-report --params '{"email":"me@co.com","password":"hunter2"}'
```

Or from Python:

```python
from computeruse import WorkflowCompiler, ReplayExecutor, ReplayConfig

# Compile
compiler = WorkflowCompiler()
workflow = compiler.compile_from_run(".pokant/runs/abc123.json")
compiler.save_workflow(workflow)

# Replay
executor = ReplayExecutor(config=ReplayConfig(headless=True, max_cost_cents=10))
result = await executor.execute(workflow, params={"email": "me@co.com"}, page=page)
print(f"Steps: {result.steps_executed}, Cost: ${result.cost_cents/100:.4f}")
```

The replay executor uses a 4-tier fallback cascade:
- **Tier 0**: Direct selector replay (deterministic, free)
- **Tier 1**: Selector healing via alternate selectors and text search (free)
- **Tier 2**: Single-shot AI call for selector recovery (cheap)
- **Tier 3**: Full AI fallback (configurable)

## View Results

```bash
pip install pokant[dashboard]
computeruse dashboard
```

Or from the CLI:

```bash
computeruse info          # summary of runs, costs, screenshots
computeruse clean         # delete runs older than 7 days
computeruse open .pokant/replays/abc123.html
```

## Features

- **Auto-retry** with exponential backoff and error classification
- **Stuck detection** catches looping agents (repeated screenshots, actions, failures)
- **Cost tracking** from token counts or browser-use's built-in totals
- **Cost circuit breakers** via BudgetMonitor — set a max spend per run and halt before overruns
- **Screenshot capture** at every step, saved to disk
- **HTML replay** generation for visual debugging
- **Workflow compiler** turns successful AI runs into deterministic Playwright workflows
- **Workflow replay** with 4-tier fallback (direct → heal → AI single-shot → full AI)
- **Post-action verification** confirms each step's expected outcome (URL, element, text)
- **Step enrichment** extracts selectors, intents, and metadata for compiled workflows
- **Run metadata** persisted as JSON for programmatic analysis
- **Session persistence** saves/restores cookies across runs
- **Error classification** maps exceptions to categories (transient, auth, timeout, etc.)
- **CLI tools** for compiling, replaying, inspecting runs, and launching the dashboard

## Configuration

### WrapConfig (browser-use agents)

```python
from computeruse import wrap, WrapConfig

config = WrapConfig(
    max_retries=3,                   # retry on transient errors
    enable_stuck_detection=True,     # detect looping agents
    stuck_screenshot_threshold=4,    # consecutive identical screenshots
    stuck_action_threshold=5,        # consecutive identical actions
    stuck_failure_threshold=3,       # consecutive failures
    track_cost=True,                 # calculate cost from tokens
    session_key="github.com",        # persist cookies across runs
    save_screenshots=True,           # save step screenshots to disk
    output_dir=".pokant",         # where to write all output
    generate_replay=True,            # create HTML replay file
    task_id=None,                    # custom task ID (auto-generated if None)
)

wrapped = wrap(agent, config=config)
result = await wrapped.run()
```

### TrackConfig (Playwright scripts)

```python
from computeruse import track, TrackConfig

config = TrackConfig(
    capture_screenshots=True,        # screenshot after each action
    retry_navigations=True,          # retry failed page.goto() calls
    max_navigation_retries=3,        # max retries per navigation
    session_key=None,                # persist cookies across runs
    output_dir=".pokant",         # where to write all output
    task_id=None,                    # custom run ID (auto-generated if None)
)

async with track(page, config=config) as t:
    await t.goto("https://example.com")
```

## Post-Action Verification

The `ActionVerifier` checks each step's outcome after execution:

```python
from computeruse import ActionVerifier

verifier = ActionVerifier()
result = await verifier.verify_action(
    page,
    action_type="click",
    expected_url_pattern="*/dashboard*",
    expected_element="#welcome-banner",
    expected_text="Welcome back",
    pre_url="https://app.example.com/login",
)
print(result.passed, result.checks_run)  # True, 3
```

Verification is built into both `track()` (for enriched steps) and `ReplayExecutor` (for workflow replay). Failures are recorded as warnings, not hard errors, so flaky checks don't block execution.

## Cost Circuit Breakers

`BudgetMonitor` enforces a per-run cost ceiling:

```python
from computeruse import BudgetMonitor, BudgetExceededError

monitor = BudgetMonitor(max_cost_cents=25.0)

# Record costs as they occur — raises BudgetExceededError when ceiling is hit
cost = monitor.record_step_cost(tokens_in=1500, tokens_out=200)
```

The `wrap()` function integrates BudgetMonitor automatically — set `max_cost_cents` in `WrapConfig`. The `ReplayExecutor` also accepts a budget via `ReplayConfig.max_cost_cents` and halts replay if AI fallback costs exceed the limit.

## Architecture

```
Your Agent --> wrap() / track() / observe_stagehand() --> Pokant Layer ----> .pokant/
                                    |                     |-- runs/*.json
                                    |-- Error Classifier  |-- screenshots/
                                    |-- Auto-Retry        |-- replays/*.html
                                    |-- Stuck Detector    '-- workflows/*.json
                                    |-- Cost Tracker
                                    |-- Budget Monitor
                                    '-- Session Manager
                                             |
                              +--------------+--------------+
                              |              |              |
                              v              v              v
                     computeruse      WorkflowCompiler  ReplayExecutor
                      dashboard       (compile runs)    (replay workflows)
```

## Any Language, Any Agent

Pokant works with agents written in any language via the REST ingest API.

### Python

```python
from computeruse import PokantTracker

tracker = PokantTracker(
    task_description="Extract pricing",
    api_url="http://localhost:8000",
    api_key="...",
)
tracker.start()
tracker.record_step(action_type="navigate", description="Opened portal")
tracker.record_step(action_type="extract", description="Got prices", tokens_in=1500)
tracker.complete(result={"price": 99})
```

### TypeScript / JavaScript

```typescript
import { PokantReporter } from "./pokant-reporter";

const reporter = new PokantReporter({ apiUrl: "http://localhost:8000", apiKey: "..." });
reporter.start("My task");
reporter.recordStep({ actionType: "navigate", description: "Opened portal" });
reporter.recordStep({ actionType: "extract", description: "Got prices", tokensIn: 1500 });
await reporter.complete();
```

### Any language (curl / HTTP)

```bash
curl -X POST http://localhost:8000/api/v1/tasks/ingest \
  -H "X-API-Key: ..." -H "Content-Type: application/json" \
  -d '{"status":"completed","steps":[{"step_number":0,"action_type":"navigate","description":"..."}]}'
```

See [Universal Integration Guide](docs/universal-integration.md) for the complete API reference, field tables, and examples in Go, TypeScript, and curl.

## Desktop Automation

Track desktop application automation alongside browser agents:

```python
from computeruse import PokantTracker
from computeruse.desktop import pyautogui_screenshot_fn

tracker = PokantTracker(
    screenshot_fn=pyautogui_screenshot_fn(),  # captures full screen
    task_description="Process invoices in SAP",
)
tracker.start()

# Your desktop automation code...
tracker.record_desktop_step(
    action_type="desktop_click",
    description="Clicked Submit in SAP",
    window_title="SAP Easy Access",
    coordinates=(450, 320),
)

tracker.complete()
# Full-screen screenshots on every step, replay, dashboard — same as browser agents
```

Supported screenshot methods: PyAutoGUI, Pillow ImageGrab, mss. Or pass any callable via `screenshot_fn=`.

See [examples/desktop_pyautogui.py](examples/desktop_pyautogui.py) and [examples/desktop_claude_computer_use.py](examples/desktop_claude_computer_use.py).

## CLI Reference

| Command | Description |
|---------|-------------|
| `computeruse run --url URL --task TASK` | Run a browser automation task |
| `computeruse compile TASK_ID` | Compile a successful run into a replayable workflow |
| `computeruse replay WORKFLOW_ID` | Replay a compiled workflow against a live browser |
| `computeruse info` | Show summary of run data |
| `computeruse dashboard` | Launch local debugging dashboard |
| `computeruse open FILE` | Open a replay file in the browser |
| `computeruse sessions` | List saved browser sessions |
| `computeruse clean` | Delete old run data |
| `computeruse version` | Print installed version |

## API Reference

### Exports from `computeruse`

| Export | Type | Description |
|--------|------|-------------|
| `wrap(agent, config=None, **kwargs)` | function | Add reliability layer to a browser-use Agent |
| `track(page, config=None, **kwargs)` | context manager | Track a Playwright Page with screenshots and timing |
| `WrapConfig` | dataclass | Configuration for `wrap()` |
| `TrackConfig` | dataclass | Configuration for `track()` |
| `WrappedAgent` | class | Returned by `wrap()`, call `.run()` on it |
| `TrackedPage` | class | Yielded by `track()`, use like a Playwright Page |
| `observe_stagehand(session, config=None, page=None, **kwargs)` | context manager | Track a Stagehand session with screenshots and timing |
| `StagehandConfig` | dataclass | Configuration for `observe_stagehand()` |
| `TrackedStagehand` | class | Yielded by `observe_stagehand()`, use like a Stagehand session |
| `classify_error(exc)` | function | Classify an exception into an error category |
| `should_retry_task(category, attempt, max_retries)` | function | Decide whether to retry based on error category |
| `StuckDetector` | class | Detect looping agents from step history |
| `ReplayGenerator` | class | Generate HTML replay from step data |
| `calculate_cost_cents(tokens_in, tokens_out)` | function | Estimate cost from token counts |
| `WorkflowCompiler` | class | Compile enriched runs into replayable workflows |
| `ReplayExecutor` | class | Execute compiled workflows with 4-tier fallback |
| `ReplayConfig` | dataclass | Configuration for `ReplayExecutor` |
| `ReplayResult` | dataclass | Result of workflow replay execution |
| `BudgetMonitor` | class | Cost circuit breaker with per-run ceiling |
| `ActionVerifier` | class | Post-action verification (URL, element, text) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
