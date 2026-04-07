# TODO: Deployment Link Checklist

Track all URL, link, and routing tasks before and after pokant.live goes live.

## One-Time Setup

- [x] **Formspree**: Endpoint `https://formspree.io/f/xbdpwokl` configured in `dashboard/src/app/contact/page.tsx`.

## Landing Page (`dashboard/src/app/page.tsx`)

- [x] Logo "Pokant" links to `/` (home)
- [x] "Log in" nav link → `/login`
- [x] "Sign up" nav link → `/signup`
- [x] Hero "Get Started Free" → `/signup`
- [x] Hero "View on GitHub" → `https://github.com/aayshahwork/pokant`
- [x] Pricing tier CTAs (Free, Startup, Growth) → `/signup`
- [x] Pricing tier "Contact Us" (Enterprise) → `/contact`
- [x] Footer GitHub link → `https://github.com/aayshahwork/pokant`

## Auth Pages

- [x] `/login` — redirects to `/tasks` on success
- [x] `/login` — "Need an account? Sign up free" → `/signup`
- [x] `/signup` — API key shown with copy button on success
- [x] `/signup` — "Continue to Dashboard" → `/tasks`
- [x] `/signup` — "Already have an account? Log in" → `/login`

## Contact Page (`dashboard/src/app/contact/page.tsx`)

- [x] Form submits to Formspree endpoint
- [x] Replace `YOUR_FORM_ID` with real Formspree form ID (`xbdpwokl`)
- [x] "Need a regular account? Sign up free" → `/signup`
- [x] Logo "Pokant" links to `/`

## Dashboard Navigation

- [x] Sidebar: Overview → `/overview`
- [x] Sidebar: Health → `/health`
- [x] Sidebar: Tasks → `/tasks`
- [x] Sidebar: Scripts → `/scripts`
- [x] Sidebar: Sessions → `/sessions`
- [x] Sidebar: Usage → `/usage`
- [x] Sidebar: Settings → `/settings`
- [x] Logout clears auth and redirects to `/login`

## README (`README.md`)

- [x] GitHub badge → `https://github.com/aayshahwork/pokant`
- [x] pip install URL → `https://github.com/aayshahwork/pokant.git#subdirectory=sdk`
- [x] Cloud API section added with `pokant.live` URLs
- [x] Enterprise contact link → `https://pokant.live/contact`

## Production Verification (do after deploy)

- [ ] https://pokant.live loads correctly
- [ ] https://pokant.live/signup creates account + shows API key
- [ ] https://pokant.live/login authenticates + redirects to /tasks
- [ ] https://pokant.live/contact form submits and email arrives at avidesai0110@gmail.com
- [ ] https://pokant.live/tasks shows task list (auth-gated)
- [ ] GitHub repo https://github.com/aayshahwork/pokant is public
- [ ] GitHub stars badge renders on README
