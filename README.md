# Observius

**One API to automate any browser workflow.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/aayshahwork/observius?style=social)](https://github.com/aayshahwork/observius)

---

## Quick Start

```bash
pip install "git+https://github.com/aayshahwork/observius.git#subdirectory=sdk"
```

Set your API key in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Run your first automation:

```python
from computeruse import ComputerUse

cu = ComputerUse()
result = cu.run_task(
    url="https://news.ycombinator.com",
    task="Get the titles of the top 3 posts",
    output_schema={"titles": "list[str]"},
)
print(result.result)
```

**Output:**

```json
{
  "titles": [
    "Founder of GitLab battles cancer by founding companies",
    "Further human + AI + proof assistant work on Knuth's problem",
    "CSS is DOOMed"
  ]
}
```

---

## What This Is

Building a browser automation agent from scratch means stitching together at least five separate concerns: a browser driver (Playwright or Puppeteer), an LLM for navigation decisions, retry and error-recovery logic, structured output parsing and validation, and some kind of replay or observability layer. Each piece has its own failure modes. Most teams spend more time on infrastructure than on the actual task logic.

Observius collapses all of that into a single `run_task()` call. You describe what you want in plain English, optionally provide an output schema, and get back validated structured data. The agent handles navigation, adapts when page layouts change, retries on transient failures, and produces a visual replay of everything it did — without you touching any of the underlying machinery.

---

## Features

- ✅ One API call for any browser automation task
- ✅ AI-powered navigation that adapts to layout changes
- ✅ Structured output with schema validation
- ✅ Built-in error recovery and retry logic
- ✅ Visual replay of every execution
- ✅ Model-agnostic (Claude, GPT-4o, and others)
- ✅ Cloud execution via managed infrastructure
- 🔜 Workflow engine — multi-step, conditional, and scheduled tasks
- 🔜 Intelligence engine — improves task success rate over time

---

## Examples

| Example | Description |
|---------|-------------|
| [`examples/extract_pricing.py`](examples/extract_pricing.py) | Scrape pricing tiers from any SaaS landing page and return structured JSON |
| [`examples/monitor_competitors.py`](examples/monitor_competitors.py) | Check a competitor's site for changes and extract key data points |
| [`examples/fill_form.py`](examples/fill_form.py) | Fill and submit a multi-step web form using provided credentials |

Run any example directly after setting `ANTHROPIC_API_KEY`:

```bash
python examples/extract_pricing.py
```

---

## How It Works

```
Your code
    │
    ▼
Observius SDK          run_task(url, task, output_schema)
    │
    ▼
Browser Use + Claude   AI agent navigates, clicks, reads, extracts
    │
    ▼
Structured JSON        validated against your schema, ready to use
```

The SDK manages the full lifecycle: launching a browser, building the LLM prompt, running the agent loop, extracting and validating output, saving a replay, and returning a `TaskResult`. On failure it retries up to `retry_attempts` times before surfacing the error.

---

## Comparison

| Feature | Observius | Raw Browser Use | Selenium | UiPath |
|---------|-----------|----------------|----------|--------|
| Setup time | ~5 min | ~1 day | ~2 days | ~1 week |
| Output format | Structured JSON | Unstructured text | None | Proprietary |
| Adapts to layout changes | Yes (AI-driven) | Partial | No | No |
| Retry / error recovery | Built-in | Manual | Manual | Built-in |
| Replay / observability | Built-in | None | None | Paid add-on |
| Cost | LLM API only | LLM API only | Free | Expensive |
| Managed cloud option | Yes | No | No | Yes |

---

## Environment Setup

**1. Get an Anthropic API key**

Sign up at [console.anthropic.com](https://console.anthropic.com/settings/keys) and create a key.

**2. Create a `.env` file in the repo root**

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
```

**3. Install Playwright browsers** (required for local execution)

```bash
playwright install chromium
```

**4. Verify**

```python
from computeruse import ComputerUse
cu = ComputerUse()
print("Ready")
```

### `run_task` parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | — | Starting URL |
| `task` | `str` | — | Plain-English task description (max 2000 chars) |
| `credentials` | `dict` | `None` | `{"username": "…", "password": "…"}` |
| `output_schema` | `dict` | `None` | `{"field": "type"}` — defines what to extract |
| `max_steps` | `int` | `50` | Maximum browser actions before giving up |
| `timeout_seconds` | `int` | `300` | Wall-clock timeout |
| `retry_attempts` | `int` | `3` | Retries on recoverable failures |

**Supported schema types:** `str`, `int`, `float`, `bool`, `list`, `dict`, `list[str]`, `list[int]`, `dict[str, str]`, and other parameterised variants.

### Cloud execution (no local browser required)

```python
cu = ComputerUse(local=False, api_key="cu-...")
result = cu.run_task(url="https://example.com", task="...")
```

---

## Contributing

1. Fork the repo and create a feature branch off `main`.
2. Install dependencies: `cd sdk && pip install -e .`
3. Run the unit test suite (no live services needed): `pytest tests/unit -x -v`
4. Check formatting and types: `ruff check sdk/ && mypy api/ workers/`
5. Open a pull request. Describe what changed and why — include a failing test or example that demonstrates the issue if it's a bug fix.

All tests must pass and ruff must report no errors before a PR is merged.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

⭐ If Observius is useful, please star the repo — it helps others find it.
