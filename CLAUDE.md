# Pokant (ComputerUse.dev)

## Project Overview
**One API to automate any browser workflow.**
Managed orchestration platform for browser-based AI automation.
Monorepo: Python backend (FastAPI + Celery) + TypeScript frontend (Next.js 15).

**Version:** 0.2.0
**Python:** 3.11+
**Node:** 20+

## Tech Stack

### Backend
- **API:** FastAPI, uvicorn, structlog (JSON prod / console dev), Prometheus
- **Queue:** Celery + Redis (Upstash), tier-based queues (`tasks:free` / `tasks:startup` / `tasks:enterprise`)
- **Database:** PostgreSQL 16 (Supabase), SQLAlchemy async, UUIDv7 PKs, Row-Level Security
- **Browser:** Playwright + Browserbase (cloud CDP sessions)
- **LLM:** Anthropic Claude API (`claude-sonnet-4-5` default)
- **Storage:** Cloudflare R2 (S3-compatible) for replays/recordings/screenshots
- **Payments:** Stripe (subscriptions + usage metering)
- **Credentials:** AES-256-GCM with HKDF-derived per-account keys

### Frontend
- **Framework:** Next.js 15 (App Router), React 19, TypeScript
- **UI:** Tailwind CSS 3, shadcn/ui, Lucide icons
- **Charts:** Recharts 3
- **Notifications:** Sonner (toasts)
- **Testing:** Playwright (e2e)

### SDK (`pip install computeruse`)
- Local Playwright execution or cloud dispatch
- Reliability: error classification, stuck detection, adaptive retry, failure analysis, recovery routing
- Observability: cost tracking, budget monitoring, replay generation, analysis (3-tier), alerts
- Automation: workflow compilation, replay execution, selector healing, step enrichment
- Integrations: Stagehand wrapper, desktop automation, generic tracker

## Directory Structure
```
/api                     FastAPI backend
├── main.py              App entry, middleware, health/metrics
├── config.py            Pydantic Settings (all env vars)
├── dependencies.py      Shared FastAPI dependency injection
├── local_bridge.py      Dev-mode local API for demos
├── db/
│   ├── engine.py        SQLAlchemy async engine + session factory
│   ├── migrations/      12 SQL migration files (UUIDv7, RLS, alerts, workflows)
│   └── init-migrations.sh
├── middleware/
│   ├── auth.py          SHA-256 key hashing + RLS context
│   ├── rate_limiter.py  Sliding window (Redis)
│   ├── logging.py       StructuredLoggingMiddleware
│   ├── metrics.py       PrometheusMiddleware
│   └── credential_scrubber.py  Strips secrets from logs
├── models/              SQLAlchemy ORM
│   ├── account.py       Account (tier, usage)
│   ├── task.py          Task (status, result, cost, analysis_json, compiled_workflow, playwright_script)
│   ├── task_step.py     TaskStep (action, screenshot, timing, context)
│   ├── api_key.py       ApiKey (SHA-256 hash)
│   ├── session.py       Session (encrypted cookies)
│   ├── alert.py         Alert (failure/cost alerts)
│   └── audit_log.py     AuditLog (immutable, RLS)
├── routes/
│   ├── tasks.py         POST/GET/DELETE tasks, ingest, retry, replay, compile, script
│   ├── analytics.py     Fleet health aggregates
│   ├── alerts.py        Alert list + acknowledge
│   ├── sessions.py      Browser session CRUD
│   ├── billing.py       Stripe subscription + checkout
│   ├── account.py       API key CRUD
│   ├── audit.py         Audit log queries
│   └── local_files.py   Dev-mode local file serving (screenshots)
├── schemas/             Pydantic v2 request/response models
├── services/
│   ├── audit_logger.py  Append-only audit log
│   ├── usage_tracker.py Atomic step billing metering
│   └── r2.py            R2 presigned URL generation
└── management/
    └── reset_usage.py   Reset monthly_steps_used script

/workers                 Celery task workers
├── main.py              Celery app config, tier-based queue routing
├── tasks.py             execute_task + deliver_webhook Celery tasks
├── executor.py          Core TaskExecutor: Playwright + LLM loop, screenshots
├── browser_manager.py   Playwright lifecycle, stealth scripts injection
├── models.py            ActionType, StepData, TaskConfig, TaskResult (dataclasses)
├── config.py            WorkerSettings (pydantic-settings)
├── db.py                Sync session factory for Celery
├── encryption.py        AES-256-GCM + HKDF key derivation
├── credential_injector.py  Login form credential injection
├── captcha_solver.py    2Captcha integration
├── session_manager.py   Encrypted cookie persistence
├── replay.py            Replay HTML generation + R2 upload
├── stuck_detector.py    Visual stagnation, action repetition, failure spirals
├── error_classifier.py  Classify errors (transient vs permanent)
├── retry_policy.py      Retry strategies
├── retry.py             RetryHandler
├── output_validator.py  Schema validation + type coercion
├── canary.py            Canary deployment metrics
├── metrics.py           Prometheus metrics (Celery signals)
├── shutdown.py          Graceful SIGTERM handler
├── stealth/             7 browser fingerprint-evasion JS scripts
└── templates/           Replay HTML template

/sdk                     Python SDK package (v0.2.0)
└── computeruse/
    ├── __init__.py      All exports (lazy imports for optional deps)
    ├── client.py        ComputerUse class (local + cloud modes)
    ├── executor.py      Local TaskExecutor (Playwright, LLM loop)
    ├── models.py        TaskConfig, TaskResult, CompiledStep, CompiledWorkflow
    ├── config.py        SDK Settings
    ├── browser_manager.py  Local Playwright setup
    ├── session_manager.py  Session persistence
    ├── validator.py     Output schema validation
    ├── cost.py          Token-based cost calculation
    │
    │ ── Reliability ──
    ├── error_classifier.py  Error classification (transient vs permanent)
    ├── stuck_detector.py    Stuck pattern detection
    ├── retry_policy.py      Retry logic
    ├── failure_analyzer.py  Deep failure analysis (FailureCategory, FailureDiagnosis)
    ├── recovery_router.py   Recovery routing (RecoveryPlan, RecoveryRouter)
    ├── retry_memory.py      Cross-run retry memory (AttemptRecord, RetryMemory)
    │
    │ ── Verification & Enrichment ──
    ├── action_verifier.py   Post-action verification (ActionVerifier, VerificationResult)
    ├── budget.py            Per-run cost limits (BudgetMonitor, BudgetExceededError)
    ├── step_enrichment.py   Intent extraction + selector annotation
    │
    │ ── Workflow Automation ──
    ├── compiler.py          Compile runs into replayable workflows (WorkflowCompiler)
    ├── replay_executor.py   Deterministic replay of compiled workflows (ReplayExecutor)
    ├── selector_healer.py   Auto-heal broken CSS selectors during replay
    │
    │ ── Observability ──
    ├── analyzer.py      3-tier analysis (rule → history → LLM)
    ├── alerts.py        AlertConfig, AlertEmitter (callbacks + webhooks)
    ├── tracker.py       PokantTracker generic reporter
    ├── _reporting.py    API task ingest POST
    ├── wrap.py          Agent wrapping for observability + enrichment reporting
    ├── track.py         Page tracking decorator
    ├── replay_generator.py  Self-contained HTML replay generation
    ├── dashboard.py     Dashboard helpers
    │
    │ ── Integrations ──
    ├── stagehand.py     Stagehand session tracking wrapper
    ├── desktop.py       Desktop screenshot helpers (pyautogui, Pillow, mss)
    │
    ├── cli/main.py      CLI dashboard (observius / computeruse aliases)
    ├── templates/       HTML/CSS replay templates
    └── static/          Static assets (index.html dashboard)

/shared                  Shared Python modules
├── constants.py         TaskStatus, ErrorCode, TIER_LIMITS, TIER_STEP_LIMITS
├── db.py                Shared DB helpers
├── errors.py            Exception hierarchy (ComputerUseError subclasses)
├── storage.py           R2/S3 client wrapper
├── url_validator.py     SSRF-safe URL validation (blocks private IPs)
└── browser_provider.py  Browser factory

/dashboard               Next.js 15 frontend
├── src/app/
│   ├── (auth)/login     Login page
│   └── (dashboard)/
│       ├── overview/    Fleet health overview
│       ├── tasks/       Task list + [id] detail + new
│       ├── health/      Health analytics metrics
│       ├── usage/       Cost/token analytics
│       ├── sessions/    Browser session management
│       ├── scripts/     Compiled workflow script viewer
│       └── settings/    API keys, billing
├── src/components/
│   ├── ui/              shadcn/ui (button, card, dialog, table, tabs, etc.)
│   ├── health/          Health dashboard (score card, error breakdown,
│   │                    executor cards, failure hotspots, hourly activity,
│   │                    metrics bar, period selector, retry stats card)
│   ├── layout/          Sidebar, header, auth guard
│   ├── task-table.tsx   Paginated task list
│   ├── step-timeline.tsx  Step detail with screenshots
│   ├── replay-viewer.tsx  Replay playback
│   ├── analysis-panel.tsx  3-tier analysis results
│   ├── alert-bell.tsx   Unread alerts notification
│   ├── session-drawer.tsx  Session management sheet
│   ├── retry-chain.tsx  Retry attempt visualization (expanded)
│   ├── workflow-panel.tsx  Compiled workflow viewer
│   ├── error-chart.tsx  Error category distribution
│   ├── cost-chart.tsx   Cost over time
│   ├── usage-chart.tsx  Usage metrics
│   ├── executor-comparison.tsx  browser_use vs native stats
│   ├── expensive-tasks-table.tsx  Top tasks by cost
│   ├── json-viewer.tsx / json-editor.tsx
│   ├── status-badge.tsx Task status indicators
│   ├── confirm-dialog.tsx
│   └── empty-state.tsx
├── src/contexts/        Auth context (no login required), theme context
├── src/hooks/           Custom React hooks
├── src/lib/
│   ├── api-client.ts    API client with workflow/script endpoints
│   ├── types.ts         TypeScript types (incl. CompiledWorkflow, PlaywrightScript)
│   └── workflow-utils.ts  Workflow display helpers
└── e2e/                 Playwright e2e tests

/tests                   Backend test suite
├── conftest.py          Stubs heavy deps, sets env defaults
├── unit/                ~20+ test files (no external services)
├── integration/         Live service tests
├── e2e/                 End-to-end tests
└── load/                Locust load testing

/e2e_tests               SDK end-to-end tests
├── run_all.py           Test runner
├── test_1_enrichment.py
├── test_2_cost_tracking.py
├── test_3_budget_breaker.py
├── test_4_verification.py
├── test_5_full_pipeline.py
├── test_6_click_chain.py
└── test_7_interrupt_safety.py

/infra                   Docker & deployment
├── Dockerfile.api
├── Dockerfile.worker
├── Dockerfile.dashboard
├── docker-compose.prod.yml
└── railway.toml         Railway deployment config

/examples                Standalone runnable examples
├── extract_pricing.py
├── fill_form.py
├── monitor_competitors.py
├── stagehand_example.py
├── compile_workflow.py  (in sdk/examples/)
└── replay_workflow.py   (in sdk/examples/)

/scripts                 Dev scripts
├── setup.sh
└── worker_health.py
```

## Commands

All commands run from the repo root unless noted.

```bash
# === Backend ===

# Unit tests (no external services)
pytest tests/unit -x -v

# SDK tests
cd sdk && pytest tests/ -x -v

# Single test file / function
pytest tests/unit/test_retry.py -v
pytest tests/unit/test_retry.py::TestRetryHandler::test_backoff_delay -v

# Lint
ruff check api/ workers/ sdk/ shared/

# Type check
mypy api/ workers/

# Run API locally
uvicorn api.main:app --reload --port 8000

# Run Celery worker locally
celery -A workers.main worker --loglevel=info --pool=prefork --concurrency=2

# === Frontend ===

cd dashboard && npm run dev       # Dev server (port 3000)
cd dashboard && npm run lint      # ESLint
cd dashboard && npx tsc --noEmit  # Type check

# E2E tests (frontend)
cd dashboard && npx playwright test
cd dashboard && npx playwright test --headed
cd dashboard && npx playwright test --ui

# === Docker ===

make dev          # docker compose up --build (auto-creates .env from .env.example)
make build        # docker compose build
make fresh        # tear down volumes + rebuild
make migrate      # run DB migrations
make reset-db     # drop + recreate + migrate
make logs         # follow all logs
make logs-api     # follow API logs
make logs-worker  # follow worker logs
make shell-db     # psql into postgres
make shell-api    # bash into API container
make load-test    # Locust load test
make setup        # Initial dev setup (scripts/setup.sh)
```

## API Routes

### Tasks — `/api/v1/tasks`
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/tasks` | Create task (queued to tier-based Redis queue) |
| POST | `/api/v1/tasks/ingest` | Ingest SDK-completed task with screenshots |
| GET | `/api/v1/tasks` | List tasks (paginated) |
| GET | `/api/v1/tasks/{id}` | Get task by ID (incl. compiled_workflow, playwright_script) |
| DELETE | `/api/v1/tasks/{id}` | Cancel task |
| POST | `/api/v1/tasks/{id}/retry` | Retry failed task |
| GET | `/api/v1/tasks/{id}/steps` | Get step details |
| GET | `/api/v1/tasks/{id}/replay` | Get signed replay URL |

### Analytics — `/api/v1/analytics`
| GET | `/api/v1/analytics/health` | Fleet health aggregates (period: 1h/6h/24h/7d/30d) |

### Alerts — `/api/v1/alerts`
| GET | `/api/v1/alerts` | List alerts (paginated) |
| POST | `/api/v1/alerts/{id}/ack` | Acknowledge alert |

### Sessions — `/api/v1/sessions`
| GET/POST/DELETE | `/api/v1/sessions[/{id}]` | Browser session CRUD |

### Billing — `/api/v1/billing`
| GET | `/api/v1/billing/subscription` | Get subscription |
| POST | `/api/v1/billing/checkout` | Stripe checkout session |

### Account — `/api/v1/account`
| GET/POST/DELETE | `/api/v1/account/keys[/{id}]` | API key CRUD |

### Audit — `/api/v1/audit`
| GET | `/api/v1/audit` | Query audit logs |

### Infrastructure
| GET | `/health` | Health check (DB + Redis) |
| GET | `/metrics` | Prometheus metrics |

## Architecture & Data Flow

### Request Lifecycle (Cloud)
```
Client → POST /api/v1/tasks (X-API-Key header)
  ├── auth.py: SHA-256 hash key → api_keys lookup → SET LOCAL app.account_id (RLS)
  ├── rate_limiter.py: sliding window (Redis)
  ├── url_validator.py: SSRF protection
  ├── tasks.py: check concurrent limit (TIER_LIMITS) → insert Task row
  │   → enqueue to tier-based Celery queue
  └── return 202 Accepted

workers/tasks.py → execute_task (Celery)
  ├── Atomic claim (UPDATE WHERE status='queued') + Redis lock
  ├── workers/executor.py: TaskExecutor.execute()
  │   ├── browser_manager.py: Playwright via Browserbase CDP
  │   ├── session_manager.py: restore encrypted cookies
  │   ├── credential_injector.py: inject creds into login forms
  │   ├── browser_use.Agent: drives browser step-by-step
  │   ├── Per-step screenshot capture → base64
  │   ├── captcha_solver.py: 2Captcha (if needed)
  │   ├── stuck_detector.py: visual stagnation / action repetition
  │   ├── output_validator.py: schema coercion
  │   └── session_manager.py: save encrypted cookies
  ├── Persist TaskResult + TaskSteps to Postgres
  ├── Increment account.monthly_steps_used
  ├── replay.py: generate HTML + upload to R2
  └── deliver_webhook: HMAC-SHA256 signed POST
```

### SDK → Dashboard Flow
```
SDK runs task locally → POST /api/v1/tasks/ingest (PokantTracker)
  → API stores task + steps + screenshots
  → Dashboard polls GET /api/v1/tasks → renders task list + replay + analysis
```

### Authentication
- API keys stored as SHA-256 hashes in `api_keys` table (raw key never stored)
- `SET LOCAL app.account_id` on each DB connection for Postgres RLS
- All routes use `get_current_account` FastAPI dependency
- Dashboard: login not required (auth guard removed for local dev)

### Tier System
Defined in `shared/constants.py`. Queues: `tasks:free`, `tasks:startup`, `tasks:enterprise`.

| Tier | Concurrent | Max Steps | Timeout | Monthly Steps |
|------|-----------|-----------|---------|---------------|
| free | 1 | 50 | 120s | 500 |
| startup | 5 | 200 | 300s | 5,000 |
| growth | 10 | 350 | 450s | 25,000 |
| enterprise | 20 | 500 | 600s | 100,000 |

### Credential Encryption
`workers/encryption.py`: HKDF derives a per-account AES-256-GCM key from `ENCRYPTION_MASTER_KEY`. Random 96-bit nonces. Encrypted at rest, decrypted at task execution time.

### Executor Modes
- **browser_use** — DOM-based agent (browser_use.Agent) — default
- **native** — Screenshot pixel-based (Claude `computer_use` tool)

### Error Classification
Categories in `workers/error_classifier.py` and `sdk/computeruse/error_classifier.py`:
- **Transient (retriable):** `TRANSIENT_LLM`, `RATE_LIMITED`, `TRANSIENT_NETWORK`, `TRANSIENT_BROWSER`
- **Permanent:** `PERMANENT_LLM`, `PERMANENT_BROWSER`, `PERMANENT_TASK`
- **Unknown:** `UNKNOWN`

### Analysis System (3-Tier)
`sdk/computeruse/analyzer.py`:
1. **RuleAnalyzer** — Pattern matching (stuck loops, permission errors, timeouts)
2. **HistoryAnalyzer** — Cross-run failure correlation
3. **LLMAnalyzer** — Claude-powered root cause analysis

### Adaptive Retry System (v0.2.0)
`sdk/computeruse/failure_analyzer.py` + `recovery_router.py` + `retry_memory.py`:
- **FailureAnalyzer** — Deep diagnosis with `FailureCategory` + `FailureDiagnosis`
- **RecoveryRouter** — Maps failures to `RecoveryPlan` (strategy, parameter adjustments)
- **RetryMemory** — Persists `AttemptRecord` across runs to avoid repeating failed strategies

### Stuck Detection
`workers/stuck_detector.py` / `sdk/computeruse/stuck_detector.py`:
- Visual stagnation (screenshot similarity)
- Action repetition (same action N times)
- Failure spirals (consecutive errors)

### Post-Action Verification (v0.2.0)
`sdk/computeruse/action_verifier.py`:
- **ActionVerifier** — Validates each step produced the expected outcome
- **VerificationResult** — Pass/fail with evidence

### Budget Monitoring (v0.2.0)
`sdk/computeruse/budget.py`:
- **BudgetMonitor** — Per-run cost limits (`max_cost_cents`)
- **BudgetExceededError** — Raised when budget is breached

### Workflow Compilation & Replay (v0.2.0)
- **WorkflowCompiler** (`compiler.py`) — Compile successful runs into `CompiledWorkflow`
- **ReplayExecutor** (`replay_executor.py`) — Deterministic replay with `ReplayConfig`
- **SelectorHealer** (`selector_healer.py`) — Auto-heal broken CSS selectors during replay
- **StepEnrichment** (`step_enrichment.py`) — Intent extraction + selector annotation

### Alerts
`sdk/computeruse/alerts.py`: `AlertConfig` + `AlertEmitter`
- On-failure callbacks
- Webhook alerts
- Cost threshold alerts
- Stored in `alerts` DB table, surfaced via API + dashboard bell

## Database Schema (PostgreSQL 16)

**UUIDv7** primary keys (temporal ordering). **Row-Level Security** for account isolation.

| Table | Purpose |
|-------|---------|
| `accounts` | User accounts (tier, monthly limits, webhook_secret) |
| `api_keys` | Hashed API keys (SHA-256) |
| `tasks` | Task records (status, result, cost, executor_mode, analysis_json, compiled_workflow, playwright_script) |
| `task_steps` | Step data (action, screenshot, timing, context JSONB) |
| `sessions` | Browser sessions (encrypted cookies) |
| `audit_logs` | Immutable audit trail (RLS enforced) |
| `alerts` | Alert records (failure/cost alerts, acknowledgement) |

### Migrations (`api/db/migrations/`)
1. `001_initial_schema.sql` — UUIDv7 function, all tables, indexes, RLS policies
2. `002_seed_data.sql` — Test account + API key
3. `003_audit_log_insert_policy.sql` — Audit log RLS
4. `004_task_retry_columns.sql` — retry_count, retry_of_task_id
5. `005_accounts_webhook_secret.sql` — webhook_secret column
6. `006_task_executor_mode.sql` — executor_mode (browser_use|native)
7. `007_alerts_table.sql` — Alerts table + RLS
8. `008_analytics_indexes.sql` — Performance indexes for analytics
9. `009_task_steps_context.sql` — context JSONB column on task_steps
10. `010_task_analysis_json.sql` — analysis_json JSONB column on tasks
11. `011_task_compiled_workflow.sql` — compiled_workflow JSONB column on tasks
12. `012_task_playwright_script.sql` — playwright_script TEXT column on tasks

## SDK Public API (v0.2.0)

```python
from computeruse import ComputerUse

cu = ComputerUse()
result = cu.run_task(
    url="https://example.com",
    task="Extract the page title",
    output_schema={"title": "str"}
)
print(result.result["title"])
```

### Core Exports
- **Client:** `ComputerUse`
- **Models:** `TaskConfig`, `TaskResult`, `ActionType`, `StepData`, `CompiledStep`, `CompiledWorkflow`
- **Errors:** `ComputerUseSDKError`, `TaskExecutionError`, `BrowserError`, `ValidationError`, `AuthenticationError`, `TaskTimeoutError`, `RateLimitError`, `NetworkError`, `ServiceUnavailableError`, `RetryExhaustedError`, `SessionError`, `APIError`

### Reliability Exports
- **Error:** `ErrorCategory`, `ClassifiedError`, `classify_error`, `classify_error_message`
- **Retry:** `RetryDecision`, `should_retry_task`, `RETRIABLE_CATEGORIES`, `MAX_DELAY_SECONDS`
- **Stuck:** `StuckDetector`, `StuckSignal`
- **Cost:** `calculate_cost_cents`, `calculate_cost_from_steps`, `COST_PER_M_INPUT`, `COST_PER_M_OUTPUT`

### v0.2.0 Exports
- **Budget:** `BudgetMonitor`, `BudgetExceededError`
- **Verification:** `ActionVerifier`, `VerificationResult`
- **Enrichment:** `extract_selectors`, `infer_intent_from_step`
- **Compilation:** `WorkflowCompiler`, `CompilationError`
- **Replay:** `ReplayExecutor`, `ReplayConfig`, `ReplayResult`, `ReplayStepError`
- **Adaptive Retry:** `FailureAnalyzer`, `FailureCategory`, `FailureDiagnosis`, `RecoveryRouter`, `RecoveryPlan`, `RetryMemory`, `AttemptRecord`

### Observability Exports
- **Replay:** `ReplayGenerator`
- **Tracking:** `track`, `TrackedPage`, `TrackConfig`
- **Tracker:** `PokantTracker`, `TrackerConfig`, `create_tracker`
- **Wrapper:** `wrap`, `WrappedAgent`, `WrapConfig`
- **Stagehand:** `observe_stagehand`, `TrackedStagehand`, `StagehandConfig`
- **Alerts:** `AlertConfig`, `AlertEmitter`
- **Analysis:** `AnalysisConfig`, `AnalysisFinding`, `RuleAnalyzer`, `HistoryAnalyzer`, `LLMAnalyzer`, `RunAnalysis`, `RunAnalyzer`
- **Desktop:** `pyautogui_screenshot_fn`, `pillow_screenshot_fn`, `mss_screenshot_fn`

### ActionType Enum (34 actions)
Expanded from 18 → 34: includes browser actions, desktop actions, observe actions, and API actions.

## Dashboard Pages

| Route | Purpose |
|-------|---------|
| `/overview` | Fleet health: success rate, latency, active tasks |
| `/tasks` | Paginated task table with status badges |
| `/tasks/[id]` | Task detail: replay viewer, step timeline, analysis panel, workflow panel |
| `/tasks/new` | Manual task creation form |
| `/health` | Health analytics: score card, error breakdown, executor comparison, failure hotspots, hourly activity, retry stats |
| `/usage` | Cost/token charts, expensive tasks table |
| `/sessions` | Browser session management (drawer) |
| `/scripts` | Compiled workflow script viewer |
| `/settings` | API keys CRUD, billing |
| `/login` | Authentication |

## Environment Variables

Copy `.env.example` to `.env`. `make dev` auto-creates it if missing.

| Variable | Required | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `DATABASE_URL` | Yes (server) | `postgresql+asyncpg://...` |
| `REDIS_URL` | Yes (server) | Redis connection string |
| `BROWSERBASE_API_KEY` | Cloud only | Browserbase cloud browsers |
| `BROWSERBASE_PROJECT_ID` | Cloud only | Browserbase project |
| `SUPABASE_URL` | Optional | Supabase project URL |
| `SUPABASE_KEY` | Optional | Supabase anon/service key |
| `R2_ACCESS_KEY` | Replays | Cloudflare R2 |
| `R2_SECRET_KEY` | Replays | Cloudflare R2 |
| `R2_ENDPOINT` | Replays | Cloudflare R2 |
| `R2_BUCKET_NAME` | Replays | Default: `pokant-replays` |
| `STRIPE_SECRET_KEY` | Billing | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | Billing | Stripe webhook signing |
| `API_SECRET_KEY` | Security | Random secret for internal signing |
| `ENCRYPTION_MASTER_KEY` | Security | 32-byte hex key for credential encryption |
| `TWOCAPTCHA_API_KEY` | Optional | Captcha solving |
| `CANARY_DEPLOYMENT` | Optional | Set `true` for canary metrics |
| `POKANT_API_URL` | Optional | SDK → dashboard reporting (http://localhost:8000) |
| `POKANT_API_KEY` | Optional | SDK → dashboard reporting API key |
| `NEXT_PUBLIC_API_URL` | Dashboard | Browser-facing API (http://localhost:8000) |
| `INTERNAL_API_URL` | Dashboard | Server-side API (http://api:8000 in Docker) |
| `DEFAULT_MODEL` | Optional | SDK default model (claude-sonnet-4-5) |
| `DEFAULT_TIMEOUT` | Optional | SDK default timeout (300) |
| `DEFAULT_MAX_STEPS` | Optional | SDK default max steps (50) |
| `SESSION_DIR` | Optional | SDK session dir (./sessions) |
| `REPLAY_DIR` | Optional | SDK replay dir (./replays) |

## Testing Architecture

### Backend (`pytest`)
- `asyncio_mode = "auto"` in `pyproject.toml` — async tests run without `@pytest.mark.asyncio`
- `tests/conftest.py` stubs heavy deps: `anthropic`, `browser_use`, `browser_use.llm.anthropic.chat`, `browser_use.browser.session`, `playwright`, `langchain_anthropic`, `aiohttp`, `boto3`, `botocore`, `psycopg2`, `rich`
- Sets env var defaults so `api.config.Settings` loads without `.env`
- **687 unit tests** (`tests/unit/`) — no external services
- **521 SDK tests** (`sdk/tests/`) — includes action_verifier, adaptive_retry, budget, compiler, failure_analyzer, recovery_router, replay_executor, retry_memory, step_enrichment, wrap, track
- Integration tests (`tests/integration/`) — require live Postgres, Redis
- E2E tests (`tests/e2e/` + `e2e_tests/`) — full stack
- Load tests (`tests/load/`) — Locust

### Frontend (Playwright)
- Config: `dashboard/playwright.config.ts`
- Run: `cd dashboard && npx playwright test`

### Linting & Types
- **ruff**: `target-version = "py311"`, `line-length = 120`
- **black**: same config
- **mypy**: `python_version = "3.11"`, `ignore_missing_imports = true`
- **ESLint**: Next.js config

## Docker Services (`docker-compose.yml`)

| Service | Image/Build | Port | Purpose |
|---------|-------------|------|---------|
| `postgres` | postgres:16 | 5432 | Database |
| `redis` | redis:7 | 6379 | Broker + cache |
| `migrate` | postgres:16 | — | Run SQL migrations on startup |
| `api` | Dockerfile.api | 8000 | FastAPI + uvicorn (hot reload) |
| `worker` | Dockerfile.worker | — | Celery worker |
| `dashboard` | node:20-alpine | 3000 | Next.js dev server |

Volumes: `pgdata`, `dashboard_node_modules`, `dashboard_next`, `replays`

## Key Files Quick Reference

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI app, middleware stack, health + metrics |
| `api/config.py` | `Settings` (pydantic-settings); all env vars |
| `api/middleware/auth.py` | `get_current_account`; SHA-256 key hashing + RLS |
| `api/routes/tasks.py` | Task CRUD + ingest + retry + replay |
| `api/routes/analytics.py` | Fleet health analytics endpoint |
| `api/routes/alerts.py` | Alert list + acknowledge |
| `api/services/r2.py` | R2 presigned URL generation |
| `workers/executor.py` | Core TaskExecutor: browser + LLM loop + screenshots |
| `workers/browser_manager.py` | Playwright lifecycle + stealth |
| `workers/stuck_detector.py` | Stuck pattern detection |
| `workers/error_classifier.py` | Error classification |
| `workers/encryption.py` | AES-256-GCM + HKDF |
| `sdk/computeruse/client.py` | ComputerUse class (local + cloud) |
| `sdk/computeruse/analyzer.py` | 3-tier failure analysis |
| `sdk/computeruse/failure_analyzer.py` | Deep failure diagnosis |
| `sdk/computeruse/recovery_router.py` | Recovery routing |
| `sdk/computeruse/retry_memory.py` | Cross-run retry memory |
| `sdk/computeruse/action_verifier.py` | Post-action verification |
| `sdk/computeruse/budget.py` | Per-run budget monitoring |
| `sdk/computeruse/compiler.py` | Workflow compilation |
| `sdk/computeruse/replay_executor.py` | Deterministic workflow replay |
| `sdk/computeruse/selector_healer.py` | Auto-heal CSS selectors |
| `sdk/computeruse/step_enrichment.py` | Intent extraction + selectors |
| `sdk/computeruse/alerts.py` | AlertConfig + AlertEmitter |
| `sdk/computeruse/tracker.py` | PokantTracker |
| `sdk/computeruse/wrap.py` | Agent wrapping + enrichment reporting |
| `sdk/computeruse/replay_generator.py` | HTML replay generation |
| `shared/constants.py` | TIER_LIMITS, TaskStatus, ErrorCode |
| `shared/errors.py` | Exception hierarchy |
| `shared/url_validator.py` | SSRF-safe URL validation |
| `tests/conftest.py` | Stubs heavy deps; sets env defaults |

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update tasks/lessons.md with the pattern
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- Skip this for simple, obvious fixes — don't over-engineer

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Point at logs, errors, failing tests — then resolve them

## Task Management
1. Plan First: Write plan to tasks/todo.md with checkable items
2. Verify Plan: Check in before starting implementation
3. Track Progress: Mark items complete as you go
4. Explain Changes: High-level summary at each step
5. Document Results: Add review section to tasks/todo.md
6. Capture Lessons: Update tasks/lessons.md after corrections

## Core Principles
- Simplicity First: Make every change as simple as possible. Impact minimal code.
- No Laziness: Find root causes. No temporary fixes. Senior developer standards.
- Minimal Impact: Changes should only touch what's necessary. Avoid introducing bugs.
