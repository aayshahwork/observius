# E2E Production Test Checklist

Target: https://pokant.live (dashboard) + https://api.pokant.live (API)

---

## Test 1: Signup Flow

Open https://pokant.live/signup in a fresh incognito window.

- [ ] `/signup` page loads without error
- [ ] Email form is displayed
- [ ] Submitting sends `POST https://api.pokant.live/api/v1/auth/register` (not localhost)
- [ ] Response contains an API key in format `cu_live_<32hex>`
- [ ] API key is displayed clearly on success screen
- [ ] "Save this key — you won't see it again" warning is shown
- [ ] Copy button works
- [ ] "Continue to Dashboard" button logs in and redirects to `/tasks`

**Fix if broken:**
- Verify `NEXT_PUBLIC_API_URL=https://api.pokant.live` in Vercel env vars
- Redeploy: `cd dashboard && vercel --prod`

---

## Test 2: Login Flow

Open https://pokant.live/login.

- [ ] `/login` page loads
- [ ] Entering a valid API key and clicking Continue validates against the API
- [ ] Invalid key shows error message ("Invalid API key")
- [ ] Valid key stores it as `computeruse_api_key` in localStorage
- [ ] Valid key redirects to `/tasks`
- [ ] Dashboard loads with no console errors
- [ ] Sidebar shows: Overview, Health, Tasks, Scripts, Sessions, Usage, Settings

**Fix if broken (CORS):**
- Check `api/main.py` CORSMiddleware origins list
- Add `https://pokant.live` if missing, redeploy API on Railway

---

## Test 3: Empty State

After login with a fresh account:

- [ ] `/tasks` — loads without error, shows empty state message
- [ ] `/tasks` — "New Task" or similar CTA is visible (if applicable)
- [ ] `/usage` — shows usage stats (0 steps used, free tier limits)
- [ ] `/settings` — shows account info (email, masked API key, tier)
- [ ] Logout button appears in sidebar and clears session → redirects to `/login`

---

## Test 4: Create a Task via API

```bash
curl -X POST https://api.pokant.live/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{
    "url": "https://example.com",
    "task": "Extract the page title and first paragraph",
    "output_schema": {"title": "string", "first_paragraph": "string"}
  }'
```

- [ ] Returns `201 Created` with a `task_id`
- [ ] `status` is `"queued"` or `"running"`

> Note: Task may stay in `"queued"` if workers aren't running (no Browserbase). That's OK — we're testing API connectivity, not execution.

---

## Test 5: Task Appears in Dashboard

- [ ] `/tasks` shows the task from Test 4 in the list
- [ ] Row shows: task_id, status badge, URL, created timestamp
- [ ] Clicking the row loads `/tasks/[id]`
- [ ] Task detail shows: URL, task description, status
- [ ] Shows result (if completed) or pending/running indicator
- [ ] Steps timeline renders (or shows empty if no steps yet)
- [ ] No console errors on the detail page

---

## Test 6: API Key Validation

```bash
# Invalid key → 401
curl https://api.pokant.live/api/v1/tasks \
  -H "X-API-Key: cu_live_invalid_key_12345"

# No key → 401
curl https://api.pokant.live/api/v1/tasks
```

- [ ] Invalid key returns `401 Unauthorized`
- [ ] No key returns `401 Unauthorized`
- [ ] In dashboard: clear localStorage → refresh → AuthGuard redirects to `/login`
- [ ] No flash of dashboard content before redirect

---

## Test 7: Cross-Origin Requests

Open DevTools on https://pokant.live after login.

- [ ] Network tab shows no CORS errors on any request
- [ ] No `Access-Control-Allow-Origin` errors in console
- [ ] Preflight `OPTIONS` requests return `200`
- [ ] All API requests use `https://api.pokant.live` (not localhost)

---

## Common Fixes Reference

| Symptom | Fix |
|---------|-----|
| Signup POSTs to `localhost:8000` | Set `NEXT_PUBLIC_API_URL=https://api.pokant.live` in Vercel → redeploy |
| Login CORS error | Add `https://pokant.live` to `api/main.py` CORS origins → redeploy API |
| Signup returns 500 | Check Railway API logs: `railway logs --service api` — likely DB issue |
| Tasks don't appear | Check API response format vs `dashboard/src/app/(dashboard)/tasks/page.tsx` |
| AuthGuard doesn't redirect | Ensure `DEFAULT_API_KEY` fallback is removed from `auth-context.tsx` |
| Login accepts invalid keys | Ensure login page validates key via API before calling `login()` |
