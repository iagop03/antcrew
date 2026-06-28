"""Trace and replay commands."""
from __future__ import annotations

import csv
import io
import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel

from antcrew.cli._app import app, console, _MODEL_HELP, _TEAM_CHOICES
from antcrew.cli._shared import _build_team, _print_state
from antcrew.cli._run_helpers import _run_with_stream


# ── Serializers (used by --dump flag) ────────────────────────────────────────

def _to_json(runs: list[dict]) -> str:
    return _json.dumps(runs, indent=2, default=str) + "\n"


def _to_csv(runs: list[dict], *, include_calls: bool = False) -> str:
    buf = io.StringIO()
    run_fields = ["id", "thread_id", "team", "status", "cost_usd", "started_at", "ended_at", "request"]

    if not runs:
        csv.DictWriter(buf, fieldnames=run_fields).writeheader()
        return buf.getvalue()

    if not include_calls:
        w = csv.DictWriter(buf, fieldnames=run_fields, extrasaction="ignore")
        w.writeheader()
        for run in runs:
            w.writerow(run)
    else:
        call_fields = ["agent_name", "duration_ms", "input_tokens", "output_tokens", "call_cost_usd"]
        w = csv.DictWriter(buf, fieldnames=run_fields + call_fields, extrasaction="ignore")
        w.writeheader()
        for run in runs:
            calls = run.get("agent_calls") or []
            if not calls:
                w.writerow({**{f: run.get(f, "") for f in run_fields}, **{f: "" for f in call_fields}})
            else:
                for call in calls:
                    w.writerow({
                        **{f: run.get(f, "") for f in run_fields},
                        "agent_name":   call.get("agent_name", ""),
                        "duration_ms":  call.get("duration_ms", ""),
                        "input_tokens": call.get("input_tokens", ""),
                        "output_tokens": call.get("output_tokens", ""),
                        "call_cost_usd": call.get("cost_usd", ""),
                    })
    return buf.getvalue()

@app.command(name="trace")
def trace_cmd(
    db: Path = typer.Argument(..., help="TraceLog SQLite file (created with --trace)"),
    run_id: Optional[str] = typer.Option(
        None, "--run", "-r", help="Show agent calls for a specific run ID"
    ),
    thread: Optional[str] = typer.Option(
        None, "--thread", help="Show the latest run for a thread_id"
    ),
    show_call: Optional[int] = typer.Option(
        None, "--show-call",
        help="Show the full prompt + response for call N in the selected run (1-indexed). "
             "Requires --run or --thread. Only available when run was recorded with --full-trace.",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max runs to list"),
    prune: Optional[int] = typer.Option(
        None, "--prune",
        help="Delete runs older than N days and exit (0 = all past runs).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation when pruning"),
    dump: Optional[str] = typer.Option(
        None, "--dump",
        help="Dump runs to stdout in this format (json or csv) and exit.",
    ),
    dump_output: Optional[Path] = typer.Option(
        None, "--dump-output", "-o", help="Write --dump output to this file instead of stdout"
    ),
    dump_calls: bool = typer.Option(False, "--dump-calls", help="Include per-agent call detail in dump"),
    dump_since: Optional[int] = typer.Option(None, "--dump-since", help="Dump only runs from last N days"),
    dump_team: Optional[str] = typer.Option(None, "--dump-team", help="Filter dump by team name"),
) -> None:
    """Inspect a TraceLog SQLite file — list runs, show call detail, prune, or dump.

    \b
    List recent runs:
        antcrew trace ~/.antcrew/trace.db

    Show agent calls for a specific run:
        antcrew trace ~/.antcrew/trace.db --run <run_id>

    Show full prompt + response for call 2 (requires --full-trace at record time):
        antcrew trace ~/.antcrew/trace.db --run <run_id> --show-call 2

    Show latest run for a thread:
        antcrew trace ~/.antcrew/trace.db --thread sprint-1

    Delete runs older than 30 days:
        antcrew trace ~/.antcrew/trace.db --prune 30 --yes

    Dump all runs as JSON to stdout:
        antcrew trace ~/.antcrew/trace.db --dump json

    Dump filtered runs as CSV to file:
        antcrew trace ~/.antcrew/trace.db --dump csv --dump-output out.csv --dump-team dev
    """
    from antcrew.trace import TraceLog as _TraceLog
    from rich.table import Table

    if not db.exists():
        console.print(f"[red]File not found:[/] {db}")
        raise typer.Exit(1)

    tlog = _TraceLog(db)

    # --- prune mode ---
    if prune is not None:
        if not yes:
            typer.confirm(
                f"Delete all runs older than {prune} day(s) from {db}?", abort=True
            )
        deleted = tlog.prune(prune)
        tlog.close()
        console.print(f"[green]Deleted[/] {deleted} run{'s' if deleted != 1 else ''} from [cyan]{db}[/].")
        return

    # --- dump mode (JSON / CSV export) ---
    if dump is not None:
        import sys as _sys
        from datetime import timezone as _tz, timedelta as _td, datetime as _dt
        fmt = dump.lower().strip()
        if fmt not in ("json", "csv"):
            console.print(f"[red]Unknown dump format:[/] {dump!r}. Use json or csv.")
            raise typer.Exit(1)
        since_iso: Optional[str] = None
        if dump_since is not None:
            since_iso = (_dt.now(_tz.utc) - _td(days=dump_since)).isoformat()
        runs = tlog.list_runs_filtered(team=dump_team, since=since_iso, limit=limit)
        if dump_calls:
            for run in runs:
                run["agent_calls"] = tlog.get_calls(run["id"])
        tlog.close()
        text = _to_json(runs) if fmt == "json" else _to_csv(runs, include_calls=dump_calls)
        if dump_output:
            dump_output.write_text(text, encoding="utf-8")
            console.print(
                f"[green]Dumped[/] {len(runs)} run(s) → [cyan]{dump_output}[/] ({fmt.upper()})"
            )
        else:
            _sys.stdout.write(text)
        return

    # --- detail view (single run) ---
    target_run: Optional[dict] = None
    if run_id:
        target_run = tlog.get_run(run_id)
        if target_run is None:
            console.print(f"[red]Run not found:[/] {run_id}")
            raise typer.Exit(1)
    elif thread:
        target_run = tlog.get_run_by_thread(thread)
        if target_run is None:
            console.print(f"[red]No run found for thread:[/] {thread}")
            raise typer.Exit(1)

    if show_call is not None and target_run is None:
        console.print("[red]--show-call requires --run or --thread.[/]")
        raise typer.Exit(1)

    if show_call is not None and target_run is not None:
        calls = tlog.get_calls(target_run["id"])
        if show_call < 1 or show_call > len(calls):
            console.print(
                f"[red]Call {show_call} out of range.[/] "
                f"Run has {len(calls)} call{'s' if len(calls) != 1 else ''}."
            )
            raise typer.Exit(1)
        call = tlog.get_call_detail(calls[show_call - 1]["id"])
        tlog.close()
        _print_call_detail(target_run, call, show_call)
        return

    if target_run is not None:
        _print_trace_detail(target_run, tlog.get_calls(target_run["id"]))
        return

    # --- list view ---
    runs = tlog.list_runs(limit=limit)
    if not runs:
        console.print("[dim]No runs recorded yet.[/dim]")
        return

    tbl = Table(title=f"TraceLog — {db}", show_header=True, header_style="bold dim")
    tbl.add_column("Run ID",    style="dim",    no_wrap=True, max_width=12)
    tbl.add_column("Thread",    style="cyan",   no_wrap=True, max_width=20)
    tbl.add_column("Team",      style="yellow", no_wrap=True)
    tbl.add_column("Status",    no_wrap=True)
    tbl.add_column("Cost",      justify="right")
    tbl.add_column("Started",   style="dim",    no_wrap=True)
    tbl.add_column("Request",   max_width=40)

    for r in runs:
        status = r["status"]
        status_str = (
            f"[green]{status}[/green]"   if status == "done"
            else f"[red]{status}[/red]"  if status == "error"
            else f"[yellow]{status}[/yellow]"
        )
        cost = r["cost_usd"]
        cost_str = f"${cost:.4f}" if cost else "—"
        started = (r["started_at"] or "")[:19].replace("T", " ")
        tbl.add_row(
            r["id"][:8] + "…",
            r["thread_id"],
            r["team"],
            status_str,
            cost_str,
            started,
            r["request"][:40],
        )

    console.print(tbl)
    console.print("\n[dim]Use --run <id> or --thread <id> to inspect agent calls.[/dim]")


def _print_trace_detail(run: dict, calls: list[dict]) -> None:
    """Print a detailed view of a single run and its agent calls."""
    from rich.table import Table

    cost = run.get("cost_usd") or 0.0
    started = (run.get("started_at") or "")[:19].replace("T", " ")
    ended = (run.get("ended_at") or "")[:19].replace("T", " ")

    console.print(Panel(
        f"[bold]{run['request'][:120]}[/bold]\n\n"
        f"Thread:  [cyan]{run['thread_id']}[/cyan]\n"
        f"Team:    [yellow]{run['team']}[/yellow]\n"
        f"Status:  [{'green' if run['status'] == 'done' else 'red'}]{run['status']}[/]\n"
        f"Cost:    [cyan]${cost:.4f}[/cyan]\n"
        f"Started: [dim]{started}[/dim]   Ended: [dim]{ended}[/dim]",
        title=f"Run {run['id'][:8]}…",
        border_style="blue",
    ))

    if not calls:
        console.print("[dim]No agent calls recorded for this run.[/dim]")
        return

    has_full = any(c.get("prompt_full") for c in calls)

    tbl = Table(show_header=True, header_style="bold dim")
    tbl.add_column("#",          style="dim",    justify="right", no_wrap=True)
    tbl.add_column("Agent",      style="cyan",   no_wrap=True)
    tbl.add_column("Duration",   justify="right", no_wrap=True)
    tbl.add_column("In",         justify="right")
    tbl.add_column("Out",        justify="right")
    tbl.add_column("Cost",       justify="right")
    tbl.add_column("Prompt snippet", max_width=60)

    for i, c in enumerate(calls, 1):
        dur = c.get("duration_ms") or 0.0
        dur_str = f"{dur:.0f}ms" if dur < 1000 else f"{dur/1000:.1f}s"
        call_cost = c.get("cost_usd") or 0.0
        cost_str = f"${call_cost:.4f}" if call_cost else "—"
        snippet = (c.get("prompt_snippet") or "")[:60]
        tbl.add_row(
            str(i),
            c["agent_name"],
            dur_str,
            str(c.get("input_tokens", 0)),
            str(c.get("output_tokens", 0)),
            cost_str,
            snippet,
        )

    console.print(tbl)

    if has_full:
        console.print(
            "\n[dim]Full prompt/response available — use "
            "[bold]--show-call N[/bold] to inspect any call.[/dim]"
        )
    else:
        console.print(
            "\n[dim]Only snippets stored. Re-run with [bold]--full-trace[/bold] "
            "to capture complete prompts.[/dim]"
        )


def _print_call_detail(run: dict, call: Optional[dict], call_number: int) -> None:
    """Print the full prompt and response for a single agent call."""
    if call is None:
        console.print("[red]Call not found.[/]")
        return

    agent = call.get("agent_name", "?")
    dur = call.get("duration_ms") or 0.0
    dur_str = f"{dur:.0f}ms" if dur < 1000 else f"{dur/1000:.1f}s"
    cost = call.get("cost_usd") or 0.0

    console.print(Panel(
        f"Run:    [cyan]{run['id'][:8]}…[/cyan]   Thread: [cyan]{run['thread_id']}[/cyan]\n"
        f"Agent:  [bold yellow]{agent}[/bold yellow]   Call #{call_number}\n"
        f"Tokens: [green]{call.get('input_tokens', 0)} in / "
        f"{call.get('output_tokens', 0)} out[/green]   "
        f"Duration: [dim]{dur_str}[/dim]   Cost: [cyan]${cost:.4f}[/cyan]",
        title="Call detail",
        border_style="dim",
    ))

    prompt_text = call.get("prompt_full") or call.get("prompt_snippet") or ""
    response_text = call.get("response_full") or call.get("response_snippet") or ""
    is_snippet = not call.get("prompt_full")

    if is_snippet:
        console.print(
            "[yellow]Note:[/yellow] only snippets available. "
            "Re-run with [bold]--full-trace[/bold] to store complete text.\n"
        )

    console.print(Panel(
        prompt_text or "[dim](empty)[/dim]",
        title=f"[bold]PROMPT[/bold]{' (snippet)' if is_snippet else ''}",
        border_style="green",
        padding=(0, 1),
    ))
    console.print(Panel(
        response_text or "[dim](empty)[/dim]",
        title=f"[bold]RESPONSE[/bold]{' (snippet)' if is_snippet else ''}",
        border_style="blue",
        padding=(0, 1),
    ))


# ---------------------------------------------------------------------------
# antcrew replay — resume a pipeline from its last SqliteSaver checkpoint
# ---------------------------------------------------------------------------

@app.command(name="replay")
def replay_cmd(
    thread_id: str = typer.Argument(..., help="Thread ID to resume"),
    checkpointer_db: Path = typer.Option(
        ..., "--checkpointer", "--db",
        help="SqliteSaver SQLite file used in the original run.",
    ),
    trace_db: Optional[Path] = typer.Option(
        None, "--trace",
        help="TraceLog SQLite file (created with --trace). "
             "Auto-looks up the original request and team so you don't need to re-specify them.",
    ),
    team: Optional[str] = typer.Option(
        None, "--team", "-t", help=f"Team to use: {_TEAM_CHOICES} (auto-detected from --trace)"
    ),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    request: Optional[str] = typer.Option(
        None, "--request", "-r",
        help="Request to pass to the pipeline (auto-detected from --trace if omitted).",
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Resume a pipeline run from its last SqliteSaver checkpoint.

    \b
    With TraceLog (zero-friction — request and team auto-detected):
        antcrew replay sprint-1 \\
          --checkpointer ~/.antcrew/threads.db \\
          --trace ~/.antcrew/trace.db

    \b
    Without TraceLog (explicit):
        antcrew replay sprint-1 \\
          --checkpointer ~/.antcrew/threads.db \\
          --team dev --request "Build JWT auth"

    \b
    Typical workflow — run fails at agent 3, resume after fix:
        antcrew run "Build JWT auth" --thread sprint-1 \\
          --checkpointer ~/.antcrew/threads.db --trace ~/.antcrew/trace.db
        # ... fix cost limit or env issue ...
        antcrew replay sprint-1 \\
          --checkpointer ~/.antcrew/threads.db --trace ~/.antcrew/trace.db
    """
    from antcrew.checkpointers import SqliteSaver as _SqliteSaver
    if _SqliteSaver is None:
        console.print(
            "[red]Error:[/] --checkpointer requires langgraph-checkpoint-sqlite.\n"
            "Install with: [bold]pip install antcrew[sqlite][/bold]"
        )
        raise typer.Exit(1)

    _request = request
    _team = team

    # Auto-detect request + team from TraceLog if --trace is provided
    if trace_db:
        if not trace_db.exists():
            console.print(f"[red]TraceLog not found:[/] {trace_db}")
            raise typer.Exit(1)
        from antcrew.trace import TraceLog as _TL
        _tlog = _TL(trace_db)
        _prior = _tlog.get_run_by_thread(thread_id)
        _tlog.close()
        if _prior:
            _request = _request or _prior["request"]
            if _team is None:
                _team = _prior["team"].lower().replace("team", "")
        else:
            console.print(
                f"[yellow]Warning:[/] thread '{thread_id}' not found in TraceLog — "
                "falling back to explicit --request / --team."
            )

    if not _request:
        console.print(
            "[red]Error:[/] Cannot determine request. "
            "Provide --request or --trace pointing to a TraceLog that contains this thread."
        )
        raise typer.Exit(1)
    if not _team:
        console.print(
            "[red]Error:[/] Cannot determine team. "
            "Provide --team or --trace pointing to a TraceLog that contains this thread."
        )
        raise typer.Exit(1)

    # Build team + attach SqliteSaver
    from antcrew.config import build_llm as _bllm
    _llm = _bllm(model)
    _active_team = _build_team(_team, model, integrations=[], llm=_llm)

    import sqlite3 as _sqlite3
    _conn = _sqlite3.connect(str(checkpointer_db.expanduser()), check_same_thread=False)
    _active_team._checkpointer = _SqliteSaver(_conn)

    console.print(
        f"\n[bold green]AntCrew[/] replay  —  "
        f"thread=[cyan]{thread_id}[/]  team=[cyan]{_team}[/]  model=[cyan]{model}[/]\n"
    )

    try:
        state = _run_with_stream(_active_team, _request, thread_id, stream, llm=_llm)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(1)
    except Exception as exc:
        from antcrew.core.exceptions import CostLimitExceeded as _CLE
        if isinstance(exc, _CLE):
            console.print(
                f"\n[yellow bold]Cost limit reached:[/] ${exc.cost_usd:.4f} spent "
                f"(limit: ${exc.limit_usd:.4f}). Pipeline stopped."
            )
        else:
            console.print(f"\n[red bold]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print()
    if output_json:
        def _ser(obj):
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)
        state_dict = state.state if hasattr(state, "state") else state
        console.print_json(__import__("json").dumps(state_dict, default=_ser))
    else:
        _print_state(state, _team)

    if hasattr(state, "thread_id"):
        cost_str = f"  cost=[cyan]${state.cost_usd:.4f}[/cyan]" if state.cost_usd else ""
        console.print(f"[dim]thread=[cyan]{state.thread_id}[/cyan]{cost_str}[/dim]")


