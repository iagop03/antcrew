"""antcrew cost — show aggregate LLM spend from a TraceLog database."""
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console


@app.command(name="cost")
def cost_cmd(
    db: Optional[Path] = typer.Argument(
        None,
        help="TraceLog SQLite file. Defaults to ~/.antcrew/trace.db",
    ),
    team: Optional[str] = typer.Option(None, "--team", "-t", help="Filter by team name."),
    since: Optional[int] = typer.Option(
        None, "--since", "-n",
        help="Limit to runs from the last N days.",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show aggregate LLM cost from a TraceLog database.

    \b
    Examples:
        antcrew cost                       # default ~/.antcrew/trace.db
        antcrew cost runs.db               # custom DB
        antcrew cost --team fullstack      # filter by team
        antcrew cost --since 7             # last 7 days
        antcrew cost --json | jq '.total_cost_usd'
    """
    from rich.table import Table

    trace_path = db or (Path.home() / ".antcrew" / "trace.db")
    if not trace_path.exists():
        if output_json:
            typer.echo(_json.dumps({"error": "no trace database found", "path": str(trace_path)}))
        else:
            console.print(
                f"[yellow]No trace database found at[/] [cyan]{trace_path}[/]\n"
                "[dim]Run with --trace to record: antcrew run ... --trace ~/.antcrew/trace.db[/dim]"
            )
        raise typer.Exit(0)

    since_str: Optional[str] = None
    if since is not None:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=since)
        since_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    from antcrew.trace import TraceLog
    tl = TraceLog(str(trace_path))
    stats = tl.get_stats(team=team, since=since_str)
    tl.close()

    if output_json:
        typer.echo(_json.dumps(stats, indent=2, default=str))
        return

    # ── Rich display ───────────────────────────────────────────────────────────
    filter_label = ""
    if team:
        filter_label += f"  [dim]team={team}[/dim]"
    if since:
        filter_label += f"  [dim]last {since}d[/dim]"

    console.print(f"\n[bold green]antcrew cost[/]  [cyan]{trace_path.name}[/]{filter_label}\n")

    total = stats["total_runs"]
    if total == 0:
        console.print("[dim]No runs recorded yet.[/dim]\n")
        return

    done = stats["done_runs"]
    err  = stats["error_runs"]
    console.print(
        f"  [bold]Runs:[/]        {total}  "
        f"([green]{done} done[/]  [red]{err} errors[/])\n"
        f"  [bold]Total cost:[/]  [cyan]${stats['total_cost_usd']:.4f}[/]\n"
        f"  [bold]Avg cost:[/]    [cyan]${stats['avg_cost_usd']:.4f}[/]  per run\n"
        f"  [bold]Tokens:[/]      {stats['total_input_tokens']:,} in  "
        f"{stats['total_output_tokens']:,} out\n"
    )

    if stats["first_run"]:
        console.print(
            f"  [dim]First run:[/dim]  {stats['first_run']}\n"
            f"  [dim]Last run:[/dim]   {stats['last_run']}\n"
        )

    # ── Per-team breakdown ─────────────────────────────────────────────────────
    by_team = stats.get("by_team") or []
    if len(by_team) > 1:
        tbl = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
        tbl.add_column("Team",      style="cyan")
        tbl.add_column("Runs",      style="white",  justify="right")
        tbl.add_column("Done",      style="green",  justify="right")
        tbl.add_column("Cost (USD)",style="yellow", justify="right")
        for row in by_team:
            tbl.add_row(
                row["team"] or "—",
                str(row["runs"]),
                str(row["done"]),
                f"${float(row['cost_usd']):.4f}",
            )
        console.print(tbl)
        console.print()
