# Dashboard Deployment — Vercel

Deploy the Next.js dashboard to Vercel with the custom domain `app.pokant.live`.

---

## Status: COMPLETE ✅

---

## Pre-flight Checklist

- [x] `DEPLOY_VERCEL.md` guide exists
- [x] `next.config.mjs` reviewed — `output: "standalone"` present (see note below)
- [x] CORS in `api/main.py` already includes `https://pokant.live` and `https://app.pokant.live`
- [x] Env vars identified: `NEXT_PUBLIC_API_URL`, `INTERNAL_API_URL`

### Note: `output: "standalone"` in next.config.mjs
Vercel ignores this setting and uses its own build pipeline. No change needed — it won't break anything.

---

## Steps

### Step 1 — Install Vercel CLI & Login
- [ ] `npm install -g vercel`
- [ ] `vercel login` (browser OAuth)

### Step 2 — Link Project
- [ ] `cd dashboard && vercel link`
  - Scope: your Vercel account/team
  - Project name: `observius` or `pokant-dashboard`
  - Root directory: `dashboard` (confirm yes when prompted)

### Step 3 — Set Environment Variables in Vercel
In Vercel → project → Settings → Environment Variables:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.pokant.live` |
| `INTERNAL_API_URL` | `https://api.pokant.live` |

- [ ] Set `NEXT_PUBLIC_API_URL`
- [ ] Set `INTERNAL_API_URL`

### Step 4 — Deploy to Production
- [ ] `cd dashboard && vercel --prod`
- [ ] Confirm deployment URL (e.g. `pokant-dashboard.vercel.app`)

### Step 5 — Configure Custom Domain
In Vercel → project → Settings → Domains:

- [ ] Add `app.pokant.live`
- [ ] Add `pokant.live` (for the landing page at the root domain)

In Cloudflare DNS:

| Type | Name | Value | Proxy |
|---|---|---|---|
| CNAME | app | cname.vercel-dns.com | DNS only (grey) |
| CNAME | @ | cname.vercel-dns.com | DNS only (grey) |

> If Cloudflare doesn't allow CNAME at `@`, use: A record `@` → `76.76.21.21`

- [ ] Add `app` CNAME in Cloudflare
- [ ] Add `@` CNAME (or A record) in Cloudflare
- [ ] SSL certificates provisioned by Vercel (automatic, ~2 min)

### Step 6 — Verify

- [ ] `https://pokant.live` loads landing page (hero, pricing, code example)
- [ ] `https://app.pokant.live` loads landing page
- [ ] `https://pokant.live/login` loads login page
- [ ] `https://pokant.live/signup` loads signup page
- [ ] `https://app.pokant.live/login` loads login page
- [ ] Signup flow works end-to-end (email → API key → dashboard)
- [ ] No CORS errors in browser console
- [ ] No stuck loading spinners
- [ ] Mobile responsive

---

## Rollback
- Vercel keeps all previous deployments — just promote any older one via the dashboard
- Zero downtime rollbacks
