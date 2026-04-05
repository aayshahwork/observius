"""
demo_dashboard.py — Multi-source intelligence demo using ObserviusTracker.

How the pieces connect
──────────────────────
  demo_dashboard.py
    └─ ObserviusTracker(api_url="http://localhost:8000", api_key="demo")
         └─ _reporting.py  →  POST localhost:8000/api/v1/tasks/ingest
              └─ api/local_bridge.py  stores in .local_tasks.json
                   └─ GET localhost:8000/api/v1/tasks
                        └─ Next.js dashboard (localhost:3000/tasks)

Run in 3 terminals
──────────────────
  Terminal 1:  cd dashboard && npm run dev
  Terminal 2:  python api/local_bridge.py
  Terminal 3:  python demo_dashboard.py

Open http://localhost:3000  → enter any key (e.g. "demo") → go to /tasks.
Tasks appear one-by-one as each scrape completes.
"""

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

import sys, time, logging, json as _json, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent / "sdk"))

# Suppress noisy browser_use logs
for _name in ("browser_use.service", "browser_use.browser.session", "browser_use.browser"):
    logging.getLogger(_name).setLevel(logging.WARNING)

try:
    import browser_use as _bu
    _orig = _bu.Agent
    _bu.Agent = lambda *a, **kw: _orig(*a, **{**kw, "use_judge": False})
except Exception:
    pass

from computeruse import ComputerUse
from computeruse.tracker import ObserviusTracker, TrackerConfig
from rich.console import Console
from rich.table import Table

BRIDGE = "http://localhost:8000"

TARGETS = [
    {
        "label": "Hacker News",
        "url": "https://news.ycombinator.com",
        "task": "Get the top 5 post titles and their point counts",
        "schema": {"titles": "list[str]", "points": "list[str]"},
    },
    {
        "label": "GitHub Trending",
        "url": "https://github.com/trending",
        "task": "Get the names and descriptions of the top 3 trending repositories",
        "schema": {"repos": "list[str]", "descriptions": "list[str]"},
    },
    {
        "label": "Product Hunt",
        "url": "https://producthunt.com",
        "task": "Get the names and taglines of today's top 3 products",
        "schema": {"products": "list[str]", "taglines": "list[str]"},
    },
]


def _patch_ingest(task_id: str, url: str, result: dict | None) -> None:
    """PATCH extra fields (url, result) into the already-ingested task.

    ObserviusTracker._reporting only sends what it knows about — task_description,
    steps (with screenshot_base64), cost, tokens, status.  It doesn't know about
    the scrape URL or the extracted result dict.  We POST those separately to
    /ingest with the same task_id (bridge upserts on task_id).
    """
    payload = {"task_id": task_id, "url": url, "result": result}
    try:
        body = _json.dumps(payload, default=str).encode()
        req = urllib.request.Request(
            f"{BRIDGE}/api/v1/tasks/ingest",
            data=body,
            headers={"Content-Type": "application/json", "X-API-Key": "demo"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as exc:
        console.print(f"  [dim yellow]patch failed: {exc}[/dim yellow]")


# ---------------------------------------------------------------------------

console = Console()
console.print("\n[bold cyan]🔍 Observius — Multi-Source Intelligence Pipeline[/bold cyan]")
console.print("[dim]ObserviusTracker → local bridge → dashboard[/dim]\n")
for i, t in enumerate(TARGETS, 1):
    console.print(f"  [dim]{i}.[/dim] {t['url']}")
console.print()

cu = ComputerUse(headless=False)
results, start_total = [], time.time()

for i, target in enumerate(TARGETS, 1):
    domain = target["url"].split("//")[1]
    console.print(f"[yellow]⏳ [{i}/3] Scraping {domain}...[/yellow]")
    t0 = time.time()

    # Create a tracker wired to the local bridge.
    # When tracker.complete() / tracker.fail() is called, _reporting.py
    # fires POST localhost:8000/api/v1/tasks/ingest automatically.
    tracker = ObserviusTracker(
        TrackerConfig(
            task_description=target["task"],
            api_url=BRIDGE,
            api_key="demo",
            generate_replay=False,   # skip disk replay for demos
            save_screenshots=False,
        )
    )
    tracker.start()

    try:
        result = cu.run_task(
            url=target["url"],
            task=target["task"],
            output_schema=target["schema"],
            max_steps=6,
        )
        elapsed = time.time() - t0
        duration_ms = int(elapsed * 1000)

        # Record the completed task as a single summary step.
        # (The SDK's executor runs its own internal loop; we surface the
        # outcome as one "extract" step in the tracker.)
        data = result.result or {}
        first_key = next(iter(target["schema"]))
        n = len(data.get(first_key) or [])
        tracker.record_step(
            action_type="extract",
            description=f"Scraped {domain} — {n} items in {elapsed:.1f}s",
            tokens_in=result.total_tokens_in or 0,
            tokens_out=result.total_tokens_out or 0,
            success=True,
            duration_ms=duration_ms,
        )
        tracker.complete(result=data)
        # _reporting.py already posted steps (with screenshot_base64) to the bridge.
        # Just patch in the url + result fields without re-sending steps.
        _patch_ingest(tracker.task_id, target["url"], data)

        console.print(f"[green]✅ [{i}/3] Done in {elapsed:.1f}s — {n} items found[/green]")
        console.print(f"  [dim]→ task_id: {tracker.task_id[:8]}… posted to dashboard[/dim]\n")

        results.append({
            "label": target["label"],
            "data": data,
            "elapsed": round(elapsed, 1),
            "steps": result.steps,
            "ok": True,
        })

    except Exception as exc:
        elapsed = time.time() - t0
        console.print(f"[red]❌ [{i}/3] {target['label']} failed: {exc}[/red]")
        tracker.fail(error=str(exc))
        _patch_ingest(tracker.task_id, target["url"], None)
        console.print()
        results.append({
            "label": target["label"], "ok": False,
            "data": {}, "elapsed": round(elapsed, 1), "steps": 0,
        })

# Summary table
table = Table(title="Intelligence Report", header_style="bold magenta")
table.add_column("Source", style="cyan", width=14)
table.add_column("Data", width=34)
table.add_column("Time", justify="right", width=8)
table.add_column("Steps", justify="right", width=7)

for r in results:
    vals = next(iter(r["data"].values()), []) if r["data"] else []
    preview = ", ".join(str(v) for v in (vals[:2] if isinstance(vals, list) else [vals]))
    table.add_row(
        r["label"],
        (preview[:31] + "…" if len(preview) > 32 else preview) or "—",
        f"{r['elapsed']}s",
        str(r["steps"]) if r["ok"] else "—",
    )

console.print(table)
total = round(time.time() - start_total, 1)
console.print(f"\n[bold green]✅ {len(results)} sources scraped in {total}s[/bold green]")
console.print("[dim]Dashboard → http://localhost:3000/tasks[/dim]\n")
