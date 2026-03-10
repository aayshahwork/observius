"""
Command-line interface for the ComputerUse SDK.

Entry point registered in pyproject.toml as:
    [tool.poetry.scripts]
    computeruse = "computeruse.cli.main:cli"

Usage:
    computeruse run --url https://example.com --task "Get the page title"
    computeruse run --url https://example.com --task "..." --no-headless
    computeruse sessions
    computeruse sessions --delete example.com
    computeruse replay replays/abc123.json
    computeruse version
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import computeruse
from computeruse import ComputerUse, ComputerUseError
from computeruse.config import settings
from computeruse.session_manager import SessionManager

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
    # ── Parse output schema ─────────────────────────────────────────────────
    output_schema: Optional[dict] = None
    if schema:
        try:
            output_schema = json.loads(schema)
            if not isinstance(output_schema, dict):
                raise ValueError("Schema must be a JSON object, not an array or scalar.")
        except (json.JSONDecodeError, ValueError) as exc:
            err_console.print(f"[bold red]Invalid --schema:[/bold red] {exc}")
            err_console.print(
                '  Expected a JSON object, e.g. \'{"price":"float","titles":"list[str]"}\''
            )
            sys.exit(1)

    # ── Build credentials dict ───────────────────────────────────────────────
    credentials: Optional[dict] = None
    if username or password:
        if not (username and password):
            err_console.print(
                "[bold red]Error:[/bold red] "
                "--username and --password must both be provided."
            )
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
        payload = json.loads(result.model_dump_json())
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"\n[dim]Full result saved to[/dim] [green]{output_path}[/green]")
    except OSError as exc:
        err_console.print(f"[bold red]Could not write output file:[/bold red] {exc}")

    sys.exit(0 if result.success else 1)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("replay_file", type=click.Path(exists=True, dir_okay=False))
def replay(replay_file: str) -> None:
    """Open a replay file in the default browser.

    REPLAY_FILE is the path to a .json or .html replay artifact produced
    by a previous task run.

    \b
    Example:
        computeruse replay replays/abc123.json
    """
    path = Path(replay_file).resolve()
    suffix = path.suffix.lower()

    if suffix not in {".json", ".html"}:
        err_console.print(
            f"[bold red]Unsupported file type '{suffix}'.[/bold red] "
            "Expected .json or .html"
        )
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
    default=settings.SESSION_DIR,
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
    manager = SessionManager(storage_dir=session_dir)

    # ── Delete mode ──────────────────────────────────────────────────────────
    if delete_domain:
        deleted = manager.delete_session(delete_domain)
        if deleted:
            console.print(
                f"[green]✓[/green] Deleted session for [cyan]{delete_domain}[/cyan]"
            )
        else:
            console.print(
                f"[yellow]No session found for[/yellow] [cyan]{delete_domain}[/cyan]"
            )
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
        "#", "Domain", "Session File",
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
    console.print(
        "\n[dim]To delete a session:[/dim] "
        "[cyan]computeruse sessions --delete <domain>[/cyan]"
    )


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print the installed ComputerUse SDK version."""
    console.print(
        f"[bold]ComputerUse[/bold] version [cyan]{computeruse.__version__}[/cyan]"
    )


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
            "Field", "Value",
            box=box.SIMPLE_HEAVY,
            header_style="bold cyan",
            show_lines=False,
        )
        for key, val in result.result.items():
            display_val = str(val)
            if len(display_val) > 80:
                display_val = display_val[:77] + "…"
            data_table.add_row(f"[cyan]{key}[/cyan]", display_val)

        console.print(
            Panel(data_table, title="[bold]Extracted Data[/bold]", border_style="cyan")
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
