# Pokant (ComputerUse.dev)

## Project Overview
**One API to automate any browser workflow.**
Managed orchestration platform for browser-based AI automation.
Monorepo: Python backend (FastAPI + Celery) + TypeScript frontend (Next.js 15).

**Version:** 0.3.0
**Python:** 3.11+
**Node:** 20+

## Tech Stack

### Backend
- **API:** FastAPI, uvicorn, structlog (JSON prod / console dev), Prometheus
- **Queue:** Celery + Redis (Upstash), tier-based queues (`tasks:free` / `tasks:startup` / `tasks:enterprise`)
- **Database:** PostgreSQL 16 (Supabase), SQLAlchemy async, UUIDv7 PKs, Row-Level Security
- **Browser:** Playwright + Browserbase (cloud CDP sessions)
- **LLM:** Anthropic Claude API (`claude-sonnet-4-6` default)
- **Auth:** Email+password (PBKDF2-SHA256, 600k iterations) + API key (SHA-256 hashed)
- **Storage:** Cloudflare R2 / AWS S3 / Supabase Storage (replays, screenshots, HAR, traces, videos)
- **Payments:** Stripe (subscriptions + usage metering)
- **Credentials:** AES-256-GCM with HKDF-derived per-account keys
- **Deployment:** Railway (API, Worker, Cron, Dashboard) + Vercel (dashboard)

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
│   ├── engine.py        SQLAlchemy async engine + session factory (SSL/TLS)
│   ├── migrations/      16 SQL migration files
│   └── init-migrations.sh
├── middleware/
│   ├── auth.py          Dual auth: API key (X-API-Key / Bearer) + email/password
│   ├── rate_limiter.py  Sliding window (Redis)
│   ├── logging.py       StructuredLoggingMiddleware
│   ├── metrics.py       PrometheusMiddleware
│   └── credential_scrubber.py  Strips secrets from logs
├── models/              SQLAlchemy ORM
│   ├── account.py       Account (tier, usage)
│   ├── task.py          Task (status, result, cost, failure_counts JSONB)
│   ├── task_step.py     TaskStep (action, screenshot, failure_class, patch_applied, artifacts)
│   ├── api_key.py       ApiKey (SHA-256 hash)
│   ├── session.py       Session (encrypted cookies)
│   ├── alert.py         Alert (failure/cost alerts)
│   └── audit_log.py     AuditLog (immutable, RLS)
├── routes/
│   ├── tasks.py         POST/GET/DELETE tasks, ingest, retry, replay
│   ├── auth.py          POST /auth/register + /auth/login
│   ├── analytics.py     Fleet health + reliability analytics
│   ├── alerts.py        Alert list + acknowledge
│   ├── sessions.py      Browser session CRUD
│   ├── billing.py       Stripe subscription + checkout
│   ├── account.py       API key CRUD
│   ├── audit.py         Audit log queries
│   └── local_files.py   Dev-mode local file serving
├── schemas/             Pydantic v2 request/response models
├── services/
│   ├── audit_logger.py  Append-only audit log
│   ├── usage_tracker.py Atomic step billing metering
│   └── r2.py            R2 presigned URL generation
└── management/
    └── reset_usage.py   Reset monthly_steps_used

/workers                 Celery task workers
├── main.py              Celery app config, tier-based queue routing
├── tasks.py             execute_task + deliver_webhook Celery tasks
├── executor.py          TaskExecutor: PAV loop orchestration entry point
├── browser_manager.py   Playwright lifecycle, stealth scripts injection
├── models.py            ActionType, StepData, TaskConfig, TaskResult
├── config.py            WorkerSettings (pydantic-settings)
├── healthcheck.py       HTTP health server on port 8001
├── db.py                Sync + async session factory (SSL/TLS)
├── encryption.py        AES-256-GCM + HKDF key derivation
├── credential_injector.py  Login form credential injection
├── captcha_solver.py    2Captcha integration
├── session_manager.py   Encrypted cookie persistence
├── replay.py            Replay HTML generation + R2 upload
├── stuck_detector.py    Visual stagnation, action repetition, failure spirals
├── error_classifier.py  Legacy error classification (transient vs permanent)
├── retry.py             retry_with_backoff async helper
├── output_validator.py  Schema validation + type coercion
├── canary.py            Canary deployment metrics
├── metrics.py           Prometheus metrics (Celery signals)
├── shutdown.py          Graceful SIGTERM handler
├── stealth/             7 browser fingerprint-evasion JS scripts
├── templates/           Replay HTML template
│
├── backends/            Backend abstraction layer
│   ├── protocol.py      CUABackend Protocol + BackendCapabilities
│   ├── registry.py      backend_for_task() factory
│   ├── _browser_use.py  BrowserUseBackend (goal delegation via browser-use Agent)
│   ├── native_anthropic.py  NativeAnthropicBackend (pixel-level computer_use)
│   └── skyvern.py       SkyvernBackend (cloud API delegation)
│
├── pav/                 Plan-Act-Validate orchestration
│   ├── loop.py          run_pav_loop: decompose → execute → validate → repair/replan
│   ├── planner.py       LLM-powered plan decomposition + replan
│   ├── validator.py     Two-phase validation (deterministic + LLM)
│   └── types.py         PlanState, SubGoal dataclasses
│
├── reliability/         Self-healing repair layer
│   ├── circuit_breaker.py  Per-group consecutive failure tracking
│   ├── detectors.py     classify_outcome: check_name → regex → UNKNOWN
│   ├── playbooks.py     RepairStrategy enum + static playbook table
│   └── repair_loop.py   run_repair: classify → circuit-break → playbook → execute
│
├── shared_types/        Shared type definitions (PAV + reliability)
│   ├── observations.py  Observation (URL, title, screenshot, DOM, errors)
│   ├── actions.py       GroundingRung, StepIntent, StepResult
│   ├── validation.py    ValidatorVerdict, ValidatorOutcome
│   ├── taxonomy.py      FailureClass (22-value enum, 7 groups)
│   └── budget.py        Budget (cost + step + time limits)
│
└── memory/              Persistent memory system
    ├── store.py         MemoryStore: asyncpg CRUD for memory_entries table
    ├── episodic.py      EpisodicMemory: per-tenant failure/fix learning
    └── longterm.py      LongTermMemory: site playbooks + compiled routes

/shared                  Shared Python modules
├── constants.py         TaskStatus, ErrorCode, TIER_LIMITS, TIER_STEP_LIMITS
├── db.py                Shared DB helpers
├── errors.py            Exception hierarchy (ComputerUseError subclasses)
├── storage.py           Async S3/Supabase storage (replays, screenshots, HAR, traces, video)
├── url_validator.py     SSRF-safe URL validation (blocks private IPs)
└── browser_provider.py  Browser factory

/sdk                     Python SDK package (v0.2.0)
└── computeruse/         (see SDK section below)

/dashboard               Next.js 15 frontend
├── vercel.json          Vercel deployment config
├── src/app/
│   ├── (auth)/login     Login page
│   ├── contact/         Enterprise contact form (Formspree)
│   └── (dashboard)/
│       ├── overview/    Fleet health overview
│       ├── tasks/       Task list + [id] detail + new
│       ├── health/      Health analytics metrics
│       ├── usage/       Cost/token analytics
│       ├── sessions/    Browser session management
│       ├── scripts/     Compiled workflow script viewer
│       └── settings/    API keys, billing
├── src/components/
│   ├── ui/              shadcn/ui primitives
│   ├── health/          Health dashboard (8 sub-components)
│   ├── layout/          Sidebar, header, auth guard
│   ├── task-table.tsx   Paginated task list
│   ├── step-timeline.tsx  Step detail with screenshots + validator verdicts
│   ├── reliability-section.tsx  Failure distribution, repair effectiveness, top domains
│   ├── repair-activity.tsx  Per-task repair history timeline
│   ├── replay-viewer.tsx  Replay playback
│   ├── analysis-panel.tsx  3-tier analysis results
│   ├── alert-bell.tsx   Unread alerts notification
│   ├── session-drawer.tsx  Session management sheet
│   ├── retry-chain.tsx  Retry attempt visualization
│   ├── workflow-panel.tsx  Compiled workflow viewer
│   ├── error-chart.tsx / cost-chart.tsx / usage-chart.tsx
│   ├── executor-comparison.tsx  browser_use vs native stats
│   ├── expensive-tasks-table.tsx  Top tasks by cost
│   └── status-badge.tsx / json-viewer.tsx / confirm-dialog.tsx
├── src/contexts/        Auth context, theme context
├── src/hooks/           Custom React hooks
├── src/lib/
│   ├── api-client.ts    API client (tasks, analytics, reliability, workflows)
│   ├── types.ts         TypeScript types (Task, Step, ReliabilityAnalytics)
│   └── workflow-utils.ts  Workflow display helpers
└── e2e/                 Playwright e2e tests

/tests                   Backend test suite
├── conftest.py          Stubs heavy deps, sets env defaults
├── unit/
│   ├── conftest.py      Stubs MemoryStore.init() for unit tests
│   ├── test_reliability.py  Circuit breaker + detectors + playbooks + repair loop
│   ├── test_circuit_breaker.py  Focused circuit breaker tests
│   ├── test_detectors.py  Focused failure detection tests
│   ├── test_playbooks.py  Focused playbook tests
│   ├── test_repair_loop.py  Focused repair loop tests
│   ├── test_memory.py   Memory store + episodic + longterm tests
│   ├── test_storage_artifacts.py  S3/Supabase upload tests
│   ├── test_pav.py      PAV loop, planner, validator tests
│   ├── test_executor.py  TaskExecutor + replay generation tests
│   ├── test_backends_protocol.py  CUABackend protocol conformance
│   ├── test_browser_use_backend.py  BrowserUseBackend tests
│   ├── test_shared_types.py  Observation, StepResult, Budget, FailureClass tests
│   └── ... (20+ more test files)
├── integration/         Live service tests (Postgres, Redis)
├── e2e/                 End-to-end tests
└── load/                Locust load testing

/infra                   Docker & deployment
├── Dockerfile.api / Dockerfile.worker / Dockerfile.dashboard
├── docker-compose.prod.yml
├── railway.toml / railway-worker.toml / railway-dashboard.toml / railway-cron.toml
├── worker-entrypoint.sh
└── DEPLOY.md            Railway deployment guide

/scripts                 Dev scripts
├── setup.sh
├── worker_health.py
├── verify_production.py  12-check post-deploy verification
└── test_cloud_pipeline.py  19-check end-to-end pipeline test
```

## Commands

```bash
# === Backend ===
pytest tests/unit -x -v                    # Unit tests (no external services)
pytest tests/unit/test_reliability.py -v   # Single test file
cd sdk && pytest tests/ -x -v              # SDK tests
ruff check api/ workers/ sdk/ shared/      # Lint
mypy api/ workers/                         # Type check
uvicorn api.main:app --reload --port 8000  # Run API locally
celery -A workers.main worker --loglevel=info --pool=prefork --concurrency=1

# === Frontend ===
cd dashboard && npm run dev       # Dev server (port 3000)
cd dashboard && npm run lint      # ESLint
cd dashboard && npx tsc --noEmit  # Type check
cd dashboard && npx playwright test  # E2E tests

# === Docker ===
make dev       # docker compose up --build
make fresh     # tear down volumes + rebuild
make migrate   # run DB migrations
make logs      # follow all logs
```

## API Routes

### Tasks — `/api/v1/tasks`
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/tasks` | Create task (queued to tier-based Redis queue) |
| POST | `/api/v1/tasks/ingest` | Ingest SDK-completed task with screenshots |
| GET | `/api/v1/tasks` | List tasks (paginated) |
| GET | `/api/v1/tasks/{id}` | Get task by ID (incl. failure_counts, dominant_failure, repair_count) |
| DELETE | `/api/v1/tasks/{id}` | Cancel task |
| POST | `/api/v1/tasks/{id}/retry` | Retry failed task |
| GET | `/api/v1/tasks/{id}/steps` | Get steps (incl. failure_class, patch_applied, validator_verdict) |
| GET | `/api/v1/tasks/{id}/replay` | Get signed replay URL |

### Analytics — `/api/v1/analytics`
| GET | `/api/v1/analytics/health` | Fleet health aggregates (period: 1h/6h/24h/7d/30d) |
| GET | `/api/v1/analytics/reliability` | Failure distribution, repair effectiveness, circuit breaker trips (period: 1h–90d) |

### Auth — `/auth`
| POST | `/auth/register` | Register with email + password (returns API key) |
| POST | `/auth/login` | Login with email + password (returns API key) |

### Other
| GET/POST/DELETE | `/api/v1/sessions[/{id}]` | Browser session CRUD |
| GET/POST/DELETE | `/api/v1/account/keys[/{id}]` | API key CRUD |
| GET | `/api/v1/alerts` | List alerts |
| POST | `/api/v1/alerts/{id}/ack` | Acknowledge alert |
| GET | `/api/v1/billing/subscription` | Get subscription |
| POST | `/api/v1/billing/checkout` | Stripe checkout session |
| GET | `/api/v1/audit` | Query audit logs |
| GET | `/health` | Health check (DB + Redis) |
| GET | `/metrics` | Prometheus metrics |

## Architecture & Data Flow

### PAV Execution Pipeline
```
Client → POST /api/v1/tasks (X-API-Key header)
  ├── auth.py: SHA-256 hash → api_keys lookup → SET LOCAL app.account_id (RLS)
  ├── rate_limiter.py: sliding window (Redis)
  ├── url_validator.py: SSRF protection
  ├── tasks.py: check concurrent limit → insert Task → enqueue Celery
  └── return 202 Accepted

workers/tasks.py → execute_task (Celery)
  ├── Atomic claim (UPDATE WHERE status='queued') + Redis lock
  ├── workers/executor.py: TaskExecutor.execute()
  │   ├── backends/registry.py → select backend (browser_use|native|skyvern)
  │   ├── pav/planner.py → decompose task into SubGoals
  │   ├── pav/loop.py → for each SubGoal:
  │   │   ├── backend.execute_goal() or backend.execute_step()
  │   │   ├── pav/validator.py → deterministic checks + LLM judgment
  │   │   ├── Stamp validator_verdict on step side_effects
  │   │   ├── On FAIL → reliability/repair_loop.py:
  │   │   │   ├── detectors.py → classify_outcome → FailureClass
  │   │   │   ├── circuit_breaker.py → allow_attempt(group)?
  │   │   │   ├── playbooks.py → get repair action (wait/scroll/refresh/dismiss)
  │   │   │   ├── Execute repair action via backend
  │   │   │   ├── memory/episodic.py → record outcome for learning
  │   │   │   └── If repair fails → planner.replan()
  │   │   └── Stamp failure_class + patch_applied on step
  │   └── Return TaskResult with all steps
  ├── Persist task + steps + failure_counts to Postgres
  ├── Upload replay HTML to R2/S3
  ├── Increment account.monthly_steps_used
  └── deliver_webhook (HMAC-SHA256 signed POST)
```

### Executor Modes
| Mode | Backend | How it works |
|------|---------|-------------|
| `browser_use` | BrowserUseBackend | DOM-based agent (browser-use Agent library), goal delegation |
| `native` | NativeAnthropicBackend | Pixel-based (Claude `computer_use` tool), fine-grained steps |
| `skyvern` | SkyvernBackend | Cloud API delegation to Skyvern service |

### CUABackend Protocol
All backends implement: `initialize()`, `execute_step()`, `execute_goal()`, `get_observation()`, `teardown()`.
`BackendCapabilities` declares: `supports_single_step`, `supports_goal_delegation`, `supports_screenshots`, `supports_har`, etc.

### Plan-Act-Validate (PAV) Loop
1. **Plan** — LLM decomposes task into SubGoals with success criteria
2. **Act** — Backend executes subgoal (delegated or fine-grained)
3. **Validate** — Two-phase: deterministic checks (auth redirect, error pages, URL patterns) + LLM judgment
4. **Repair** — On failure: classify → circuit-break → playbook action → retry or replan

### Self-Healing Repair System
- **FailureClass** (`shared_types/taxonomy.py`): 22-value enum in 7 groups (llm, browser, network, anti_bot, auth, agent, unknown)
- **CircuitBreaker**: Tracks failures per group (`max_consecutive=3`), per class (`max_same_class=3`), and total (`max_total_failures=10`)
- **classify_outcome()**: check_name fast-path → 22 regex patterns → UNKNOWN fallback
- **RepairStrategy**: `WAIT_AND_RETRY`, `REFRESH_PAGE`, `SCROLL_AND_RETRY`, `DISMISS_OVERLAY`, `RE_NAVIGATE`, `REPLAN`, `ABORT`
- **Playbook mappings**: e.g., `BROWSER_ELEMENT_MISSING → [scroll, wait]`, `LLM_OVERLOADED → [wait 10s]`, `BROWSER_CRASH → [abort]`
- **EpisodicMemory**: Records fix attempts per tenant per domain, surfaces best-known repairs

### Memory System
- **MemoryStore** (`workers/memory/store.py`): asyncpg CRUD over `memory_entries` table
- **EpisodicMemory**: Per-tenant per-run failure/fix tracking. Key format: `fix:{domain}:{failure_class}:{repair_action}`
- **LongTermMemory**: Per-tenant site playbooks and compiled routes. Keys: `site:{domain}:playbook`, `route:{domain}:{workflow_type}`

### Shared Types (Import Chain)
```
shared_types/  → nothing from workers/  (leaf dependency)
reliability/   → shared_types/ only     (TYPE_CHECKING for backends/pav)
pav/           → shared_types/, backends/protocol
executor.py    → all of the above       (lazy imports inside execute())
tasks.py       → executor.py            (no direct reliability imports)
```

### Authentication
- **API keys:** SHA-256 hashed in `api_keys` table (raw key never stored)
- **Email+password:** PBKDF2-SHA256 (600k iterations), `password_hash` on `accounts`
- **Dual header:** `X-API-Key` and `Authorization: Bearer` both accepted
- `SET LOCAL app.account_id` on each DB connection for Postgres RLS
- Registration: `POST /auth/register` → creates account + API key, returns `cu_live_*` key

### Tier System
Defined in `shared/constants.py`. Queues: `tasks:free`, `tasks:startup`, `tasks:enterprise`.

| Tier | Concurrent | Max Steps | Timeout | Monthly Steps |
|------|-----------|-----------|---------|---------------|
| free | 1 | 50 | 600s | 500 |
| startup | 5 | 200 | 300s | 5,000 |
| growth | 10 | 350 | 450s | 25,000 |
| enterprise | 20 | 500 | 600s | 100,000 |

**Worker concurrency:** Set to `1` in `workers/main.py` (`worker_concurrency=1`) to prevent 429 rate limits on the Anthropic API — screenshot-heavy `computer_use` requests can't run concurrently on a single API key. Infra TOML/Dockerfile still pass `--concurrency=2` but the in-code setting wins.

### Storage
- **R2/S3** (`shared/storage.py`): Replays (HTML/JSON), screenshots (PNG per step)
- **Supabase Storage**: HAR files (gzip compressed), Playwright traces (ZIP), video recordings (WebM)
- **Artifact refs on task_steps**: `har_ref`, `trace_ref`, `video_ref` (URLs to Supabase)
- Graceful fallback to local filesystem when unconfigured

## Database Schema (PostgreSQL 16)

**UUIDv7** primary keys. **Row-Level Security** for account isolation.

| Table | Purpose |
|-------|---------|
| `accounts` | User accounts (tier, usage, webhook_secret, email, password_hash) |
| `api_keys` | Hashed API keys (SHA-256) |
| `tasks` | Task records (status, result, cost, executor_mode, failure_counts JSONB) |
| `task_steps` | Steps (action, screenshot, failure_class, patch_applied, validator_verdict, har/trace/video refs) |
| `sessions` | Browser sessions (encrypted cookies) |
| `audit_logs` | Immutable audit trail (RLS enforced) |
| `alerts` | Alert records (failure/cost alerts, acknowledgement) |
| `memory_entries` | Persistent memory (scope, scope_id, key, content JSONB, provenance) |

### Migrations (`api/db/migrations/`)
1–13: Initial schema, seed data, RLS policies, retry columns, webhook_secret, executor_mode, alerts, analytics indexes, context JSONB, analysis_json, compiled_workflow, playwright_script, password_hash
14: *(reserved)*
15: `015_failure_and_artifacts.sql` — failure_class, patch_applied, validator_verdict on task_steps; har_ref, trace_ref, video_ref; failure_counts JSONB on tasks; index on failure_class
16: `016_memory_system.sql` — memory_entries table (scope, scope_id, key, content, provenance, safety_label, timestamps; unique constraint + prefix index)

## Dashboard Pages

| Route | Purpose |
|-------|---------|
| `/overview` | Fleet health: success rate, latency, active tasks, reliability section |
| `/tasks` | Paginated task list with status badges, repair indicators |
| `/tasks/[id]` | Task detail: replay, step timeline (with validator verdicts + repair badges), analysis, repair activity |
| `/tasks/new` | Task creation form (URL, task, schema, executor mode, Skyvern config) |
| `/health` | Health analytics: score card, error breakdown, executor comparison, hourly activity |
| `/usage` | Cost/token charts, expensive tasks table |
| `/sessions` | Browser session management |
| `/scripts` | Compiled workflow script viewer |
| `/settings` | API keys CRUD, billing |

### New Dashboard Components
- **ReliabilitySection** — Success rate card, repair effectiveness chart, failure distribution bars, circuit breaker trips, top failing domains
- **RepairActivity** — Per-task repair timeline: step → failure_class → action → success/fail; circuit breaker trip warnings
- **StepTimeline** — Enhanced with validator verdict badges, failure class pills, repair action indicators

## Environment Variables

| Variable | Required | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `DATABASE_URL` | Yes (server) | `postgresql+asyncpg://...` |
| `REDIS_URL` | Yes (server) | Redis connection string |
| `BROWSERBASE_API_KEY` | Cloud only | Browserbase cloud browsers |
| `BROWSERBASE_PROJECT_ID` | Cloud only | Browserbase project |
| `SUPABASE_URL` | Optional | Supabase project URL (storage + DB) |
| `SUPABASE_KEY` | Optional | Supabase anon/service key |
| `AWS_ACCESS_KEY_ID` | Replays | S3/R2 access key |
| `AWS_SECRET_ACCESS_KEY` | Replays | S3/R2 secret key |
| `AWS_BUCKET_NAME` | Replays | Default: `computeruse-replays` |
| `AWS_REGION` | Replays | Default: `us-east-1` |
| `AWS_CDN_BASE_URL` | Optional | CDN URL prefix for S3 objects |
| `R2_ACCESS_KEY` / `R2_SECRET_KEY` / `R2_ENDPOINT` / `R2_BUCKET_NAME` | Replays (alt) | Cloudflare R2 |
| `STRIPE_SECRET_KEY` | Billing | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | Billing | Stripe webhook signing |
| `API_SECRET_KEY` | Security | Random secret for internal signing |
| `ENCRYPTION_MASTER_KEY` | Security | 32-byte hex key for credential encryption |
| `TWOCAPTCHA_API_KEY` | Optional | Captcha solving |
| `POKANT_API_URL` | Optional | SDK → dashboard reporting |
| `POKANT_API_KEY` | Optional | SDK → dashboard API key |
| `NEXT_PUBLIC_API_URL` | Dashboard | Browser-facing API URL |
| `INTERNAL_API_URL` | Dashboard | Server-side API URL (Docker) |

## Testing

### Backend (`pytest`)
- `asyncio_mode = "auto"` in `pyproject.toml` — async tests run without decorator
- `tests/conftest.py` stubs: `anthropic`, `browser_use`, `playwright`, `langchain_anthropic`, `aiohttp`, `boto3`, `botocore`, `psycopg2`, `rich`, `asyncpg`
- `tests/unit/conftest.py` stubs `MemoryStore.init()` to prevent real asyncpg connections
- Unit tests (`tests/unit/`) — no external services
- Integration tests (`tests/integration/`) — require live Postgres, Redis
- E2E tests — full stack
- Load tests (`tests/load/`) — Locust

### Frontend (Playwright)
- Config: `dashboard/playwright.config.ts`
- Run: `cd dashboard && npx playwright test`

### Linting & Types
- **ruff**: `target-version = "py311"`, `line-length = 120`
- **mypy**: `python_version = "3.11"`, `ignore_missing_imports = true`
- **ESLint**: Next.js config

## Docker Services

| Service | Image/Build | Port | Purpose |
|---------|-------------|------|---------|
| `postgres` | postgres:16 | 5432 | Database |
| `redis` | redis:7 | 6379 | Broker + cache |
| `migrate` | postgres:16 | — | Run SQL migrations on startup |
| `api` | Dockerfile.api | 8000 | FastAPI + uvicorn (hot reload) |
| `worker` | Dockerfile.worker | — | Celery worker |
| `dashboard` | node:20-alpine | 3000 | Next.js dev server |

## Deployment

### Production Stack
- **API + Worker + Cron:** Railway (per-service TOML configs in `infra/`)
- **Dashboard:** Vercel (Next.js, `dashboard/vercel.json`)
- **Database:** Supabase PostgreSQL 16 (Session mode pooler, port 5432)
- **Redis:** Upstash Redis
- **Browsers:** Browserbase (cloud CDP sessions)

### SSL/TLS for Remote Postgres
- `api/db/engine.py` and `workers/db.py` auto-detect remote connections
- Adds `sslmode=require` (hostname verification disabled for Supabase self-signed certs)
- Supabase requires Session mode pooler (port 5432), NOT Transaction mode (6543)

## Key Files Quick Reference

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI app, middleware stack, health + metrics |
| `api/config.py` | `Settings` (pydantic-settings); all env vars |
| `api/middleware/auth.py` | Dual auth (X-API-Key + Bearer); SHA-256 + RLS |
| `api/routes/tasks.py` | Task CRUD + ingest + repair_count/dominant_failure |
| `api/routes/analytics.py` | /health + /reliability analytics endpoints |
| `workers/executor.py` | TaskExecutor entry point → PAV loop |
| `workers/tasks.py` | execute_task Celery task (claim + run + persist) |
| `workers/backends/protocol.py` | CUABackend Protocol definition |
| `workers/backends/_browser_use.py` | BrowserUseBackend (cached AsyncAnthropic client) |
| `workers/backends/registry.py` | backend_for_task() factory |
| `workers/pav/loop.py` | run_pav_loop: plan → act → validate → repair |
| `workers/pav/planner.py` | LLM-powered plan decomposition + replan |
| `workers/pav/validator.py` | Two-phase validation (deterministic + LLM) |
| `workers/reliability/repair_loop.py` | run_repair: classify → CB → playbook → execute |
| `workers/reliability/circuit_breaker.py` | Per-group/class/total failure tracking |
| `workers/reliability/detectors.py` | classify_outcome + detect_failure |
| `workers/reliability/playbooks.py` | RepairStrategy enum + static playbook table |
| `workers/shared_types/taxonomy.py` | FailureClass (22 values, 7 groups) |
| `workers/memory/store.py` | MemoryStore: asyncpg CRUD |
| `workers/memory/episodic.py` | EpisodicMemory: failure/fix learning |
| `shared/storage.py` | Async S3/Supabase storage operations |
| `shared/constants.py` | TIER_LIMITS, TaskStatus, ErrorCode |
| `tests/conftest.py` | Stubs heavy deps; sets env defaults |

## Gotchas & Rate Limiting

Accumulated learnings from production debugging (see recent commits):

- **Cached `AsyncAnthropic` client** (`workers/backends/_browser_use.py`): browser-use 0.11.x creates a fresh `AsyncAnthropic` on every LLM call via `get_client()`, preventing TCP connection pooling. Anthropic's load balancer then returns 529 (overloaded) under moderate load. We monkey-patch `get_client()` to return a single cached instance.
- **Fresh `BrowserSession` per `execute_goal()`**: browser-use fires `BrowserStopEvent` on agent completion which calls `reset(force=True)` and destroys the CDP connection. Subsequent `execute_goal()` calls fail with `CDP client not initialized`. Fix: create a new `BrowserSession` per goal.
- **1s rate-limit guard between PAV iterations** (`workers/pav/loop.py`): each loop fires 2–3 rapid LLM calls (planner + backend + validator). Without spacing, the next burst triggers 429s with 15–34s retry delays. A 1s pause between iterations prevents most 429s and is faster net.
- **Worker concurrency = 1**: Two concurrent workers would both issue screenshot-heavy `computer_use` requests and hit 429s on a single API key. Celery's `worker_concurrency=1` is set in code (`workers/main.py`); infra command-line `--concurrency=2` is legacy and overridden.
- **browser-use DOM mode default**: `use_vision=False` is the default to reduce token cost and screenshot payload; set back to `True` for vision-critical tasks.
- **Start URL injected into task string**: browser-use's Agent doesn't auto-navigate; we prepend `Navigate to {url}. ` to the task string and also call `page.goto(url)` explicitly.
- **Free tier timeout raised to 600s**: Free-tier PAV runs regularly exceeded the old 120s limit (plan + 3–5 steps + validate + repair). Bumped to 600s to match enterprise.

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
- `ErrorCategory`, `ClassifiedError`, `classify_error`, `classify_error_message`
- `RetryDecision`, `should_retry_task`, `RETRIABLE_CATEGORIES`, `MAX_DELAY_SECONDS`
- `StuckDetector`, `StuckSignal`
- `BudgetMonitor`, `BudgetExceededError`
- `ActionVerifier`, `VerificationResult`
- `FailureAnalyzer`, `FailureCategory`, `FailureDiagnosis`
- `RecoveryRouter`, `RecoveryPlan`
- `RetryMemory`, `AttemptRecord`

### Observability Exports
- `ReplayGenerator`, `track`, `TrackedPage`, `PokantTracker`, `create_tracker`
- `wrap`, `WrappedAgent`, `WrapConfig`
- `AlertConfig`, `AlertEmitter`
- `RuleAnalyzer`, `HistoryAnalyzer`, `LLMAnalyzer`, `RunAnalysis`, `RunAnalyzer`

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- One task per subagent for focused execution

### 3. Verification Before Done
- Never mark a task complete without proving it works
- Run tests, check logs, demonstrate correctness

### 4. Autonomous Bug Fixing
- When given a bug report: just fix it. Point at logs, errors, failing tests — then resolve

## Core Principles
- Simplicity First: Make every change as simple as possible
- No Laziness: Find root causes. No temporary fixes
- Minimal Impact: Changes should only touch what's necessary
