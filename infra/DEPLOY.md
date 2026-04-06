# Railway Cloud Deployment

Deploy the full Pokant stack (API, Worker, Dashboard, Cron) on Railway.

## Prerequisites

- [Railway account](https://railway.com) + CLI installed (`npm i -g @railway/cli`)
- External services already provisioned:
  - **PostgreSQL** — Supabase (or Railway's Postgres plugin)
  - **Redis** — Upstash (or Railway's Redis plugin)
  - **Cloudflare R2** — for replay storage (optional)
  - **Stripe** — for billing (optional)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Railway Project                                     │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │   API    │  │  Worker  │  │    Dashboard     │   │
│  │ :8000    │  │ :8001    │  │    :3000         │   │
│  │ FastAPI  │  │ Celery   │  │    Next.js       │   │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │
│       │              │                 │             │
│       └──────┬───────┘                 │             │
│              ▼                         │             │
│  ┌───────────────────┐                 │             │
│  │  Cron (monthly)   │                 │             │
│  │  reset_usage      │                 │             │
│  └───────────────────┘                 │             │
└─────────────────────────────────────────┘             │
           │              │               │             │
           ▼              ▼               ▼             │
    ┌────────────┐  ┌──────────┐  ┌──────────────┐    │
    │ PostgreSQL │  │  Redis   │  │ Cloudflare R2│    │
    │ (Supabase) │  │(Upstash) │  │  (replays)   │    │
    └────────────┘  └──────────┘  └──────────────┘    │
```

## Step 1: Create Railway Project

```bash
railway login
railway init    # creates a new project
```

## Step 2: Add Redis & Postgres (or use external)

**Option A: Railway plugins (simplest)**
```bash
railway add --plugin postgresql
railway add --plugin redis
```
Railway auto-injects `DATABASE_URL` and `REDIS_URL`.

**Option B: External services**
Set `DATABASE_URL` and `REDIS_URL` as shared variables in the Railway project settings.

If using Supabase for Postgres, the `DATABASE_URL` should use the `postgresql+asyncpg://` scheme for the API and `postgresql://` for the worker (sync).

## Step 3: Create Services

Create 4 services in the Railway dashboard (or via CLI):

### 3a. API Service

- **Name:** `api`
- **Source:** this repo
- **Dockerfile path:** `infra/Dockerfile.api`
- **Config:** use `infra/railway.toml` (already at repo root)

### 3b. Worker Service

- **Name:** `worker`
- **Source:** this repo
- **Dockerfile path:** `infra/Dockerfile.worker`
- **Config:** copy contents of `infra/railway-worker.toml`

### 3c. Dashboard Service

- **Name:** `dashboard`
- **Source:** this repo
- **Dockerfile path:** `infra/Dockerfile.dashboard`
- **Config:** copy contents of `infra/railway-dashboard.toml`

### 3d. Cron Service (monthly usage reset)

- **Name:** `usage-reset`
- **Source:** this repo
- **Type:** Cron Job
- **Dockerfile path:** `infra/Dockerfile.api`
- **Schedule:** `0 0 1 * *`
- **Config:** copy contents of `infra/railway-cron.toml`

## Step 4: Set Environment Variables

Set these as **shared variables** across all services in the Railway project:

### Required

| Variable | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | Your Claude API key |
| `DATABASE_URL` | `postgresql+asyncpg://...` (auto-set if using Railway plugin) |
| `REDIS_URL` | `redis://...` (auto-set if using Railway plugin) |
| `ENVIRONMENT` | `production` |
| `API_SECRET_KEY` | Random 32+ char secret |
| `ENCRYPTION_MASTER_KEY` | Random 64-char hex string |

### Per-Service Overrides

**API service:**
| Variable | Value |
|----------|-------|
| `PORT` | `8000` |

**Worker service:**
| Variable | Value |
|----------|-------|
| `BROWSERBASE_API_KEY` | Your Browserbase key |
| `BROWSERBASE_PROJECT_ID` | Your Browserbase project |

**Dashboard service:**
| Variable | Value |
|----------|-------|
| `PORT` | `3000` |
| `NEXT_PUBLIC_API_URL` | `https://api.pokant.live` |
| `INTERNAL_API_URL` | `http://api.railway.internal:8000` |

### Optional (set if using these features)

| Variable | Notes |
|----------|-------|
| `R2_ACCESS_KEY` | Cloudflare R2 |
| `R2_SECRET_KEY` | Cloudflare R2 |
| `R2_BUCKET_NAME` | Default: `pokant-replays` |
| `R2_ENDPOINT` | `https://<account>.r2.cloudflarestorage.com` |
| `STRIPE_SECRET_KEY` | Stripe billing |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing |
| `TWOCAPTCHA_API_KEY` | Captcha solving |

## Step 5: Run Database Migrations

Connect to your Postgres instance and run migrations in order:

```bash
# If using Railway Postgres plugin:
railway run psql $DATABASE_URL

# Then run each migration:
\i api/db/migrations/001_initial_schema.sql
\i api/db/migrations/002_seed_data.sql
# ... through 012
```

Or run them all at once:
```bash
for f in api/db/migrations/*.sql; do
  railway run psql $DATABASE_URL -f "$f"
done
```

## Step 6: Deploy

```bash
railway up
```

Or push to the connected GitHub repo — Railway auto-deploys on push.

## Step 7: Set Up Custom Domain (Optional)

In the Railway dashboard, add custom domains for each service:
- **API:** `api.pokant.live`
- **Dashboard:** `app.pokant.live`

Then in Cloudflare DNS (pokant.live zone), add CNAME records:
```
api.pokant.live  → CNAME → <api-service>.up.railway.app
app.pokant.live  → CNAME → <dashboard-service>.up.railway.app
```

Set Cloudflare proxy to **DNS only** (grey cloud) for these records — Railway handles SSL.

Update `NEXT_PUBLIC_API_URL` to `https://api.pokant.live` in the dashboard service.

## Networking

- Railway services on the same project can communicate via **private networking**: `http://<service-name>.railway.internal:<port>`
- The dashboard uses `INTERNAL_API_URL` for server-side requests (private) and `NEXT_PUBLIC_API_URL` for browser requests (public)
- The worker connects to Redis and Postgres directly — no inter-service HTTP needed

## Monitoring

- **API:** `/health` endpoint + `/metrics` (Prometheus)
- **Worker:** `/health` on port 8001 (lightweight HTTP probe)
- **Logs:** Railway log drain captures structured JSON logs from both API and worker
- **Alerts:** Built-in Railway alerts + Pokant's own alert system

## Scaling

Adjust `numReplicas` in each service's TOML config or via Railway dashboard:

- **API:** scale horizontally (stateless, multiple uvicorn workers)
- **Worker:** scale horizontally (each replica runs 2 Celery workers)
- **Dashboard:** typically 1 replica is sufficient

## Costs (Estimate)

| Service | Memory | CPU | ~Monthly |
|---------|--------|-----|----------|
| API | 512MB–2GB | 0.5–2 vCPU | $5–20 |
| Worker | 1–4GB | 1–2 vCPU | $10–40 |
| Dashboard | 256MB–1GB | 0.25–1 vCPU | $3–10 |
| Cron | runs ~1s/month | minimal | <$1 |
| Redis (plugin) | 256MB | — | $5 |
| Postgres (plugin) | 1GB | — | $7 |

Total: **~$30–80/month** depending on traffic.
