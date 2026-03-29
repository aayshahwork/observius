"""
Observius demo — two live browser-automation tasks with rich terminal output.

Run from the repo root before opening the dashboard:

    python demo.py

Results are saved to intel_report.json so the dashboard can read them.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.table import Table

load_dotenv(".env", override=True)

from computeruse import ComputerUse  # noqa: E402

console = Console()

TASKS = [
    {
        "description": "Top 5 Hacker News posts",
        "url": "https://news.ycombinator.com",
        "task": "Get the titles and point counts of the top 5 posts on the front page.",
        "schema": {"items": "list[str]", "details": "list[str]"},
    },
    {
        "description": "Top 5 trending GitHub repos",
        "url": "https://github.com/trending",
        "task": "Get the repository names and one-line descriptions of the top 5 trending repos today.",
        "schema": {"items": "list[str]", "details": "list[str]"},
    },
]


def run_demo() -> None:
    cu = ComputerUse()
    records: list[dict] = []

    for i, t in enumerate(TASKS, 1):
        console.print(f"\n[bold cyan]Task {i}/{len(TASKS)}:[/] {t['description']}")
        console.print(f"[dim]  {t['url']}[/]")

        r = None
        with console.status("[yellow]Running…[/]", spinner="dots"):
            try:
                r = cu.run_task(url=t["url"], task=t["task"], output_schema=t["schema"])
            except Exception as e:
                console.print(f"[bold red]  ✗ Error:[/] {e}")
                records.append({"id": i, "url": t["url"], "description": t["description"],
                                 "success": False, "steps": 0, "duration_ms": 0, "error": str(e)})
                continue

        if r.success:
            console.print(f"[bold green]  ✓ {r.duration_ms/1000:.1f}s  {r.steps} steps[/]")
            tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0, 1))
            tbl.add_column("Item",   style="cyan",  max_width=58)
            tbl.add_column("Detail", style="green", max_width=20)
            items   = (r.result or {}).get("items",   [])
            details = (r.result or {}).get("details", [])
            for item, detail in zip(items, details + [""] * len(items)):
                tbl.add_row(item, detail)
            console.print(tbl)
        else:
            console.print(f"[bold red]  ✗ Failed:[/] {r.error}")

        records.append({"id": i, "url": t["url"], "description": t["description"],
                        "success": r.success, "steps": r.steps,
                        "duration_ms": r.duration_ms, "result": r.result})

    # Summary
    console.rule("[bold]Summary[/]")
    tbl = Table(box=box.ROUNDED, header_style="bold white")
    tbl.add_column("Task");  tbl.add_column("Status", justify="center")
    tbl.add_column("Duration", justify="right");  tbl.add_column("Steps", justify="right")
    for rec in records:
        tbl.add_row(rec["description"],
                    "[bold green]✓ OK[/]" if rec["success"] else "[bold red]✗ FAIL[/]",
                    f"{rec['duration_ms']/1000:.1f}s", str(rec["steps"]))
    console.print(tbl)

    Path("intel_report.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "tasks": records}, indent=2)
    )
    console.print("\n[bold green]✓ Report saved to intel_report.json[/]")


if __name__ == "__main__":
    run_demo()
