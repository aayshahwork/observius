from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)
import sys, time; sys.path.insert(0, str(Path(__file__).parent / "sdk"))

from computeruse import ComputerUse
from rich.console import Console
from rich.table import Table

console = Console()
TARGETS = [
    {"label": "Hacker News", "url": "https://news.ycombinator.com",
     "task": "Get the top 5 post titles and their point counts",
     "schema": {"titles": "list[str]", "points": "list[str]"}},
    {"label": "GitHub Trending", "url": "https://github.com/trending",
     "task": "Get the names and descriptions of the top 3 trending repositories",
     "schema": {"repos": "list[str]", "descriptions": "list[str]"}},
    {"label": "Product Hunt", "url": "https://producthunt.com",
     "task": "Get the names and taglines of today's top 3 products",
     "schema": {"products": "list[str]", "taglines": "list[str]"}},
]

console.print("\n[bold cyan]🔍 Pokant — Multi-Source Intelligence Pipeline[/bold cyan]")
console.print("[dim]Extracting structured data from 3 sources in one workflow...[/dim]\n")
for i, t in enumerate(TARGETS, 1):
    console.print(f"  [dim]{i}.[/dim] {t['url']}")
console.print()

cu = ComputerUse(headless=False)
if cu.pokant_api_url and cu.pokant_api_key:
    console.print(f"[green]Dashboard reporting enabled[/green] → {cu.pokant_api_url}\n")
else:
    console.print("[dim]Dashboard reporting disabled (set POKANT_API_URL and POKANT_API_KEY in .env to enable)[/dim]\n")
results, start_total = [], time.time()

for i, target in enumerate(TARGETS, 1):
    domain = target["url"].split("//")[1]
    console.print(f"[yellow]⏳ [{i}/3] Scraping {domain}...[/yellow]")
    t0 = time.time()
    result = cu.run_task(
        url=target["url"], task=target["task"],
        output_schema=target["schema"], max_steps=6,
    )
    elapsed = round(time.time() - t0, 1)
    data = result.result or {}
    first_key = next(iter(target["schema"]))
    n = len(data.get(first_key) or [])
    console.print(f"[green]✅ [{i}/3] Done in {elapsed}s — {n} items found[/green]\n")
    results.append({"label": target["label"], "data": data, "elapsed": elapsed, "steps": result.steps})

table = Table(title="Intelligence Report", header_style="bold magenta")
table.add_column("Source", style="cyan", width=14)
table.add_column("Data", width=34)
table.add_column("Time", justify="right", width=8)
table.add_column("Steps", justify="right", width=7)

for r in results:
    vals = next(iter(r["data"].values()), []) if r["data"] else []
    preview = ", ".join(str(v) for v in (vals[:2] if isinstance(vals, list) else [vals]))
    table.add_row(r["label"], (preview[:31] + "…" if len(preview) > 32 else preview) or "—",
                  f"{r['elapsed']}s", str(r["steps"]))

console.print(table)
total = round(time.time() - start_total, 1)
console.print(f"\n[bold green]✅ 3 sources scraped in {total}s — structured intelligence ready[/bold green]\n")
