# ComputerUse SDK

The simplest way to automate web workflows with AI.

![Version](https://img.shields.io/badge/version-0.1.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Tests](https://img.shields.io/badge/tests-passing-brightgreen)

---

## Features

- ✅ **One-line task execution** — describe what to do in plain English, get structured results back
- ✅ **Automatic session persistence** — login once, reuse credentials across runs without re-authenticating
- ✅ **Structured output extraction** — define a schema, get validated, typed data back every time
- ✅ **Built-in retry with exponential backoff** — recovers from network blips and rate limits automatically
- ✅ **Local or cloud execution** — run on your machine or dispatch to a managed cloud browser
- ✅ **Full replay artifacts** — every run saves a step-by-step trace for debugging
- ✅ **CLI included** — run tasks, inspect sessions, and view replays directly from your terminal
- ✅ **Powered by Claude** — uses Anthropic's models via `browser-use` for reliable, context-aware automation

---

## Installation

```bash
pip install computeruse
```

Set your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Quick Start

```python
from computeruse import ComputerUse

cu = ComputerUse()
result = cu.run_task(url="https://news.ycombinator.com", task="Get the top 5 post titles", output_schema={"titles": "list[str]"})
print(result.result["titles"])
```

---

## Examples

Full runnable scripts are in [`examples/`](examples/).

### Login Automation

Automate a login flow. The session is saved automatically and restored on the next run — the agent skips the login form entirely.

```python
from computeruse import ComputerUse

cu = ComputerUse()

result = cu.run_task(
    url="https://github.com/login",
    task="Log in and confirm the login was successful",
    credentials={
        "username": "alice",
        "password": "s3cr3t",
    },
    output_schema={
        "logged_in": "bool",
        "username_displayed": "str",
    },
)

if result.success:
    print("Logged in as:", result.result["username_displayed"])
else:
    print("Login failed:", result.error)
```

### Data Extraction

Extract typed, validated data from any page. The `output_schema` defines exactly what fields to return and their types.

```python
from computeruse import ComputerUse

cu = ComputerUse()

result = cu.run_task(
    url="https://finance.yahoo.com/quote/AAPL",
    task="Get the current stock price and today's change percentage",
    output_schema={
        "price": "float",
        "change_pct": "float",
        "currency": "str",
    },
)

data = result.result
print(f"AAPL: {data['currency']}{data['price']}  ({data['change_pct']:+.2f}%)")
```

### Form Submission

Fill and submit a form, then capture the confirmation.

```python
from computeruse import ComputerUse, TaskExecutionError

cu = ComputerUse()

try:
    result = cu.run_task(
        url="https://example.com/contact",
        task=(
            "Fill in the contact form with the following details and submit it:\n"
            "  name: Alice Example\n"
            "  email: alice@example.com\n"
            "  message: Hello, I'd like more information about your product."
        ),
        output_schema={
            "submitted": "bool",
            "confirmation_message": "str",
        },
        max_steps=20,
    )
except TaskExecutionError as exc:
    print("Agent error:", exc)
else:
    if result.result.get("submitted"):
        print("Form submitted:", result.result["confirmation_message"])
    else:
        print("Form was not accepted:", result.result.get("confirmation_message"))
```

---

## Documentation

### Basic Usage

`run_task` is the primary entry point. All parameters except `url` and `task` are optional.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | — | Starting URL for the browser |
| `task` | `str` | — | Plain-English description of what to do |
| `credentials` | `dict` | `None` | Login credentials (`{"username": …, "password": …}`) |
| `output_schema` | `dict` | `None` | Fields to extract (`{"field": "type"}`) |
| `max_steps` | `int` | `50` | Max browser actions before aborting |
| `timeout_seconds` | `int` | `300` | Wall-clock timeout for the task |
| `retry_attempts` | `int` | `3` | Number of retries on recoverable failures |
| `retry_delay_seconds` | `int` | `2` | Base delay between retries (grows exponentially) |

**Supported schema types:** `str`, `int`, `float`, `bool`, `list`, `dict`, `list[str]`, `list[int]`, `dict[str, int]`, and other parameterised variants.

**`TaskResult` fields:**

```python
result.success        # bool — True if the task completed without error
result.result         # dict | None — extracted output data
result.error          # str | None — error message on failure
result.steps          # int — number of browser actions taken
result.duration_ms    # int — total wall-clock time in milliseconds
result.replay_path    # str | None — path to the local replay JSON file
result.replay_url     # str | None — URL to a hosted replay (cloud mode)
result.task_id        # str — unique identifier for this run
```

---

### Advanced Features

#### Session Persistence

Pass `credentials` and the SDK automatically saves cookies and Web Storage after the first successful run. Subsequent calls restore the session so the agent never has to log in again.

```python
# First run: logs in, saves session to ./sessions/github.com.json
result = cu.run_task(url="https://github.com", task="…", credentials={…})

# Second run: restores session, skips login form entirely
result = cu.run_task(url="https://github.com", task="…", credentials={…})
```

Manage sessions from the CLI:

```bash
computeruse sessions                        # list all saved sessions
computeruse sessions --delete github.com    # delete a specific session
```

#### Error Recovery

The SDK classifies errors as retryable (network issues, rate limits, HTTP 429/500/502/503) or non-retryable (bad credentials, schema mismatch). Retryable errors are automatically retried with exponential backoff.

Use the `RetryHandler` directly for fine-grained control:

```python
from computeruse.retry import RetryHandler

handler = RetryHandler(max_attempts=5, base_delay=1.0, max_delay=30.0)
result = await handler.execute_with_retry(my_async_func, arg1, arg2)
```

#### Custom Models

Override the default model per-client or per-task:

```python
# Use a different model for all tasks
cu = ComputerUse(model="claude-opus-4-5")

# Inspect the current model
print(cu.model)
```

#### Output Validation

The `OutputValidator` is used automatically when `output_schema` is provided, but you can use it standalone to parse and validate LLM responses:

```python
from computeruse.validator import OutputValidator

validator = OutputValidator()

# Parse JSON from an LLM response that may include markdown fencing
data = validator.parse_llm_json("```json\n{\"price\": \"9.99\"}\n```")

# Validate and coerce types against a schema
result = validator.validate_output(data, {"price": "float"})
# → {"price": 9.99}
```

#### Cloud Execution

Dispatch tasks to the hosted cloud service (no local browser required):

```python
cu = ComputerUse(
    local=False,
    api_key="your_computeruse_api_key",
)
result = cu.run_task(url="https://example.com", task="…")
```

#### Replay Inspection

Every run produces a replay JSON file at `./replays/<task_id>.json`. View it in the terminal:

```bash
computeruse replay replays/abc123.json
```

---

## Configuration

Copy `.env.example` to `.env` and fill in the values you need:

```bash
cp .env.example .env
```

```dotenv
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional — cloud browser sessions (BrowserBase)
BROWSERBASE_API_KEY=your_key_here

# Optional — S3 replay storage
AWS_ACCESS_KEY_ID=your_key_here
AWS_SECRET_ACCESS_KEY=your_secret_here
AWS_BUCKET_NAME=computeruse-replays
AWS_REGION=us-east-1

# Optional — backend services
DATABASE_URL=postgresql://localhost/computeruse
REDIS_URL=redis://localhost:6379/0

# Execution defaults (override per-task if needed)
DEFAULT_MODEL=claude-sonnet-4-5
DEFAULT_TIMEOUT=300
DEFAULT_MAX_STEPS=50

# Storage directories
SESSION_DIR=./sessions
REPLAY_DIR=./replays
```

---

## CLI Reference

```bash
# Run a task
computeruse run --url https://example.com --task "Get the page title"

# With schema and credentials
computeruse run \
  --url https://github.com/login \
  --task "Star the repo anthropics/anthropic-sdk-python" \
  --username alice \
  --password hunter2

# Open a visible browser window (useful for debugging)
computeruse run --url https://example.com --task "…" --no-headless

# Inspect a replay
computeruse replay replays/abc123.json

# Manage sessions
computeruse sessions
computeruse sessions --delete example.com

# Print version
computeruse version
```

---

## Local Development

```bash
git clone https://github.com/your-org/computeruse.git
cd computeruse

# Install dependencies (requires Poetry)
poetry install

# Install Playwright browsers
poetry run playwright install chromium

# Copy and configure environment
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY

# Run tests (no API key or browser needed)
poetry run pytest

# Run a single test file
poetry run pytest tests/test_retry.py -v

# Format and type-check
poetry run black computeruse/ tests/
poetry run mypy computeruse/
```

---

## API Reference

Full API documentation: [docs.computeruse.dev](https://docs.computeruse.dev) *(coming soon)*

**Core classes:**

| Class | Module | Description |
|-------|--------|-------------|
| `ComputerUse` | `computeruse.client` | Main client — start here |
| `TaskConfig` | `computeruse.models` | Task configuration model |
| `TaskResult` | `computeruse.models` | Task result model |
| `StepData` | `computeruse.models` | Individual step record |
| `SessionData` | `computeruse.models` | Persisted session model |
| `TaskExecutor` | `computeruse.executor` | Core orchestration engine |
| `BrowserManager` | `computeruse.browser_manager` | Browser lifecycle |
| `SessionManager` | `computeruse.session_manager` | Session persistence |
| `RetryHandler` | `computeruse.retry` | Retry and timeout logic |
| `OutputValidator` | `computeruse.validator` | Schema validation |

**Exceptions:**

| Exception | When raised |
|-----------|-------------|
| `ComputerUseError` | Base class — catch all SDK errors |
| `TaskExecutionError` | Agent failed mid-task |
| `BrowserError` | Browser launch or navigation failure |
| `ValidationError` | Output doesn't match schema |
| `AuthenticationError` | Credentials rejected |
| `TimeoutError` | Task exceeded `timeout_seconds` |
| `RetryExhaustedError` | All retry attempts consumed |
| `SessionError` | Session save/load failure |
| `APIError` | Cloud API returned an error (carries `status_code`) |

---

## Contributing

Contributions are welcome.

1. Fork the repository and create a feature branch.
2. Make your changes with tests — `poetry run pytest` must pass.
3. Keep code formatted with `black` and typed with `mypy`.
4. Open a pull request with a clear description of what changed and why.

Please open an issue before starting significant work so we can align on approach.

---

## License

MIT — see [LICENSE](LICENSE) for details.
