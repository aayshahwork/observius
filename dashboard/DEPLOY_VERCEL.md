# Deploying the Dashboard to Vercel

## Prerequisites

- A Vercel account (https://vercel.com)
- The repo pushed to GitHub at `https://github.com/aayshahwork/observius`
- The API running on Railway (or any public URL)

---

## 1. Connect to Vercel

1. Go to https://vercel.com/new
2. Import the GitHub repo `aayshahwork/observius`
3. Configure the project:

| Setting            | Value         |
| ------------------ | ------------- |
| **Root Directory** | `dashboard`   |
| **Framework**      | Next.js       |
| **Build Command**  | `npm run build` (default) |
| **Output Dir**     | `.next` (default) |
| **Install Command**| `npm install` (default) |
| **Node.js Version**| 18.x or 20.x |

4. Click **Deploy**

## 2. Environment Variables

Set these in Vercel project settings > **Settings > Environment Variables**:

| Variable | Value | Required |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `https://api.pokant.live` | Yes |

Notes:
- `NEXT_PUBLIC_API_URL` is used client-side (browser fetch calls). It must be the public API URL.
- Do NOT include a trailing slash.
- The `INTERNAL_API_URL` env var is only needed for Docker/server-side proxying and is not used on Vercel.

## 3. Verify the Deployment

After deployment, check these pages:

| URL | Expected |
| --- | --- |
| `https://app.pokant.live/` | Landing page (no auth) |
| `https://app.pokant.live/signup` | Signup form (no auth) |
| `https://app.pokant.live/login` | Login form (no auth) |
| `https://app.pokant.live/tasks` | Redirects to /login (requires auth) |

Test the signup flow:
1. Go to `/signup`, enter an email
2. You should get an API key back (this calls the Railway API)
3. Click "Continue to Dashboard" — should redirect to `/tasks`

## 4. Custom Domain (pokant.live)

Since `pokant.live` DNS is on Cloudflare:

1. Go to Vercel project > **Settings > Domains**
2. Add `app.pokant.live`
3. In Cloudflare DNS, add:

   ```
   Type: CNAME
   Name: app
   Value: cname.vercel-dns.com
   Proxy: DNS only (grey cloud)
   ```

4. Vercel auto-provisions an SSL certificate
5. Ensure `NEXT_PUBLIC_API_URL` is set to `https://api.pokant.live`

## 5. CORS on the Railway API

The dashboard makes client-side fetch calls to the API. The CORS middleware in `api/main.py` already allows:

- `https://pokant.live`
- `https://app.pokant.live`
- `https://pokant.vercel.app`
- Any `https://pokant-*.vercel.app` (preview deployments)

## 6. Redeployments

- **Automatic:** Every push to `main` triggers a new deployment
- **Manual:** Vercel dashboard > Deployments > Redeploy
- **Env var changes:** Require a redeployment to take effect (Vercel does NOT auto-redeploy on env var change)

## 7. Troubleshooting

**Signup returns "Could not connect to the API":**
- Check that `NEXT_PUBLIC_API_URL` is set to `https://api.pokant.live`
- Check Railway API is running and healthy: `curl https://api.pokant.live/health`
- Check CORS is configured on the API

**Login works but dashboard pages are blank:**
- Open browser console — look for CORS or network errors
- Verify the API key format matches what the API expects (`cu_live_...`)

**Build fails on Vercel:**
- Check that the root directory is set to `dashboard`
- Check Node.js version matches local (18.x or 20.x)
