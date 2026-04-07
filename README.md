# ComputerUse

**Stop manually checking competitor websites. Automate it in 5 lines of Python.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Competitive Intelligence in 5 Lines

```python
from computeruse import ComputerUse

cu = ComputerUse()
result = cu.run_task(
    url="https://competitor.com/pricing",
    task="Extract all pricing plan names and their monthly prices",
    output_schema={"plans": "list[str]", "prices": "list[str]"}
)
print(result.result)
```

```json
{"plans": ["Starter", "Pro", "Enterprise"], "prices": ["$29/mo", "$99/mo", "Custom"]}
```

No selectors. No Puppeteer scripts. No maintenance when layouts change. Describe what you want in English, get structured JSON back.

---

## Install

```bash
pip install "git+https://github.com/aayshahwork/pokant.git#subdirectory=sdk"
playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Cloud Mode (No Local Browser)

Sign up at [computeruse.dev/signup](https://computeruse.dev/signup) for a free API key (500 steps/month), then run tasks on our infrastructure:

```python
cu = ComputerUse(local=False, api_key="cu_live_...")
result = cu.run_task(
    url="https://competitor.com/pricing",
    task="Extract all pricing tiers with features and prices",
    output_schema={"tiers": [{"name": "str", "price": "str", "features": "list[str]"}]}
)
```

Monitor every run at [computeruse.dev/tasks](https://computeruse.dev/tasks) — step-by-step screenshots, visual replays, cost tracking.

---

## Use Cases

### 1. Competitor Pricing Monitoring

```python
result = cu.run_task(
    url="https://competitor.com/pricing",
    task="Extract every pricing plan with name, monthly price, and included features",
    output_schema={"plans": [{"name": "str", "price": "str", "features": "list[str]"}]}
)
```

### 2. Job Posting Monitoring

```python
result = cu.run_task(
    url="https://competitor.com/careers",
    task="List all open engineering positions with title, team, and location",
    output_schema={"jobs": [{"title": "str", "team": "str", "location": "str"}]}
)
```

### 3. Product Review Aggregation

```python
result = cu.run_task(
    url="https://g2.com/products/competitor/reviews",
    task="Extract the 5 most recent reviews with rating, title, and summary",
    output_schema={"reviews": [{"rating": "int", "title": "str", "summary": "str"}]}
)
```

---

## How It Works

```
run_task(url, task, schema)
  → launches browser → AI agent navigates, clicks, extracts
  → validates output against your schema → returns structured JSON
```

Uses Claude + Playwright under the hood. Adapts to layout changes. Retries on failure. Generates a visual replay of every run.

---

## Why Not Do It Yourself?

| | ComputerUse | DIY (Selenium/Playwright) |
|---|---|---|
| **Setup** | `pip install` + 5 lines | Days of scripting per site |
| **Maintenance** | AI adapts to layout changes | Breaks when HTML changes |
| **Output** | Validated JSON to your schema | Raw HTML you parse yourself |
| **Error handling** | Built-in retry + recovery | You build it |
| **Observability** | Screenshot replay included | You build it |
| **New site** | Change the URL and task string | Write a new scraper |

---

## `run_task` API Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | *required* | Starting URL |
| `task` | `str` | *required* | Plain-English instruction |
| `output_schema` | `dict` | `None` | Expected output shape (`{"field": "type"}`) |
| `credentials` | `dict` | `None` | `{"username": "...", "password": "..."}` |
| `max_steps` | `int` | `50` | Max browser actions |
| `timeout_seconds` | `int` | `300` | Wall-clock timeout |
| `retry_attempts` | `int` | `3` | Retries on failure |

Returns a `TaskResult`: `result.result` (dict), `result.success` (bool), `result.steps` (list), `result.cost_cents` (float).

**Schema types:** `str`, `int`, `float`, `bool`, `list[str]`, `dict[str, str]`, nested objects.

---

## Development

```bash
git clone https://github.com/aayshahwork/pokant.git && cd pokant
make setup && make dev   # Postgres, Redis, API, worker, dashboard
```

Open [localhost:3000](http://localhost:3000). Tests: `pytest tests/unit -x -v`.

---

MIT — see [LICENSE](LICENSE). Need higher limits? [Contact us](https://computeruse.dev/contact).
