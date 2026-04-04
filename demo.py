"""
Pokant demo — multi-source intelligence pipeline as a single tracked task.

Run from the repo root before opening the dashboard:

    python demo.py

All sub-tasks are recorded as one unified task on the dashboard.
Click the task to see the step timeline broken down by sub-task.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.table import Table

load_dotenv(".env", override=True)

from computeruse import ComputerUse  # noqa: E402
from computeruse.models import StepData  # noqa: E402

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
    # Create client WITHOUT pokant config so it doesn't auto-report each sub-task.
    # We'll report the combined task manually at the end.
    cu = ComputerUse(pokant_api_url="", pokant_api_key="")

    pokant_api_url = os.environ.get("POKANT_API_URL", "")
    pokant_api_key = os.environ.get("POKANT_API_KEY", "")

    if pokant_api_url and pokant_api_key:
        console.print(f"[green]Dashboard reporting enabled[/green] (single combined task) -> {pokant_api_url}\n")
    else:
        console.print("[dim]Dashboard reporting disabled (set POKANT_API_URL and POKANT_API_KEY in .env)[/dim]\n")

    records: list[dict] = []
    all_steps: list[StepData] = []
    total_cost_cents: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    start_time = time.monotonic()
    created_at = datetime.now(timezone.utc)
    had_failure = False
    last_error: str | None = None

    for i, t in enumerate(TASKS, 1):
        console.print(f"\n[bold cyan]Task {i}/{len(TASKS)}:[/] {t['description']}")
        console.print(f"[dim]  {t['url']}[/]")

        r = None
        with console.status("[yellow]Running...[/]", spinner="dots"):
            try:
                r = cu.run_task(url=t["url"], task=t["task"], output_schema=t["schema"])
            except Exception as e:
                console.print(f"[bold red]  x Error:[/] {e}")
                records.append({"id": i, "url": t["url"], "description": t["description"],
                                "success": False, "steps": 0, "duration_ms": 0, "error": str(e)})
                had_failure = True
                last_error = str(e)
                continue

        # Merge this sub-task's steps into the combined timeline
        for step in r.step_data:
            step_copy = step.model_copy() if hasattr(step, "model_copy") else step
            # Re-number sequentially across all sub-tasks
            step_copy.step_number = len(all_steps) + 1
            # Tag with sub-task context so the dashboard can group them
            step_copy.context = {
                "type": "subtask",
                "subtask_name": t["description"],
                "subtask_number": i,
                "subtask_total": len(TASKS),
                "subtask_url": t["url"],
            }
            all_steps.append(step_copy)

        total_cost_cents += r.cost_cents
        total_tokens_in += r.total_tokens_in
        total_tokens_out += r.total_tokens_out

        if r.success:
            console.print(f"[bold green]  v {r.duration_ms/1000:.1f}s  {r.steps} steps[/]")
            tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0, 1))
            tbl.add_column("Item", style="cyan", max_width=58)
            tbl.add_column("Detail", style="green", max_width=20)
            items = (r.result or {}).get("items", [])
            details = (r.result or {}).get("details", [])
            for item, detail in zip(items, details + [""] * len(items)):
                tbl.add_row(item, detail)
            console.print(tbl)
        else:
            console.print(f"[bold red]  x Failed:[/] {r.error}")
            had_failure = True
            last_error = r.error

        records.append({"id": i, "url": t["url"], "description": t["description"],
                        "success": r.success, "steps": r.steps,
                        "duration_ms": r.duration_ms, "result": r.result})

    total_duration_ms = int((time.monotonic() - start_time) * 1000)

    # Summary
    console.rule("[bold]Summary[/]")
    tbl = Table(box=box.ROUNDED, header_style="bold white")
    tbl.add_column("Task")
    tbl.add_column("Status", justify="center")
    tbl.add_column("Duration", justify="right")
    tbl.add_column("Steps", justify="right")
    for rec in records:
        tbl.add_row(rec["description"],
                    "[bold green]v OK[/]" if rec["success"] else "[bold red]x FAIL[/]",
                    f"{rec['duration_ms']/1000:.1f}s", str(rec["steps"]))
    console.print(tbl)

    Path("intel_report.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "tasks": records}, indent=2)
    )
    console.print("\n[bold green]v Report saved to intel_report.json[/]")

    # Report as ONE combined task to Pokant dashboard
    if pokant_api_url and pokant_api_key:
        try:
            from computeruse._reporting import _report_to_api_sync
            from computeruse.analyzer import AnalysisConfig, RunAnalyzer

            # Run analysis on combined steps
            analysis = None
            try:
                task_desc = "Multi-source intelligence pipeline: " + ", ".join(
                    t["description"] for t in TASKS
                )
                status = "failed" if had_failure else "completed"
                config = AnalysisConfig(
                    llm_api_key=os.environ.get("ANTHROPIC_API_KEY"),
                )
                analyzer = RunAnalyzer(config)
                result = analyzer.analyze_sync(
                    all_steps, status, last_error, task_desc,
                )
                analysis = {
                    "summary": result.summary,
                    "primary_suggestion": result.primary_suggestion,
                    "wasted_steps": result.wasted_steps,
                    "wasted_cost_cents": result.wasted_cost_cents,
                    "tiers_executed": list(result.tiers_executed),
                    "findings": [
                        {
                            "tier": f.tier,
                            "category": f.category,
                            "summary": f.summary,
                            "suggestion": f.suggestion,
                            "confidence": f.confidence,
                        }
                        for f in result.findings
                    ],
                }
            except Exception:
                pass

            import uuid
            ok = _report_to_api_sync(
                api_url=pokant_api_url,
                api_key=pokant_api_key,
                task_id=str(uuid.uuid4()),
                task_description="Multi-source intelligence pipeline: " + ", ".join(
                    t["description"] for t in TASKS
                ),
                status="failed" if had_failure else "completed",
                steps=all_steps,
                cost_cents=total_cost_cents,
                error_category=None,
                error_message=last_error,
                duration_ms=total_duration_ms,
                created_at=created_at,
                analysis=analysis,
            )
            if ok:
                console.print(f"[green]v Reported to dashboard as single task ({len(all_steps)} steps)[/]")
            else:
                console.print("[yellow]! Dashboard reporting failed[/]")
        except Exception as e:
            console.print(f"[yellow]! Dashboard reporting error: {e}[/]")


if __name__ == "__main__":
    run_demo()
