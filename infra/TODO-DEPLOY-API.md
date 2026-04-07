# Railway API Deployment — TODO

## Status: IN PROGRESS

---

## Phase 1: Diagnose current Railway state
- [ ] Check Railway dashboard — list all services (api, worker, redis, cron)
- [ ] Check which services are Active / Crashed / Idle
- [ ] Read api service logs — identify the exact startup error
- [ ] Confirm whether a Redis service/plugin exists

## Phase 2: Set environment variables on Railway api service
- [ ] DATABASE_URL — Supabase session-mode pooler (port 5432, postgresql+asyncpg scheme)
- [ ] REDIS_URL — from Railway Redis plugin (auto-injected) or Upstash
- [ ] ENVIRONMENT = production
- [ ] ANTHROPIC_API_KEY — from local .env
- [ ] API_SECRET_KEY — from local .env
- [ ] ENCRYPTION_MASTER_KEY — from local .env
- [ ] DASHBOARD_URL = https://pokant.live
- [ ] R2_ACCESS_KEY — from local .env
- [ ] R2_SECRET_KEY — from local .env
- [ ] R2_ENDPOINT — from local .env
- [ ] R2_BUCKET_NAME = pokant-sessions
- [ ] BROWSERBASE_API_KEY — from browserbase.com (needed for worker, optional for API)
- [ ] BROWSERBASE_PROJECT_ID — from browserbase.com

## Phase 3: Set environment variables on Railway worker service
- [ ] Same DATABASE_URL (use postgresql:// NOT postgresql+asyncpg:// — Celery uses sync driver)
- [ ] Same REDIS_URL
- [ ] Same ANTHROPIC_API_KEY
- [ ] Same API_SECRET_KEY
- [ ] Same ENCRYPTION_MASTER_KEY
- [ ] Same ENVIRONMENT = production
- [ ] BROWSERBASE_API_KEY
- [ ] BROWSERBASE_PROJECT_ID

## Phase 4: Add Redis if missing
- [ ] Railway dashboard → New → Database → Redis
- [ ] Link Redis to api and worker services

## Phase 5: Redeploy and check logs
- [ ] Trigger redeploy on api service
- [ ] Trigger redeploy on worker service
- [ ] Confirm "Uvicorn running on 0.0.0.0:8000" in api logs
- [ ] Confirm Celery worker startup in worker logs

## Phase 6: Test Railway URL directly
- [ ] curl https://pokant-production.up.railway.app/health → 200
- [ ] curl https://pokant-production.up.railway.app/docs → 200
- [ ] POST /api/v1/auth/register → returns API key
- [ ] GET /api/v1/tasks with key → 200 (empty list)
- [ ] GET /api/v1/tasks with bad key → 401

## Phase 7: DNS — api.pokant.live
- [ ] Railway api service → Settings → Networking → Custom Domain → add api.pokant.live
- [ ] Note the CNAME target Railway provides
- [ ] Cloudflare pokant.live DNS → Add CNAME: api → Railway target (grey cloud / DNS only)
- [ ] Wait 1-5 min, then: curl https://api.pokant.live/health → 200

## Phase 8: Verify DB migrations
- [ ] POST /api/v1/auth/register succeeds (proves accounts table exists)
- [ ] If 500 "relation does not exist" → run migrations via Supabase SQL editor

---

## Values to set (from local .env)

| Variable | Source |
|----------|--------|
| ANTHROPIC_API_KEY | local .env |
| API_SECRET_KEY | local .env |
| ENCRYPTION_MASTER_KEY | local .env |
| R2_ACCESS_KEY | local .env |
| R2_SECRET_KEY | local .env |
| R2_ENDPOINT | local .env |
| R2_BUCKET_NAME | local .env (pokant-sessions) |
| DATABASE_URL | Supabase → Settings → Database → Session pooler URL |
| REDIS_URL | Railway Redis plugin (auto-injected) |
| BROWSERBASE_API_KEY | browserbase.com dashboard |
| BROWSERBASE_PROJECT_ID | browserbase.com dashboard |

---

## Verification Checklist
- [ ] Railway api service → Active (green)
- [ ] Railway worker service → Active (green)
- [ ] Railway Redis service exists and linked
- [ ] https://pokant-production.up.railway.app/health → 200
- [ ] https://api.pokant.live/health → 200
- [ ] https://api.pokant.live/docs → loads Swagger UI
- [ ] POST register → API key returned
- [ ] GET /tasks with valid key → 200
- [ ] GET /tasks with invalid key → 401
- [ ] No errors in api logs
- [ ] No errors in worker logs
