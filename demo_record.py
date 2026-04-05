from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)
import sys, time, logging, os
sys.path.insert(0, str(Path(__file__).parent / "sdk"))

# Suppress noisy browser_use logs — keep only Agent step output
logging.getLogger("browser_use.service").setLevel(logging.WARNING)
logging.getLogger("browser_use.browser.session").setLevel(logging.WARNING)
logging.getLogger("browser_use.browser").setLevel(logging.WARNING)

# Disable the judge via monkey-patch (use_judge param exists in 0.12.2)
import browser_use
_orig_agent = browser_use.Agent
browser_use.Agent = lambda *a, **kw: _orig_agent(*a, **{**kw, "use_judge": False})

from computeruse import ComputerUse
from rich.console import Console
from rich.table import Table

console = Console()
targets = [
    {"name": "Browserbase", "url": "https://browserbase.com/pricing",
     "task": "Extract all pricing plan names and their monthly prices",
     "output_schema": {"plans": "list[str]", "prices": "list[str]"}},
    {"name": "Hacker News", "url": "https://news.ycombinator.com",
     "task": "Get the top 5 post titles and their point counts",
     "output_schema": {"titles": "list[str]", "points": "list[str]"}},
]

console.print("\n🔍 [bold]Observius — Competitive Intelligence Demo[/bold]")
console.print("Extracting structured data from 2 sources...\n")

cu = ComputerUse(headless=False)
results, start_total = [], time.time()

for i, t in enumerate(targets, 1):
    n = len(targets)
    console.print(f"[yellow]⏳ [{i}/{n}] Scraping {t['url'].split('//')[1]}...[/yellow]")
    t0 = time.time()
    try:
        r = cu.run_task(url=t["url"], task=t["task"],
                        output_schema=t["output_schema"], max_steps=6)
        duration = time.time() - t0
        first_key = next(iter(t["output_schema"]))
        items = (r.result or {}).get(first_key) or []
        console.print(f"[green]✅ [{i}/{n}] {t['name']} — {len(items)} items found in {duration:.1f}s[/green]\n")
        results.append({"name": t["name"], "data": r.result or {}, "schema": t["output_schema"],
                        "duration": duration, "steps": r.steps, "ok": True})
    except Exception as e:
        duration = time.time() - t0
        console.print(f"[red]❌ [{i}/{n}] {t['name']} — failed: {e}[/red]\n")
        results.append({"name": t["name"], "ok": False, "data": {}, "duration": duration, "steps": 0})
    time.sleep(1)

table = Table(title="Competitive Intelligence Report", header_style="bold magenta")
table.add_column("Source", style="cyan", width=13)
table.add_column("Data", width=36)
table.add_column("Time", justify="right", width=8)
table.add_column("Steps", justify="right", width=6)

for r in results:
    vals = next(iter(r["data"].values()), []) if r["data"] else []
    preview = ", ".join(str(v) for v in (vals[:2] if isinstance(vals, list) else [vals]))
    table.add_row(r["name"], (preview[:33] + "…" if len(preview) > 34 else preview) or "—",
                  f"{r['duration']:.1f}s", str(r["steps"]) if r["ok"] else "—")

console.print(table)
total = time.time() - start_total
console.print(f"\n[bold green]✅ 2 sources scraped in {total:.0f}s — structured data ready[/bold green]\n")
