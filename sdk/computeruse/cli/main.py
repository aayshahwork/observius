"""
Command-line interface for the ComputerUse SDK.

Entry point registered in pyproject.toml as:
    [tool.poetry.scripts]
    computeruse = "computeruse.cli.main:cli"

Usage:
    computeruse run --url https://example.com --task "Get the page title"
    computeruse run --url https://example.com --task "..." --no-headless
    computeruse compile abc123
    computeruse replay login-flow --params '{"email":"test@test.com"}'
    computeruse open replays/abc123.html
    computeruse sessions
    computeruse sessions --delete example.com
    computeruse version
"""

from __future__ import annotations

import json
import re
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import computeruse

# stdout console for normal output; stderr console for errors.
console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """ComputerUse — automate any web workflow from the command line."""


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--url", required=True, help="Starting URL for the task.")
@click.option("--task", required=True, help="Natural-language description of what to do.")
@click.option(
    "--username",
    default=None,
    show_default=False,
    help="Login username (optional). Must be used together with --password.",
)
@click.option(
    "--password",
    default=None,
    show_default=False,
    help="Login password (optional). Must be used together with --username.",
)
@click.option(
    "--schema",
    default=None,
    show_default=False,
    metavar="JSON",
    help=(
        'JSON object mapping field names to types, e.g. \'{"price":"float","name":"str"}\'. '
        "Supported types: str, int, float, bool, list[T], dict[str, T]."
    ),
)
@click.option(
    "--output",
    default="result.json",
    show_default=True,
    help="File path where the full JSON result will be saved.",
)
@click.option(
    "--no-headless",
    "no_headless",
    is_flag=True,
    default=False,
    help="Show the browser window (useful for debugging).",
)
def run(
    url: str,
    task: str,
    username: Optional[str],
    password: Optional[str],
    schema: Optional[str],
    output: str,
    no_headless: bool,
) -> None:
    """Run a browser automation task.

    \b
    Examples:

    \b
        # Simple task
        computeruse run --url https://example.com --task "Get the page title"

    \b
        # Extract structured data with a schema
        computeruse run \\
          --url https://news.ycombinator.com \\
          --task "Get the top 5 post titles" \\
          --schema '{"titles":"list[str]"}'

    \b
        # Authenticated task
        computeruse run \\
          --url https://github.com/login \\
          --task "Star the repo anthropics/anthropic-sdk-python" \\
          --username alice \\
          --password hunter2

    \b
        # Visible browser (debugging)
        computeruse run --url https://example.com --task "..." --no-headless
    """
    from computeruse import ComputerUse, ComputerUseError

    # ── Parse output schema ─────────────────────────────────────────────────
    output_schema: Optional[dict] = None
    if schema:
        try:
            output_schema = json.loads(schema)
            if not isinstance(output_schema, dict):
                raise ValueError("Schema must be a JSON object, not an array or scalar.")
        except (json.JSONDecodeError, ValueError) as exc:
            err_console.print(f"[bold red]Invalid --schema:[/bold red] {exc}")
            err_console.print('  Expected a JSON object, e.g. \'{"price":"float","titles":"list[str]"}\'')
            sys.exit(1)

    # ── Build credentials dict ───────────────────────────────────────────────
    credentials: Optional[dict] = None
    if username or password:
        if not (username and password):
            err_console.print("[bold red]Error:[/bold red] " "--username and --password must both be provided.")
            sys.exit(1)
        credentials = {"username": username, "password": password}

    # ── Print task summary panel ─────────────────────────────────────────────
    summary = Table.grid(padding=(0, 2))
    summary.add_row("[dim]URL[/dim]", f"[cyan]{url}[/cyan]")
    summary.add_row("[dim]Task[/dim]", task)
    if credentials:
        summary.add_row("[dim]Auth[/dim]", "[yellow]credentials provided[/yellow]")
    if output_schema:
        summary.add_row("[dim]Schema[/dim]", str(output_schema))
    summary.add_row(
        "[dim]Browser[/dim]",
        "[yellow]visible[/yellow]" if no_headless else "headless",
    )
    console.print(Panel(summary, title="[bold]ComputerUse Task[/bold]", border_style="blue"))

    # ── Execute ──────────────────────────────────────────────────────────────
    cu = ComputerUse(headless=not no_headless)

    with console.status("[bold cyan]Running task…[/bold cyan]", spinner="dots"):
        try:
            result = cu.run_task(
                url=url,
                task=task,
                credentials=credentials,
                output_schema=output_schema,
            )
        except ComputerUseError as exc:
            err_console.print(f"\n[bold red]Task error:[/bold red] {exc}")
            sys.exit(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user.[/yellow]")
            sys.exit(130)

    # ── Display result ───────────────────────────────────────────────────────
    _print_result(result)

    # ── Save output file ─────────────────────────────────────────────────────
    output_path = Path(output)
    try:
        payload = result.to_dict()
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"\n[dim]Full result saved to[/dim] [green]{output_path}[/green]")
    except OSError as exc:
        err_console.print(f"[bold red]Could not write output file:[/bold red] {exc}")

    sys.exit(0 if result.success else 1)


# ---------------------------------------------------------------------------
# open (was "replay" — renamed so "replay" can be used for workflow execution)
# ---------------------------------------------------------------------------


@cli.command("open")
@click.argument("replay_file", type=click.Path(exists=True, dir_okay=False))
def open_file(replay_file: str) -> None:
    """Open a replay file in the default browser.

    REPLAY_FILE is the path to a .json or .html replay artifact produced
    by a previous task run.

    \b
    Example:
        computeruse open replays/abc123.html
    """
    path = Path(replay_file).resolve()
    suffix = path.suffix.lower()

    if suffix not in {".json", ".html"}:
        err_console.print(f"[bold red]Unsupported file type '{suffix}'.[/bold red] " "Expected .json or .html")
        sys.exit(1)

    url = path.as_uri()
    console.print(f"Opening replay: [cyan]{path}[/cyan]")
    webbrowser.open(url)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--delete",
    "delete_domain",
    default=None,
    metavar="DOMAIN",
    help="Delete the saved session for DOMAIN.",
)
@click.option(
    "--dir",
    "session_dir",
    default="./sessions",
    show_default=True,
    help="Directory to scan for saved sessions.",
)
def sessions(delete_domain: Optional[str], session_dir: str) -> None:
    """List or manage saved browser sessions.

    Sessions are created automatically when credentials are passed to
    run_task. They store cookies and localStorage so subsequent runs on
    the same domain skip the login step entirely.

    \b
    Examples:
        computeruse sessions
        computeruse sessions --delete example.com
    """
    from computeruse.session_manager import SessionManager

    manager = SessionManager(storage_dir=session_dir)

    # ── Delete mode ──────────────────────────────────────────────────────────
    if delete_domain:
        deleted = manager.delete_session(delete_domain)
        if deleted:
            console.print(f"[green]✓[/green] Deleted session for [cyan]{delete_domain}[/cyan]")
        else:
            console.print(f"[yellow]No session found for[/yellow] [cyan]{delete_domain}[/cyan]")
        return

    # ── List mode ────────────────────────────────────────────────────────────
    domain_list = manager.list_sessions()

    if not domain_list:
        console.print(
            f"[yellow]No saved sessions in[/yellow] [dim]{session_dir}[/dim]\n\n"
            "Sessions are created automatically when you pass [cyan]credentials[/cyan] "
            "to [cyan]run_task()[/cyan]."
        )
        return

    table = Table(
        "#",
        "Domain",
        "Session File",
        title=f"Saved Sessions  [dim]({session_dir})[/dim]",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=False,
    )

    for i, domain in enumerate(domain_list, start=1):
        # SessionManager sanitises the domain for the filename; reconstruct the
        # expected path for display only — the manager handles the real lookup.
        session_files = list(Path(session_dir).glob("*.json")) if Path(session_dir).exists() else []
        # Find the first file whose stored domain matches.
        file_display = "[dim](unknown)[/dim]"
        for sf in session_files:
            try:
                stored = json.loads(sf.read_text(encoding="utf-8")).get("domain", "")
                if stored == domain:
                    file_display = str(sf)
                    break
            except (OSError, json.JSONDecodeError):
                continue

        table.add_row(str(i), f"[cyan]{domain}[/cyan]", file_display)

    console.print(table)
    console.print("\n[dim]To delete a session:[/dim] " "[cyan]computeruse sessions --delete <domain>[/cyan]")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print the installed ComputerUse SDK version."""
    console.print(f"[bold]ComputerUse[/bold] version [cyan]{computeruse.__version__}[/cyan]")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _print_result(result) -> None:  # type: ignore[no-untyped-def]
    """Render a TaskResult to the terminal with Rich formatting.

    Prints a summary panel (status, task ID, steps, duration, error and
    replay paths) followed by an extracted-data table when result.result
    is populated.
    """
    if result.success:
        icon = "[bold green]✓ COMPLETED[/bold green]"
    else:
        icon = "[bold red]✗ FAILED[/bold red]"

    meta = Table.grid(padding=(0, 2))
    meta.add_row("[dim]Status[/dim]", icon)
    meta.add_row("[dim]Task ID[/dim]", f"[dim]{result.task_id}[/dim]")
    meta.add_row("[dim]Steps[/dim]", str(result.steps))
    meta.add_row("[dim]Duration[/dim]", f"{result.duration_ms / 1000:.2f}s")

    if result.error:
        meta.add_row("[dim]Error[/dim]", f"[red]{result.error}[/red]")
    if result.replay_path:
        meta.add_row("[dim]Replay[/dim]", f"[dim]{result.replay_path}[/dim]")
    if result.replay_url:
        meta.add_row("[dim]Replay URL[/dim]", result.replay_url)

    border = "green" if result.success else "red"
    console.print(Panel(meta, title="[bold]Result[/bold]", border_style=border))

    # Pretty-print extracted data when a schema was used.
    if result.result:
        data_table = Table(
            "Field",
            "Value",
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
            show_lines=False,
        )
        for key, val in result.result.items():
            display_val = str(val)
            if len(display_val) > 80:
                display_val = display_val[:77] + "…"
            data_table.add_row(f"[cyan]{key}[/cyan]", display_val)

        console.print(Panel(data_table, title="[bold]Extracted Data[/bold]", border_style="cyan"))


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--data-dir",
    default=".pokant",
    show_default=True,
    help="Data directory.",
)
def info(data_dir: str) -> None:
    """Show summary of Pokant run data."""
    base = Path(data_dir)
    runs_dir = base / "runs"
    screenshots_dir = base / "screenshots"

    # -- Load runs ---------------------------------------------------------
    runs: list[dict] = []
    if runs_dir.is_dir():
        for f in runs_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "task_id" in data:
                    runs.append(data)
            except (OSError, json.JSONDecodeError):
                continue

    total = len(runs)
    completed = sum(1 for r in runs if r.get("status") == "completed")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    timeout = sum(
        1 for r in runs
        if r.get("error_category") == "timeout" or r.get("status") == "timeout"
    )
    other = total - completed - failed - timeout
    total_cost_cents = sum(r.get("cost_cents", 0) for r in runs)

    # -- Screenshot stats --------------------------------------------------
    ss_count = 0
    ss_bytes = 0
    if screenshots_dir.is_dir():
        for img in screenshots_dir.rglob("*.png"):
            ss_count += 1
            try:
                ss_bytes += img.stat().st_size
            except OSError:
                pass

    if ss_bytes >= 1_048_576:
        ss_size = f"{ss_bytes / 1_048_576:.1f} MB"
    elif ss_bytes >= 1024:
        ss_size = f"{ss_bytes / 1024:.1f} KB"
    else:
        ss_size = f"{ss_bytes} B"

    # -- Render output -----------------------------------------------------
    from rich.table import Table  # noqa: E402 (already imported at top, but fine)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()

    status_parts = [f"{completed} completed", f"{failed} failed", f"{timeout} timeout"]
    if other:
        status_parts.append(f"{other} other")

    grid.add_row("Runs", f"{total} ({', '.join(status_parts)})")

    if total:
        success_rate = completed / total * 100
        grid.add_row("Success rate", f"{success_rate:.1f}%")

    grid.add_row("Total cost", f"${total_cost_cents / 100:.2f}")
    grid.add_row("Screenshots", f"{ss_count} files ({ss_size})")

    console.print(
        Panel(grid, title=f"[bold]Pokant[/bold] [dim]{data_dir}/[/dim]", border_style="cyan")
    )


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--data-dir",
    default=".pokant",
    show_default=True,
    help="Data directory.",
)
@click.option(
    "--port",
    default=8080,
    show_default=True,
    help="Port to serve on.",
)
def dashboard(data_dir: str, port: int) -> None:
    """Launch local debugging dashboard."""
    try:
        from computeruse.dashboard import create_app  # noqa: F811
        import uvicorn  # noqa: F811
    except ImportError:
        err_console.print("[bold red]Dashboard requires extra dependencies.[/bold red]")
        err_console.print("Install with:  [cyan]pip install pokant\\[dashboard][/cyan]")
        raise SystemExit(1)

    app = create_app(data_dir)
    console.print(f"[bold]Pokant Dashboard[/bold]: [cyan]http://localhost:{port}[/cyan]")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


def _parse_duration(value: str) -> int:
    """Parse a human duration string (e.g. '7d', '24h', '30m') to seconds."""
    m = re.fullmatch(r"(\d+)\s*([dhm])", value.strip().lower())
    if not m:
        raise click.BadParameter(
            f"Invalid duration '{value}'. Use e.g. 7d, 24h, 30m.",
            param_hint="'--older-than'",
        )
    amount = int(m.group(1))
    unit = m.group(2)
    multipliers = {"d": 86400, "h": 3600, "m": 60}
    return amount * multipliers[unit]


@cli.command()
@click.option(
    "--data-dir",
    default=".pokant",
    show_default=True,
    help="Data directory.",
)
@click.option(
    "--older-than",
    default="7d",
    show_default=True,
    help="Delete runs older than this (e.g. 7d, 24h, 30m).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be deleted without deleting.",
)
def clean(data_dir: str, older_than: str, dry_run: bool) -> None:
    """Delete old run data."""
    base = Path(data_dir)
    runs_dir = base / "runs"
    screenshots_dir = base / "screenshots"
    replays_dir = base / "replays"

    threshold_seconds = _parse_duration(older_than)
    now = datetime.now(timezone.utc)

    if not runs_dir.is_dir():
        console.print("[yellow]No runs directory found.[/yellow]")
        return

    to_delete: list[dict] = []

    for f in runs_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "task_id" not in data:
            continue

        created_at = data.get("completed_at") or data.get("created_at")
        if not created_at:
            continue

        try:
            ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        age_seconds = (now - ts).total_seconds()
        if age_seconds > threshold_seconds:
            to_delete.append({"file": f, "data": data})

    if not to_delete:
        console.print(f"[green]No runs older than {older_than} found.[/green]")
        return

    label = "[bold yellow]DRY RUN[/bold yellow] " if dry_run else ""

    for item in to_delete:
        task_id = item["data"]["task_id"]
        run_file: Path = item["file"]
        ss_dir = screenshots_dir / task_id
        replay_file = replays_dir / f"{task_id}.html"

        parts = [str(run_file)]
        if ss_dir.is_dir():
            parts.append(str(ss_dir) + "/")
        if replay_file.is_file():
            parts.append(str(replay_file))

        console.print(f"  {label}[red]delete[/red] {', '.join(parts)}")

        if not dry_run:
            try:
                run_file.unlink(missing_ok=True)
            except OSError:
                pass
            if ss_dir.is_dir():
                import shutil

                shutil.rmtree(ss_dir, ignore_errors=True)
            if replay_file.is_file():
                try:
                    replay_file.unlink(missing_ok=True)
                except OSError:
                    pass

    action = "Would delete" if dry_run else "Deleted"
    console.print(f"\n[bold]{action} {len(to_delete)} run(s).[/bold]")


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


@cli.command("compile")
@click.argument("task_id")
@click.option("--data-dir", default=".pokant", show_default=True, help="Data directory.")
@click.option("--name", default=None, help="Workflow name (defaults to task ID).")
@click.option("--params", default=None, help="Comma-separated parameter names, e.g. 'email,password'.")
@click.option("--script/--no-script", default=True, help="Generate a Playwright script alongside the workflow.")
def compile_workflow(task_id: str, data_dir: str, name: Optional[str], params: Optional[str], script: bool) -> None:
    """Compile a successful run into a replayable workflow.

    TASK_ID is the ID of a completed run in the data directory.

    \b
    Examples:
        computeruse compile abc123
        computeruse compile abc123 --name "login-flow" --params "email,password"
        computeruse compile abc123 --no-script
    """
    try:
        from computeruse.compiler import CompilationError, WorkflowCompiler
    except ImportError:
        err_console.print(
            "[bold red]Workflow compiler not available.[/bold red]\n"
            "Install the full SDK or check that compiler.py is present."
        )
        sys.exit(1)

    run_file = Path(data_dir) / "runs" / f"{task_id}.json"
    if not run_file.is_file():
        err_console.print(f"[bold red]Run not found:[/bold red] {run_file}")
        sys.exit(1)

    parameter_names = [p.strip() for p in params.split(",")] if params else None
    compiler = WorkflowCompiler()

    try:
        workflow = compiler.compile_from_run(
            str(run_file),
            name=name,
            parameter_names=parameter_names,
        )
    except CompilationError as exc:
        err_console.print(f"[bold red]Compilation failed:[/bold red] {exc}")
        sys.exit(1)

    workflows_dir = str(Path(data_dir) / "workflows")
    wf_path = compiler.save_workflow(workflow, output_dir=workflows_dir)

    # Summary
    grid = Table.grid(padding=(0, 2))
    grid.add_row("[dim]Workflow[/dim]", f"[cyan]{workflow.name}[/cyan]")
    grid.add_row("[dim]Steps[/dim]", str(len(workflow.steps)))
    if workflow.parameters:
        grid.add_row("[dim]Parameters[/dim]", ", ".join(workflow.parameters))
    grid.add_row("[dim]Saved to[/dim]", f"[green]{wf_path}[/green]")

    if script:
        script_path = str(Path(workflows_dir) / f"{workflow.name}.py")
        try:
            compiler.generate_playwright_script(workflow, output_path=script_path)
            grid.add_row("[dim]Script[/dim]", f"[green]{script_path}[/green]")
        except CompilationError as exc:
            grid.add_row("[dim]Script[/dim]", f"[yellow]Failed: {exc}[/yellow]")

    console.print(Panel(grid, title="[bold]Compiled Workflow[/bold]", border_style="green"))


# ---------------------------------------------------------------------------
# replay (workflow execution)
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("workflow_id")
@click.option("--data-dir", default=".pokant", show_default=True, help="Data directory.")
@click.option("--params", default=None, help='JSON parameters: \'{"email":"test@test.com"}\'')
@click.option("--headless/--no-headless", default=True, help="Run browser headless (default) or visible.")
@click.option("--budget", default=50.0, show_default=True, help="Max cost in cents for AI fallback.")
@click.option("--no-verify", is_flag=True, default=False, help="Disable post-action verification.")
def replay(
    workflow_id: str,
    data_dir: str,
    params: Optional[str],
    headless: bool,
    budget: float,
    no_verify: bool,
) -> None:
    """Replay a compiled workflow against a live browser.

    WORKFLOW_ID is the name of a compiled workflow in the data directory.

    \b
    Examples:
        computeruse replay login-flow
        computeruse replay login-flow --params '{"email":"test@test.com"}'
        computeruse replay login-flow --no-headless --budget 10
    """
    import asyncio

    try:
        from computeruse.replay_executor import ReplayConfig, ReplayExecutor, ReplayResult
    except ImportError:
        err_console.print(
            "[bold red]Replay executor not available.[/bold red]\n"
            "Install the full SDK or check that replay_executor.py is present."
        )
        sys.exit(1)

    wf_path = Path(data_dir) / "workflows" / f"{workflow_id}.json"
    if not wf_path.is_file():
        err_console.print(f"[bold red]Workflow not found:[/bold red] {wf_path}")
        sys.exit(1)

    # Parse params JSON
    param_dict: Dict[str, str] = {}
    if params:
        try:
            param_dict = json.loads(params)
            if not isinstance(param_dict, dict):
                raise ValueError("Must be a JSON object")
            if not all(isinstance(v, str) for v in param_dict.values()):
                raise ValueError("All values must be strings")
        except (json.JSONDecodeError, ValueError) as exc:
            err_console.print(f"[bold red]Invalid --params:[/bold red] {exc}")
            sys.exit(1)

    config = ReplayConfig(
        headless=headless,
        max_cost_cents=budget,
        verify_actions=not no_verify,
        output_dir=data_dir,
    )

    async def _run() -> ReplayResult:
        from playwright.async_api import async_playwright

        executor = ReplayExecutor(config=config)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            page = await browser.new_page()
            try:
                result = await executor.execute_from_file(
                    str(wf_path), params=param_dict, page=page
                )
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass
        return result

    with console.status("[bold cyan]Replaying workflow...[/bold cyan]", spinner="dots"):
        try:
            result = asyncio.run(_run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user.[/yellow]")
            sys.exit(130)
        except Exception as exc:
            err_console.print(f"\n[bold red]Replay error:[/bold red] {exc}")
            sys.exit(1)

    # Result summary
    icon = "[bold green]PASSED[/bold green]" if result.success else "[bold red]FAILED[/bold red]"
    grid = Table.grid(padding=(0, 2))
    grid.add_row("[dim]Status[/dim]", icon)
    grid.add_row("[dim]Steps[/dim]", f"{result.steps_executed}/{result.steps_total}")
    grid.add_row("[dim]Duration[/dim]", f"{result.duration_ms / 1000:.2f}s")
    grid.add_row("[dim]Cost[/dim]", f"${result.cost_cents / 100:.4f}")

    tier_parts = []
    if result.steps_deterministic:
        tier_parts.append(f"{result.steps_deterministic} deterministic")
    if result.steps_healed:
        tier_parts.append(f"{result.steps_healed} healed")
    if result.steps_ai_recovered:
        tier_parts.append(f"{result.steps_ai_recovered} AI-recovered")
    if tier_parts:
        grid.add_row("[dim]Tiers[/dim]", ", ".join(tier_parts))

    if result.error:
        grid.add_row("[dim]Error[/dim]", f"[red]{result.error}[/red]")

    border = "green" if result.success else "red"
    console.print(Panel(grid, title="[bold]Replay Result[/bold]", border_style=border))
    sys.exit(0 if result.success else 1)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


# (existing _print_result is above)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
