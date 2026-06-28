"""antcrew run command."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console, _MODEL_HELP, _TEAM_CHOICES
from antcrew.cli._shared import _build_team, _print_state, _print_usage
from antcrew.cli._run_helpers import (
    _print_dry_run, _run_with_stream, _save_outputs_to_dir, _run_repl,
)


def _parse_project_dirs(
    flags: list[str] | None,
) -> tuple[str | None, dict[str, str] | None]:
    """Parse --project-dir flags into (project_dir, project_dirs).

    Each flag is either a bare path ("./src") or "label:path" ("backend:./src").
    Single entry → project_dir (str) for backward compat.
    Multiple entries → project_dirs (dict).
    """
    if not flags:
        return None, None
    pairs: dict[str, str] = {}
    for entry in flags:
        colon = entry.find(":")
        # "label:path" only when colon exists and label is ≥2 chars (avoids C:\ on Windows)
        if colon > 1:
            label = entry[:colon].strip()
            path = entry[colon + 1:].strip()
            pairs[label] = path
        else:
            pairs[Path(entry).name] = entry
    if len(pairs) == 1:
        only_path = next(iter(pairs.values()))
        return only_path, None
    return None, pairs


@app.command()
def run(
    request: Optional[str] = typer.Argument(
        None,
        help="Task or topic for the team (prompted interactively if omitted)",
    ),
    team: str = typer.Option("dev", "--team", "-t", help=f"Team to use: {_TEAM_CHOICES}"),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to agentteam.yaml"
    ),
    thread: str = typer.Option("default", "--thread", help="Thread ID for checkpointing"),
    save: Optional[Path] = typer.Option(
        None, "--save", "-s", help="Save final state to a JSON file"
    ),
    stream: bool = typer.Option(
        True, "--stream/--no-stream", help="Show tokens as they stream from the LLM"
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Print final state as JSON instead of rich output"
    ),
    project: Optional[Path] = typer.Option(
        None, "--project", "-p",
        help="Project JSON file for persistent sessions (created if it doesn't exist). "
             "Each run accumulates state so agents build on prior work.",
    ),
    cache: Optional[Path] = typer.Option(
        None, "--cache",
        help="SQLite file for persistent LLM response cache. "
             "Avoids redundant API calls across runs.",
    ),
    checkpointer_db: Optional[Path] = typer.Option(
        None, "--checkpointer", "--db",
        help="SQLite file for persistent thread state. "
             "Runs with the same --thread resume from where they left off. "
             "Requires: pip install antcrew[sqlite]",
    ),
    max_cost: Optional[float] = typer.Option(
        None, "--max-cost",
        help="Abort the run if estimated LLM cost (USD) exceeds this limit. "
             "Example: --max-cost 1.50",
    ),
    trace_db: Optional[Path] = typer.Option(
        None, "--trace",
        help="SQLite file for per-agent call tracing (timing, tokens, cost). "
             "View with: antcrew trace <file.db>",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show pipeline steps without running them (CustomTeam only).",
    ),
    request_file: Optional[Path] = typer.Option(
        None, "--request-file", "-r",
        help="Read the request from a file instead of the command line.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", "-O",
        help="Save each step output_key to a separate file in this directory.",
    ),
    write_back: Optional[Path] = typer.Option(
        None, "--write-back", "-W",
        help="After run, write code/test/devops artifacts to this directory "
             "(respects each artifact's file_path). Use '.' for the current directory.",
    ),
    write_back_yes: bool = typer.Option(
        False, "--write-back-yes",
        help="With --write-back: overwrite existing files without confirmation.",
    ),
    project_dir: Optional[list[str]] = typer.Option(
        None, "--project-dir",
        help="Existing project directory for CodebaseScannerAgent (fullstack team). "
             "Repeat for multiple components: --project-dir backend:./src --project-dir frontend:./client. "
             "Without a label prefix the directory name is used as the label.",
    ),
    scan_context: Optional[Path] = typer.Option(
        None, "--context",
        help="Pre-computed scan JSON from 'antcrew scan --output'. Injects codebase_analysis "
             "into the initial state so CodebaseScannerAgent is skipped (saves cost).",
    ),
    repl: bool = typer.Option(
        False, "--repl",
        help="Interactive REPL mode — run the pipeline repeatedly in a loop.",
    ),
    repl_stateful: bool = typer.Option(
        False, "--repl-stateful",
        help="Carry output state from each REPL iteration into the next (implies --repl).",
    ),
) -> None:
    """Run a multi-agent pipeline on REQUEST.

    \b
    Basic usage:
        antcrew run "Build JWT auth" --team dev --model claude

    \b
    Interactive prompt (omit the request argument):
        antcrew run --config team.yaml

    \b
    Request from file:
        antcrew run --config team.yaml --request-file task.md

    \b
    Persistent project (state accumulates across runs):
        antcrew run "Build JWT auth" --team dev --project auth.json
        antcrew run "Add OAuth"      --team dev --project auth.json

    \b
    With LLM cache (avoids re-calling the API during development):
        antcrew run "Build JWT auth" --team dev --cache ~/.antcrew/cache.db

    \b
    Combined — from a config file that includes both:
        antcrew run "Build JWT auth" --config team.yaml
    """
    _llm_ref = None
    _project_ref = None

    try:
        if config:
            from antcrew.config import load_context
            ctx = load_context(config)
            active_team = ctx.team
            _project_ref = ctx.project
            _llm_ref = getattr(active_team, "llm", None)
            team = type(active_team).__name__.lower().replace("team", "")
        else:
            from antcrew.config import build_llm
            _llm_ref = build_llm(model)
            _pd, _pds = _parse_project_dirs(project_dir)
            if project_dir and team != "fullstack":
                console.print(
                    f"[yellow]Warning:[/] --project-dir is only used by the "
                    f"[bold]fullstack[/] team (CodebaseScannerAgent). "
                    f"Current team [bold]{team}[/] will ignore it."
                )
            _ctx: "dict | None" = None
            if scan_context is not None:
                if not scan_context.exists():
                    console.print(f"[red]Context file not found:[/] {scan_context}")
                    raise typer.Exit(1)
                if team != "fullstack":
                    console.print(
                        f"[yellow]Warning:[/] --context is only used by the "
                        f"[bold]fullstack[/] team. Current team [bold]{team}[/] will ignore it."
                    )
                else:
                    import json as _json
                    _ctx = _json.loads(scan_context.read_text(encoding="utf-8"))
                    if project_dir:
                        console.print(
                            "[yellow]Note:[/] --context takes precedence; "
                            "--project-dir will not trigger a new scan."
                        )
            active_team = _build_team(
                team, model, integrations=[], llm=_llm_ref,
                project_dir=_pd, project_dirs=_pds, scan_context=_ctx,
            )

        # Resolve the request from file, argument, or interactive prompt.
        # Done here (after team is built) so --dry-run can still work without
        # a request.
        if request_file is not None:
            if not request_file.exists():
                console.print(f"[red]✗[/] Request file not found: {request_file}")
                raise typer.Exit(1)
            actual_request: str = request_file.read_text(encoding="utf-8").strip()
        elif request is not None:
            actual_request = request
        elif not dry_run and not repl:
            actual_request = typer.prompt("Request")
        else:
            actual_request = ""   # dry_run / repl don't need it yet

        # --dry-run: show pipeline structure and exit without LLM calls
        if dry_run:
            try:
                from antcrew.teams.custom_team import CustomTeam as _CT
                if isinstance(active_team, _CT):
                    _print_dry_run(active_team)
                else:
                    console.print(
                        "[yellow]--dry-run is only supported for CustomTeam "
                        "(team: custom) configs.[/yellow]"
                    )
            except Exception as exc:
                console.print(f"[red]--dry-run error:[/] {exc}")
                raise typer.Exit(1)
            raise typer.Exit(0)

        # --cache flag overrides / supplements config
        if cache and _llm_ref is not None:
            from antcrew.models.cache import FileLLMCache
            _llm_ref.with_cache(FileLLMCache(cache))

        # --checkpointer flag attaches SqliteSaver for persistent thread state
        if checkpointer_db:
            from antcrew.checkpointers import SqliteSaver as _SqliteSaver
            if _SqliteSaver is None:
                console.print(
                    "[red]Error:[/] --checkpointer requires langgraph-checkpoint-sqlite.\n"
                    "Install with: [bold]pip install antcrew[sqlite][/bold]"
                )
                raise typer.Exit(1)
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(
                str(checkpointer_db.expanduser()), check_same_thread=False
            )
            active_team._checkpointer = _SqliteSaver(_conn)

        # --max-cost flag sets a per-run cost budget
        if max_cost is not None and _llm_ref is not None:
            _llm_ref.max_cost_usd = max_cost

        # --trace flag attaches TraceLog for per-agent call recording
        if trace_db:
            from antcrew.trace import TraceLog as _TraceLog
            active_team._trace_log = _TraceLog(trace_db)

        # --project flag creates / resumes a Project (overrides config project:)
        if project:
            from antcrew.project import Project
            team_spec = (
                {"type": "config", "path": str(config)}
                if config
                else {"type": "inline", "team": team, "model": model}
            )
            if project.exists():
                _project_ref = Project.load(project, team=active_team)
            else:
                _project_ref = Project(active_team, name=project.stem, path=project)
            _project_ref._team_spec = team_spec

        # --repl / --repl-stateful: interactive loop
        if repl or repl_stateful:
            _run_repl(active_team, stream=stream, llm=_llm_ref,
                      output_dir=output_dir, team_name=team,
                      stateful=repl_stateful)
            raise typer.Exit(0)

        # Run
        if _project_ref is not None:
            _p = _project_ref  # local alias so the lambda closes over it

            class _ProjRunner:
                def run(self_, req: str, *, thread_id: str = "default") -> dict:
                    return _p.run(req)

            n_before = len(_p.history)
            console.print(
                f"\n[bold green]AntCrew[/] v0.4  —  "
                f"team=[cyan]{team}[/]  model=[cyan]{model}[/]  "
                f"project=[cyan]{project or getattr(_p, '_path', '')}[/]  "
                f"[dim](run #{n_before + 1})[/dim]\n"
            )
            state = _run_with_stream(_ProjRunner(), actual_request, thread, stream, llm=_llm_ref)
        else:
            console.print(
                f"\n[bold green]AntCrew[/] v0.4  —  team=[cyan]{team}[/]  model=[cyan]{model}[/]\n"
            )
            state = _run_with_stream(active_team, actual_request, thread, stream, llm=_llm_ref)

    except typer.Exit:
        raise
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
        console.print_json(json.dumps(state_dict, default=_ser))
    else:
        _print_state(state, team)

    # Project summary footer
    if _project_ref is not None:
        h = _project_ref.history[-1]
        proj_path = project or getattr(_project_ref, "_path", "")
        console.print(
            f"\n[dim]Project [{_project_ref.name}] — "
            f"run #{len(_project_ref.history)}  "
            f"+{h.get('new_tickets', 0)} tickets  "
            f"+{h.get('new_code_files', 0)} files  "
            f"→ [cyan]{proj_path}[/][/dim]"
        )

    # RunResult metadata footer (thread_id / cost)
    if hasattr(state, "thread_id"):
        cost_str = f"  cost=[cyan]${state.cost_usd:.4f}[/cyan]" if state.cost_usd else ""
        console.print(
            f"[dim]thread=[cyan]{state.thread_id}[/cyan]{cost_str}[/dim]"
        )

    # Cache stats
    if _llm_ref is not None and hasattr(_llm_ref, "cache") and _llm_ref.cache is not None:
        _cache = _llm_ref.cache
        if hasattr(_cache, "stats"):
            s = _cache.stats()
            total = s.get("hits", 0) + s.get("misses", 0)
            if total > 0:
                console.print(
                    f"[dim]Cache: {s['hits']} hits / {s['misses']} misses "
                    f"({s['hit_rate'] * 100:.0f}% hit rate)[/dim]"
                )

    if output_dir:
        _save_outputs_to_dir(state, output_dir)

    if write_back is not None:
        from antcrew.core.writeback import write_back as _wb
        root = Path(write_back).resolve()
        console.print(f"\nWriting artifacts to [cyan]{root}[/cyan]\n")
        _wb(
            state, root,
            dry_run=False,
            yes=write_back_yes,
            confirm_fn=(None if write_back_yes else lambda p: typer.confirm(p, default=False)),
            print_fn=console.print,
        )

    if save:
        from antcrew.utils.persistence import save_state
        save_state(state, save)
        console.print(f"\n[dim]State saved → [cyan]{save}[/][/dim]")

    _print_usage(_llm_ref)
    console.print("\n[bold green]Done![/]\n")


