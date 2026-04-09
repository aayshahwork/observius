import { NextResponse } from "next/server";

/**
 * GET /api/config
 *
 * Returns runtime configuration read from server-side env vars.
 * Using a server route means the same Docker image can behave
 * differently based on env vars set at container startup — no
 * rebuild required.
 *
 * DISABLE_AUTH=true   → skip login (local Docker)
 * DEFAULT_API_KEY=... → API key injected automatically when auth is disabled
 *
 * On Vercel these vars are not set, so auth stays enabled.
 */
export async function GET() {
  const disableAuth = process.env.DISABLE_AUTH === "true";
  const defaultApiKey = disableAuth
    ? (process.env.DEFAULT_API_KEY ?? null)
    : null;

  return NextResponse.json({ requireAuth: !disableAuth, defaultApiKey });
}
