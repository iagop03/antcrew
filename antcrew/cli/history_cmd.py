"""History command."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel

from antcrew.cli._app import app, console


def _parse_since(since: str) -> str:
    """Return an ISO timestamp string from a human date or relative spec.

    Accepts:
        "YYYY-MM-DD"   → midnight UTC on that date
        "7d", "30d"    → N days ago from now (UTC)
    """
    import time as _time

    since = since.strip()
    if since.endswith("d") and since[:-1].isdigit():
        days = int(since[:-1])
        secs = _time.time() - days * 86400
        from datetime import datetime as _dt
        from datetime import timezone as _tz
        return _dt.fromtimestamp(secs, tz=_tz.utc).isoformat()
    # Assume YYYY-MM-DD
    return since + "T00:00:00+00:00"


@app.command(name="history")
def history_cmd(
    db: Path = typer.Argument(..., help="TraceLog SQLite file (created with --trace)."),
    team: Optional[str] = typer.Option(
        None, "--team", "-t", help="Filter runs to a specific team (dev, fullstack, …)."
    ),
    status: Optional[str] = typer.Option(
        None, "--status", "-s", help="Filter by status: done | error | all (default: all)."
    ),
    since: Optional[str] = typer.Option(
        None, "--since",
        help="Only show runs after this date/period.  "
             "Formats: YYYY-MM-DD or relative (e.g. 7d, 30d).",
    ),
    limit: int = typer.Option(
        50, "--limit", "-n", help="Maximum number of runs to display (default: 50)."
    ),
    stats: bool = typer.Option(
        False, "--stats", help="Show aggregate statistics panel only (no run table)."
    ),
    export: Optional[Path] = typer.Option(
        None, "--export", "-e", help="Export the filtered run list to a CSV file."
    ),
) -> None:
    """Browse TraceLog run history with aggregate statistics.

    Unlike 'antcrew trace' (which focuses on per-agent call detail for a
    single run), 'antcrew history' shows aggregate metrics across all runs:
    success rate, total cost, token usage, and a by-team breakdown.

    \b
    Show all runs with a statistics summary:
        antcrew history ~/.antcrew/trace.db

    \b
    Filter to dev team runs in the last 7 days:
        antcrew history ~/.antcrew/trace.db --team dev --since 7d

    \b
    Statistics only (no run table):
        antcrew history ~/.antcrew/trace.db --stats

    \b
    Export filtered runs to CSV:
        antcrew history ~/.antcrew/trace.db --since 30d --export runs.csv
    """
    from rich.table import Table

    from antcrew.trace import TraceLog

    if not db.exists():
        console.print(f"[red]TraceLog not found:[/] {db}")
        raise typer.Exit(1)

    tlog = TraceLog(db)

    since_iso: Optional[str] = None
    if since:
        try:
            since_iso = _parse_since(since)
        except Exception:
            console.print(f"[red]Invalid --since value:[/] {since!r}  (use YYYY-MM-DD or Nd)")
            raise typer.Exit(1)

    agg = tlog.get_stats(team=team, since=since_iso)
    runs = tlog.list_runs_filtered(
        team=team, status=status, since=since_iso, limit=limit
    )
    tlog.close()

    console.print(f"\n[bold green]antcrew history[/]  [cyan]{db}[/]\n")

    # ── No data ──────────────────────────────────────────────────────────────
    if agg["total_runs"] == 0:
        console.print("[dim]No runs found — run a pipeline with --trace to start recording.[/dim]\n")
        return

    # ── Statistics panel ─────────────────────────────────────────────────────
    total   = agg["total_runs"]
    done    = agg["done_runs"]
    error   = agg["error_runs"]
    pct_ok  = round(done / total * 100) if total else 0
    cost    = agg["total_cost_usd"]
    avg     = agg["avg_cost_usd"]
    in_tok  = agg["total_input_tokens"]
    out_tok = agg["total_output_tokens"]
    first   = (agg["first_run"] or "")[:16].replace("T", " ")
    last    = (agg["last_run"]  or "")[:16].replace("T", " ")

    total_tok = in_tok + out_tok
    tok_str   = f"{total_tok:,}" if total_tok < 1_000_000 else f"{total_tok/1_000_000:.1f}M"
    cost_str  = f"${cost:.4f}" if cost else "—"
    avg_str   = f"${avg:.4f}" if avg else "—"

    success_colour = "green" if pct_ok >= 80 else "yellow" if pct_ok >= 50 else "red"
    error_colour   = "red" if error else "dim"

    stats_lines = (
        f"  [bold]{total}[/] runs   "
        f"[{success_colour}]{done} done ({pct_ok}%)[/{success_colour}]   "
        f"[{error_colour}]{error} error{'s' if error != 1 else ''}[/{error_colour}]\n"
        f"  Total cost  : [cyan]{cost_str}[/]   Avg / run: [cyan]{avg_str}[/]\n"
        f"  Total tokens: [cyan]{tok_str}[/]   "
        f"  Period      : [dim]{first}[/dim] → [dim]{last}[/dim]"
    )

    console.print(Panel(stats_lines, title="Summary", border_style="blue"))

    # ── By-team breakdown ─────────────────────────────────────────────────────
    if agg["by_team"] and not team:
        team_tbl = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
        team_tbl.add_column("Team",    style="cyan",  no_wrap=True, min_width=12)
        team_tbl.add_column("Runs",    justify="right")
        team_tbl.add_column("Success", justify="right")
        team_tbl.add_column("Cost",    justify="right")

        for row in agg["by_team"]:
            t_done = int(row.get("done") or 0)
            t_runs = int(row.get("runs") or 0)
            t_pct  = round(t_done / t_runs * 100) if t_runs else 0
            t_cost = float(row.get("cost_usd") or 0.0)
            t_col  = "green" if t_pct >= 80 else "yellow" if t_pct >= 50 else "red"
            team_tbl.add_row(
                row["team"],
                str(t_runs),
                f"[{t_col}]{t_pct}%[/{t_col}]",
                f"${t_cost:.4f}" if t_cost else "—",
            )

        console.print(Panel(team_tbl, title="By team", border_style="dim"))

    if stats:
        console.print()
        return

    # ── Run table ─────────────────────────────────────────────────────────────
    if not runs:
        console.print("[dim]No runs match the current filters.[/dim]")
        console.print()
        return

    run_tbl = Table(
        show_header=True, header_style="bold dim", box=None, padding=(0, 1)
    )
    run_tbl.add_column("#",       style="dim",    no_wrap=True, width=4)
    run_tbl.add_column("Thread",  style="cyan",   no_wrap=True, max_width=22)
    run_tbl.add_column("Team",    style="yellow", no_wrap=True, width=10)
    run_tbl.add_column("Status",  no_wrap=True,   width=7)
    run_tbl.add_column("Cost",    justify="right", width=9)
    run_tbl.add_column("Started", style="dim",    no_wrap=True, width=17)
    run_tbl.add_column("Request", max_width=45)

    for i, r in enumerate(runs, 1):
        st = r["status"]
        st_str = (
            "[green]done[/]"  if st == "done"
            else "[red]error[/]" if st == "error"
            else f"[yellow]{st}[/]"
        )
        c = r.get("cost_usd") or 0.0
        c_str = f"${c:.4f}" if c else "—"
        started = (r["started_at"] or "")[:16].replace("T", " ")
        req = (r["request"] or "")[:45]
        run_tbl.add_row(str(i), r["thread_id"], r["team"], st_str, c_str, started, req)

    console.print(run_tbl)

    if len(runs) == limit:
        console.print(
            f"\n[dim]Showing {limit} most recent runs. "
            "Use --limit to see more.[/dim]"
        )

    # ── CSV export ────────────────────────────────────────────────────────────
    if export:
        import csv as _csv
        _fields = ["id", "thread_id", "team", "request", "status",
                   "cost_usd", "started_at", "ended_at"]
        with open(export, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(runs)
        console.print(f"\n[dim]Exported {len(runs)} run(s) → [cyan]{export}[/][/dim]")

    console.print()

