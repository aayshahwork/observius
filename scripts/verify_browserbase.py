"""
Verify Browserbase connectivity via CDP + Playwright.

Usage:
    export BROWSERBASE_API_KEY=your_key
    export BROWSERBASE_PROJECT_ID=your_project_id
    python scripts/verify_browserbase.py

Or pass directly:
    python scripts/verify_browserbase.py <api_key> <project_id>
"""

from __future__ import annotations

import asyncio
import os
import sys
import time


def get_credentials() -> tuple[str, str]:
    if len(sys.argv) >= 3:
        return sys.argv[1], sys.argv[2]
    api_key = os.environ.get("BROWSERBASE_API_KEY", "")
    project_id = os.environ.get("BROWSERBASE_PROJECT_ID", "")
    if not api_key or not project_id:
        print("Usage: python scripts/verify_browserbase.py <api_key> <project_id>")
        print("   or: export BROWSERBASE_API_KEY=... BROWSERBASE_PROJECT_ID=...")
        sys.exit(1)
    return api_key, project_id


def mask(s: str) -> str:
    if len(s) <= 12:
        return s[:4] + "..."
    return s[:8] + "..." + s[-4:]


async def main() -> None:
    api_key, project_id = get_credentials()
    print(f"API Key:    {mask(api_key)}")
    print(f"Project ID: {project_id}\n")

    total_pass = 0
    total_fail = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal total_pass, total_fail
        if ok:
            total_pass += 1
            print(f"  [PASS] {label}")
        else:
            total_fail += 1
            msg = f"  [FAIL] {label}"
            if detail:
                msg += f" — {detail}"
            print(msg)

    # ------------------------------------------------------------------
    # 1. Check dependencies
    # ------------------------------------------------------------------
    try:
        import httpx
    except ImportError:
        print("[FAIL] httpx not installed. Run: pip install httpx")
        sys.exit(1)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[FAIL] playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Create a Browserbase session
    # ------------------------------------------------------------------
    print("== Browserbase API ==")
    session_id = None
    t0 = time.time()

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                "https://api.browserbase.com/v1/sessions",
                headers={
                    "x-bb-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={"projectId": project_id},
            )
            if resp.status_code in (200, 201):
                session_data = resp.json()
                session_id = session_data["id"]
                elapsed = int((time.time() - t0) * 1000)
                check(True, f"session created: {session_id} ({elapsed}ms)")
            else:
                check(False, "create session", f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            check(False, "create session", str(e))

    if not session_id:
        print("\nCannot continue without a session.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. Connect via CDP
    # ------------------------------------------------------------------
    print("\n== CDP Connection ==")
    cdp_url = f"wss://connect.browserbase.com?apiKey={api_key}&sessionId={session_id}"

    try:
        async with async_playwright() as p:
            t0 = time.time()
            browser = await p.chromium.connect_over_cdp(cdp_url)
            elapsed = int((time.time() - t0) * 1000)
            check(True, f"CDP connected ({elapsed}ms)")

            ctx_count = len(browser.contexts)
            check(ctx_count > 0, f"browser contexts available: {ctx_count}")

            # ----------------------------------------------------------
            # 4. Load a page
            # ----------------------------------------------------------
            print("\n== Page Navigation ==")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            t0 = time.time()
            resp = await page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
            elapsed = int((time.time() - t0) * 1000)

            status = resp.status if resp else 0
            check(status == 200, f"GET https://example.com → {status} ({elapsed}ms)")

            title = await page.title()
            check("Example" in title, f"page title: \"{title}\"")

            # ----------------------------------------------------------
            # 5. Basic DOM interaction
            # ----------------------------------------------------------
            print("\n== DOM Interaction ==")
            heading = await page.text_content("h1")
            check(heading is not None and len(heading) > 0, f"<h1> text: \"{heading}\"")

            url = page.url
            check("example.com" in url, f"current URL: {url}")

            # ----------------------------------------------------------
            # 6. Screenshot capability
            # ----------------------------------------------------------
            print("\n== Screenshot ==")
            try:
                screenshot = await page.screenshot(type="png")
                size_kb = len(screenshot) / 1024
                check(size_kb > 1, f"screenshot captured: {size_kb:.1f} KB")
            except Exception as e:
                check(False, "screenshot", str(e))

            # ----------------------------------------------------------
            # 7. Cleanup
            # ----------------------------------------------------------
            print("\n== Cleanup ==")
            await browser.close()
            check(True, "browser closed")

    except Exception as e:
        check(False, "CDP connection", str(e))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total = total_pass + total_fail
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {total_pass}/{total} passed, {total_fail} failed")
    if total_fail == 0:
        print("Browserbase is ready for production!")
    else:
        print("Fix the failures above before deploying.")
    print(f"{'=' * 50}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
