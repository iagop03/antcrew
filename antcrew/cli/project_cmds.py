"""Project sub-app commands (project run / show / history)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import _project_app, console, _MODEL_HELP, _TEAM_CHOICES
from antcrew.cli._shared import _print_state, _print_state_raw

def _resolve_project_team(config: Optional[Path], team_opt: Optional[str], model: str):
    """Return (team_instance, team_spec_dict) or (None, None)."""
    if config:
        from antcrew.config import load as _load_config
        t = _load_config(config)
        return t, {"type": "config", "path": str(config)}
    if team_opt:
        from antcrew.config import build_llm
        llm = build_llm(model)
        if team_opt == "dev":
            from antcrew.teams.dev_team import DevTeam
            return DevTeam(model=llm), {"type": "inline", "team": team_opt, "model": model}
        if team_opt == "fullstack":
            from antcrew.teams.fullstack_team import FullStackTeam
            return FullStackTeam(model=llm), {"type": "inline", "team": team_opt, "model": model}
        if team_opt == "research":
            from antcrew.teams.research_team import ResearchTeam
            return ResearchTeam(model=llm), {"type": "inline", "team": team_opt, "model": model}
        if team_opt == "content":
            from antcrew.teams.content_team import ContentTeam
            return ContentTeam(model=llm), {"type": "inline", "team": team_opt, "model": model}
        raise typer.BadParameter(f"Unknown team '{team_opt}'. Choose from: {_TEAM_CHOICES}")
    return None, None


@_project_app.command("run")
def project_run(
    path: Path = typer.Argument(..., help="Project JSON file (created if it doesn't exist)"),
    request: str = typer.Argument(..., help="Task or request for this run"),
    team: Optional[str] = typer.Option(
        None, "--team", "-t",
        help=f"Team to use (new projects): {_TEAM_CHOICES}",
    ),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to agentteam.yaml (alternative to --team)"
    ),
    name: str = typer.Option("", "--name", "-n", help="Project name (only used when creating)"),
    cache: Optional[Path] = typer.Option(
        None, "--cache",
        help="SQLite file for persistent LLM response cache. "
             "Avoids redundant API calls across runs.",
    ),
) -> None:
    """Run a request against a persistent project.

    On the first run a new project is created at PATH.
    Subsequent runs load the project, inject prior context, and accumulate state.
    The project auto-saves after every run.

    Team must be specified (via --team or --config) the first time; subsequent
    runs reuse the stored team spec unless you override it.

    \b
    Examples:
        antcrew project run auth.json "Build JWT auth" --team dev --model claude
        antcrew project run auth.json "Add OAuth"          # reuses stored team
        antcrew project run auth.json "Fix refresh token"  # continues accumulating
    """
    from antcrew.project import Project

    resolved_team, team_spec = _resolve_project_team(config, team, model)

    if not path.exists():
        if resolved_team is None:
            console.print(
                "[red]Error:[/] New project requires [bold]--team[/bold] or "
                "[bold]--config[/bold].\n"
                f"  Example: antcrew project run {path} \"{request}\" --team dev"
            )
            raise typer.Exit(1)
        p = Project(resolved_team, name=name or path.stem, path=path)
    else:
        try:
            p = Project.load(path, team=resolved_team)
        except ValueError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Failed to load project:[/] {exc}")
            raise typer.Exit(1)

    if team_spec:
        p._team_spec = team_spec

    # Attach FileLLMCache if --cache flag given
    if cache is not None:
        _llm = getattr(p.team, "llm", None)
        if _llm is not None:
            from antcrew.models.cache import FileLLMCache
            _llm.with_cache(FileLLMCache(cache))

    n_runs_before = len(p.history)
    console.print(
        f"\n[bold green]AntCrew project[/] — [cyan]{path}[/]  "
        f"[dim](run #{n_runs_before + 1})[/dim]\n"
    )

    try:
        with console.status("[bold]Running…[/]"):
            state = p.run(request)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"\n[red bold]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print()

    team_type = type(p.team).__name__.lower().replace("team", "")
    _print_state(state, team_type)

    h = p.history[-1]
    console.print(
        f"\n[dim]+{h.get('new_tickets', 0)} tickets  "
        f"+{h.get('new_code_files', 0)} code files  "
        f"total: {len(p.state.get('tickets') or [])} tickets, "
        f"{len(p.state.get('code_artifacts') or [])} files[/dim]"
    )
    console.print(f"[dim]Saved → [cyan]{path}[/][/dim]")

    if cache is not None:
        _llm = getattr(p.team, "llm", None)
        _c = getattr(_llm, "cache", None) if _llm is not None else None
        if _c is not None and hasattr(_c, "stats"):
            s = _c.stats()
            total = s.get("hits", 0) + s.get("misses", 0)
            if total > 0:
                console.print(
                    f"[dim]Cache: {s['hits']} hits / {s['misses']} misses "
                    f"({s['hit_rate'] * 100:.0f}% hit rate)[/dim]"
                )
    console.print()


@_project_app.command("show")
def project_show(
    path: Path = typer.Argument(..., help="Project JSON file"),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON"),
) -> None:
    """Display the current accumulated state of a project.

    Shows the PRD, all tickets, all code files, and test results collected
    across all runs.
    """
    if not path.exists():
        console.print(f"[red]Project file not found:[/] {path}")
        raise typer.Exit(1)

    try:
        import json as _json
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to read project:[/] {exc}")
        raise typer.Exit(1)

    if output_json:
        console.print_json(path.read_text(encoding="utf-8"))
        return

    name = raw.get("name") or path.stem
    runs = len(raw.get("history") or [])
    console.print(f"\n[bold green]Project:[/] [cyan]{name}[/]  [dim]({runs} run{'s' if runs != 1 else ''})[/dim]\n")

    state = raw.get("state") or {}
    if state.get("prd") or state.get("tickets") or state.get("code_artifacts"):
        _print_state_raw(state, "dev")
    elif state.get("research_document"):
        _print_state_raw(state, "research")
    elif state.get("content_piece"):
        _print_state_raw(state, "content")
    else:
        console.print("[dim](no state yet — run a request first)[/dim]")
    console.print()


@_project_app.command("history")
def project_history(
    path: Path = typer.Argument(..., help="Project JSON file"),
) -> None:
    """Show the run history of a project as a table."""
    from rich.table import Table
    import json as _json
    import time as _time

    if not path.exists():
        console.print(f"[red]Project file not found:[/] {path}")
        raise typer.Exit(1)

    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to read project:[/] {exc}")
        raise typer.Exit(1)

    name = raw.get("name") or path.stem
    history = raw.get("history") or []

    console.print(f"\n[bold green]Project history:[/] [cyan]{name}[/]  [dim]{path}[/dim]\n")

    if not history:
        console.print("[dim]No runs yet.[/dim]\n")
        return

    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    table.add_column("#",          style="dim",   no_wrap=True)
    table.add_column("Timestamp",  style="dim",   no_wrap=True)
    table.add_column("Request",    style="cyan")
    table.add_column("Tickets",    justify="right")
    table.add_column("Code files", justify="right")

    for i, h in enumerate(history, 1):
        ts  = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(h.get("timestamp", 0)))
        req = (h.get("request") or "")[:60]
        t   = str(h.get("new_tickets", 0))
        c   = str(h.get("new_code_files", 0))
        table.add_row(str(i), ts, req, t, c)

    console.print(table)

    # Totals
    state = raw.get("state") or {}
    total_t = len(state.get("tickets") or [])
    total_c = len(state.get("code_artifacts") or [])
    console.print(
        f"\n[dim]Total: {total_t} ticket{'s' if total_t != 1 else ''}, "
        f"{total_c} code file{'s' if total_c != 1 else ''}[/dim]\n"
    )


# Template strings are in antcrew/cli/_templates.py

# ---------------------------------------------------------------------------
# antcrew flow show / antcrew flow validate
# ---------------------------------------------------------------------------

