# ComputerUse.dev

## Project Overview
Managed orchestration platform for browser-based AI automation.
Monorepo: Python backend (FastAPI + Celery) + TypeScript frontend (Next.js).

## Tech Stack
- API: FastAPI (Python 3.11+), uvicorn, structlog, Prometheus
- Queue: Celery + Redis (Upstash), tier-based queues (free/startup/enterprise)
- Database: PostgreSQL 16 (Supabase), SQLAlchemy async, UUIDv7 PKs, Row-Level Security
- Browser: Playwright + Browserbase (cloud)
- LLM: Anthropic Claude API (`claude-sonnet-4-5` default)
- Storage: Cloudflare R2 (S3-compatible) for replays/recordings
- Frontend: Next.js 14, TypeScript, Tailwind CSS, shadcn/ui
- Payments: Stripe
- Credentials: AES-256-GCM with HKDF-derived per-account keys
- Testing: pytest + pytest-asyncio (backend), vitest (frontend)

## Directory Structure
```
/api          - FastAPI app (routes, models, middleware, services, migrations)
/workers      - Celery workers (executor, browser_manager, captcha, encryption,
                credential_injector, canary, metrics, models, config, stealth/, templates/)
/sdk          - Python SDK package (pip install computeruse)
/dashboard    - Next.js frontend
/shared       - Constants, DB, storage, browser_provider, url_validator, errors
/tests        - unit/, integration/, e2e/, load/
/infra        - Dockerfiles, railway.toml, docker-compose.prod.yml
/scripts      - Dev scripts (worker_health.py)
/examples     - Standalone runnable SDK scripts
```

## Commands

All commands run from the repo root unless noted.

```bash
# Unit tests (no external services needed)
pytest tests/unit -x -v

# Single test file
pytest tests/unit/test_retry.py -v

# Single test
pytest tests/unit/test_retry.py::TestRetryHandler::test_backoff_delay -v

# Lint (ruff)
ruff check api/ workers/ sdk/ shared/

# Type check
mypy api/ workers/

# Run API locally
uvicorn api.main:app --reload --port 8000

# Run Celery worker locally
celery -A workers.main worker --loglevel=info --pool=prefork --concurrency=2

# Run dashboard
cd dashboard && npm run dev

# Frontend tests
cd dashboard && npm test

# Frontend type check
cd dashboard && npx tsc --noEmit

# Frontend lint
cd dashboard && npm run lint

# Docker (all services)
make dev         # docker compose up --build

# DB migrations
make migrate     # alembic upgrade head

# Load test
make load-test
```

## Architecture & Data Flow

### Request lifecycle (cloud task submission)

```
Client  →  POST /api/v1/tasks  (X-API-Key header)
              │
              ├── auth.py: SHA-256 hash key → api_keys table lookup
              │           SET LOCAL app.account_id (RLS context)
              ├── rate_limiter.py: per-account sliding window (Redis)
              ├── url_validator.py: SSRF protection on task URL + webhook URL
              ├── tasks.py route: check concurrent task limit (TIER_LIMITS)
              │   → enqueue to Redis queue: tasks:free / tasks:startup / tasks:enterprise
              └── return 202 Accepted

workers.main  →  celery -A workers.main worker
              │
              ├── execute_task (Celery task)
              │   ├── workers/executor.py: TaskExecutor.execute()
              │   │   ├── workers/browser_manager.py: Playwright via Browserbase CDP
              │   │   ├── workers/session_manager.py: restore encrypted cookies
              │   │   ├── workers/credential_injector.py: inject creds into login forms
              │   │   ├── browser_use.Agent: drives browser step-by-step
              │   │   ├── workers/captcha_solver.py: 2Captcha integration (if needed)
              │   │   ├── workers/output_validator.py: schema coercion
              │   │   ├── workers/session_manager.py: save encrypted cookies
              │   │   └── workers/replay.py: write replay HTML + upload to R2
              │   └── workers/db.py: persist TaskResult to Postgres
              │
              └── deliver_webhook (Celery task, chained after execute_task)
                  └── HMAC-SHA256 signed POST to webhook_url
```

### Authentication
- API keys stored as SHA-256 hashes in `api_keys` table (raw key never stored)
- Auth sets `SET LOCAL app.account_id` on each DB connection to enforce Postgres RLS
- All routes use `get_current_account` FastAPI dependency from `api/middleware/auth.py`

### Tier system
Defined in `shared/constants.py`. Queues: `tasks:free`, `tasks:startup`, `tasks:enterprise`.

| Tier | Concurrent | Max Steps | Timeout | Monthly Steps |
|------|-----------|-----------|---------|---------------|
| free | 1 | 50 | 120s | 500 |
| startup | 5 | 200 | 300s | 5,000 |
| growth | 10 | 350 | 450s | 25,000 |
| enterprise | 20 | 500 | 600s | 100,000 |

### Credential encryption
`workers/encryption.py`: HKDF derives a per-account AES-256-GCM key from `ENCRYPTION_MASTER_KEY`. Nonces are random (96-bit). Credentials are encrypted before being stored in the DB and decrypted in the worker at task execution time.

### SDK (local mode)
`sdk/computeruse/client.py → executor.py` runs Playwright directly on the calling machine. The cloud path POSTs to `https://api.computeruse.dev/v1` and polls until completion.

## Key Files

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI app, middleware stack, health + metrics endpoints |
| `api/config.py` | `Settings` (pydantic-settings); all env vars |
| `api/dependencies.py` | Shared FastAPI dependency injection helpers |
| `api/db/engine.py` | SQLAlchemy async engine + session factory |
| `api/middleware/auth.py` | `get_current_account` dependency; SHA-256 key hashing + RLS |
| `api/middleware/rate_limiter.py` | Sliding window rate limiter (Redis) |
| `api/middleware/logging.py` | Structured request/response logging (structlog) |
| `api/middleware/metrics.py` | Prometheus HTTP metrics middleware |
| `api/middleware/credential_scrubber.py` | structlog processor that strips sensitive fields from logs |
| `api/routes/tasks.py` | Task CRUD: POST/GET/DELETE/retry/replay |
| `api/routes/billing.py` | Stripe subscription + usage management |
| `api/routes/account.py` | Account management: API key CRUD |
| `api/routes/audit.py` | Audit log query endpoints |
| `api/routes/sessions.py` | Browser session management endpoints |
| `api/services/audit_logger.py` | Append-only audit log for security-relevant actions |
| `api/services/usage_tracker.py` | Atomic step billing metering |
| `api/management/reset_usage.py` | Script to reset monthly_steps_used for all accounts |
| `api/db/migrations/` | Raw SQL migrations (001 initial schema, 002 seed, 003 audit RLS) |
| `workers/tasks.py` | `execute_task` + `deliver_webhook` Celery tasks |
| `workers/executor.py` | Core async orchestration; step capture; result extraction |
| `workers/browser_manager.py` | Playwright lifecycle; stealth scripts injection |
| `workers/credential_injector.py` | Secure injection of credentials into browser login forms |
| `workers/encryption.py` | AES-256-GCM encrypt/decrypt; HKDF key derivation |
| `workers/captcha_solver.py` | 2Captcha integration |
| `workers/canary.py` | Canary deployment metrics and evaluation |
| `workers/metrics.py` | Prometheus metrics for Celery workers |
| `workers/models.py` | Worker-level data models for task execution engine |
| `workers/config.py` | `WorkerSettings` (pydantic-settings); worker-specific env vars |
| `workers/shutdown.py` | Graceful SIGTERM handler; drains in-progress tasks |
| `workers/stealth/` | Browser fingerprint-evasion JS scripts (7 files) |
| `workers/templates/replay.html` | Replay artifact HTML template |
| `shared/constants.py` | `TIER_LIMITS`, `TIER_STEP_LIMITS`, `TaskStatus`, `ErrorCode` |
| `shared/errors.py` | Shared exception hierarchy (`ComputerUseError` and subclasses) |
| `shared/url_validator.py` | SSRF-safe URL validation (blocks private IPs, metadata endpoints) |
| `tests/conftest.py` | Stubs heavy deps in `sys.modules`; sets env defaults |

## Environment Variables

Copy `.env.example` to `.env`. Required for local dev:

| Variable | Required | Notes |
|----------|----------|-------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` |
| `REDIS_URL` | Yes | |
| `ANTHROPIC_API_KEY` | Yes | |
| `BROWSERBASE_API_KEY` | Yes (cloud) | |
| `BROWSERBASE_PROJECT_ID` | Yes (cloud) | |
| `R2_ACCESS_KEY` / `R2_SECRET_KEY` / `R2_ENDPOINT` / `R2_BUCKET_NAME` | Yes (replays) | Cloudflare R2 |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | Yes (billing) | |
| `STRIPE_PRICE_IDS` | Yes (billing) | JSON dict mapping tier → Stripe price ID |
| `API_SECRET_KEY` | Yes | Random secret for internal signing |
| `ENCRYPTION_MASTER_KEY` | Yes | 32-byte hex key for credential encryption |
| `TWOCAPTCHA_API_KEY` | No | Captcha solving |
| `CANARY_DEPLOYMENT` | No | Set `true` to enable canary metrics tracking |

## Testing Architecture

Unit tests in `tests/unit/` run with no external services. `tests/conftest.py` stubs `anthropic`, `browser_use`, `playwright`, `langchain_anthropic`, `aiohttp`, `boto3`, `botocore`, `psycopg2`, `rich` in `sys.modules` before collection, and sets env var defaults so `api.config.Settings` loads without a `.env` file.

`asyncio_mode = "auto"` (pyproject.toml) — every `async def test_*` runs as an asyncio coroutine without `@pytest.mark.asyncio`.

Integration tests (`tests/integration/`) and e2e tests (`tests/e2e/`) require live services.

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
