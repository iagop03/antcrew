"""
AntCrew CLI — run multi-agent pipelines from the terminal.

Usage:
    antcrew run "Build a JWT auth module"
    antcrew run "AI safety risks" --team research --model ollama:llama3
    antcrew run "Blog post about LLMs" --team content --model gpt-4o
    antcrew run "Build auth" --model simulated          # no API calls
    antcrew run "Build auth" --config agentteam.yaml
    antcrew init --template dev_team
    antcrew init --template fullstack_team
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

app = typer.Typer(
    name="antcrew",
    help="Multi-agent framework for software dev teams, built on LangGraph.",
    add_completion=False,
)
console = Console()

# Sub-app for flow commands (antcrew flow show / antcrew flow validate)
_flow_app = typer.Typer(
    name="flow",
    help="Inspect and validate flow definitions (YAML/JSON).",
    add_completion=False,
)
app.add_typer(_flow_app)

# Sub-app for project commands (antcrew project run / show / history)
_project_app = typer.Typer(
    name="project",
    help="Manage persistent multi-run projects (state survives across runs).",
    add_completion=False,
)
app.add_typer(_project_app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEAM_CHOICES = ("dev", "fullstack", "research", "content")
_MODEL_HELP = (
    "Model to use: claude (default), gpt-4o, ollama:<name>, groq:<name>, simulated"
)


def _build_team(team: str, model_str: str, integrations: list, llm=None):
    from antcrew.config import build_llm

    llm = llm or build_llm(model_str)

    if team == "dev":
        from antcrew.teams.dev_team import DevTeam
        return DevTeam(model=llm, integrations=integrations)
    if team == "fullstack":
        from antcrew.teams.fullstack_team import FullStackTeam
        return FullStackTeam(model=llm, integrations=integrations)
    if team == "research":
        from antcrew.teams.research_team import ResearchTeam
        return ResearchTeam(model=llm)
    if team == "content":
        from antcrew.teams.content_team import ContentTeam
        return ContentTeam(model=llm)

    raise typer.BadParameter(f"Unknown team '{team}'. Choose from: {_TEAM_CHOICES}")


def _print_state(state: dict, team: str) -> None:
    if team in ("dev", "fullstack"):
        if state.get("prd"):
            console.print(Panel(
                f"[bold]{state['prd'].title}[/]\n{state['prd'].summary}",
                title="PRD", border_style="blue",
            ))
        if state.get("tickets"):
            tickets = state["tickets"]
            console.print(Panel(
                "\n".join(f"  [{t.priority.value}] {t.id}: {t.title}" for t in tickets),
                title=f"Tickets ({len(tickets)})", border_style="yellow",
            ))
        if state.get("code_artifacts"):
            for a in state["code_artifacts"]:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  ({a.ticket_id})", border_style="green",
                ))
        if state.get("test_artifacts"):
            console.print(f"[dim]Test files: {len(state['test_artifacts'])}[/]")
        if state.get("test_results") is not None:
            _print_test_results(state["test_results"])
        if state.get("devops_artifacts"):
            for a in state["devops_artifacts"]:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  [dim](devops)[/dim]", border_style="cyan",
                ))
        if state.get("doc_artifacts"):
            for a in state["doc_artifacts"]:
                console.print(Panel(
                    Syntax(a.content, "markdown", theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  [dim]({a.doc_type})[/dim]", border_style="blue",
                ))
        if state.get("review"):
            r = state["review"]
            colour = {"approve": "green", "request_changes": "yellow", "reject": "red"}[r.verdict]
            console.print(Panel(
                r.summary,
                title=f"Code Review: [{colour}]{r.verdict.upper()}[/{colour}]",
                border_style=colour,
            ))

    elif team == "research":
        if state.get("research_document"):
            doc = state["research_document"]
            console.print(Panel(
                "\n".join(f"• {f}" for f in doc.key_findings),
                title=f"[bold]{doc.title}[/] — Key Findings", border_style="cyan",
            ))
        if state.get("content_piece") and state["content_piece"].body:
            piece = state["content_piece"]
            console.print(Panel(
                piece.body[:2000] + (" …" if len(piece.body) > 2000 else ""),
                title=piece.title, border_style="blue",
            ))

    elif team == "content":
        if state.get("content_piece"):
            piece = state["content_piece"]
            console.print(Panel(
                piece.body[:3000] + (" …" if len(piece.body) > 3000 else ""),
                title=f"{piece.title}  [dim]({piece.word_count or '?'} words)[/dim]",
                border_style="magenta",
            ))

    elif team == "custom":
        _SKIP = {"request", "messages", "errors", "metadata", "current_agent"}
        for key, value in state.items():
            if key in _SKIP or value is None:
                continue
            text = str(value)
            console.print(Panel(
                text[:3000] + (" …" if len(text) > 3000 else ""),
                title=f"[bold]{key}[/]",
                border_style="cyan",
            ))

    if state.get("errors"):
        for err in state["errors"]:
            console.print(f"[red]Error:[/] {err}")


# ---------------------------------------------------------------------------
# Test results display
# ---------------------------------------------------------------------------

def _print_test_results(tr) -> None:
    """Print a RunResult panel — works with both live objects and raw dicts."""
    if tr is None:
        return
    # tr may be a RunResult dataclass or a plain dict (from load_state)
    if hasattr(tr, "success"):
        success  = tr.success
        summary  = tr.summary()
        output   = tr.output or ""
    else:
        passed   = int(tr.get("passed", 0))
        failed   = int(tr.get("failed", 0))
        errors   = int(tr.get("errors", 0))
        success  = bool(tr.get("success", failed == 0 and errors == 0))
        ms       = float(tr.get("duration_ms", 0))
        parts    = []
        if passed:
            parts.append(f"{passed} passed")
        if failed:
            parts.append(f"{failed} failed")
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        summary = (", ".join(parts) or "no tests ran") + f" in {ms:.0f}ms"
        output  = str(tr.get("output", ""))

    colour  = "green" if success else "red"
    icon    = "✓" if success else "✗"
    body    = summary
    if not success and output:
        tail = output[-1200:].strip()
        body = f"{summary}\n\n{tail}"
    console.print(Panel(
        body,
        title=f"[{colour}]Tests {icon}[/{colour}]",
        border_style=colour,
    ))


# ---------------------------------------------------------------------------
# Usage display
# ---------------------------------------------------------------------------

def _print_usage(llm) -> None:
    """Print a token/cost summary table if the LLM recorded any usage."""
    if llm is None:
        return
    summary = llm.get_usage_summary()
    if not summary["by_agent"]:
        return

    from rich.table import Table

    table = Table(title="Token usage", show_header=True, header_style="bold dim")
    table.add_column("Agent",          style="cyan",  no_wrap=True)
    table.add_column("Model",          style="dim",   no_wrap=True)
    table.add_column("In tokens",      justify="right")
    table.add_column("Out tokens",     justify="right")
    table.add_column("Cost (USD)",     justify="right")

    for entry in summary["by_agent"]:
        cost = entry["cost_usd"]
        cost_str = f"${cost:.4f}" if cost else "—"
        table.add_row(
            entry.get("agent") or "—",
            entry.get("model") or "—",
            str(entry["input_tokens"]),
            str(entry["output_tokens"]),
            cost_str,
        )

    total_cost = summary["total_cost_usd"]
    table.add_section()
    table.add_row(
        "[bold]Total[/]", "",
        f"[bold]{summary['total_input_tokens']}[/]",
        f"[bold]{summary['total_output_tokens']}[/]",
        f"[bold]{'$'+f'{total_cost:.4f}' if total_cost else '—'}[/]",
    )

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------

def _run_with_stream(team, request: str, thread: str, stream: bool, llm=None):
    """Run the pipeline, optionally showing tokens live with Rich."""
    from rich.live import Live
    from rich.panel import Panel as _Panel
    from rich.text import Text

    if not stream or llm is None:
        with console.status("[bold]Running…[/]"):
            return team.run(request, thread_id=thread)

    display: dict = {"agent": "", "text": ""}

    def _on_token(token: str) -> None:
        if llm.current_agent != display["agent"]:
            display["agent"] = llm.current_agent
            display["text"] = ""
        display["text"] += token
        live.update(_Panel(
            Text(display["text"][-800:], overflow="fold"),
            title=f"[bold cyan]{display['agent'] or '…'}[/bold cyan]",
            border_style="cyan",
        ))

    llm.on_token = _on_token

    with Live(
        _Panel("Starting…", border_style="dim"),
        console=console,
        refresh_per_second=15,
        transient=True,
    ) as live:
        state = team.run(request, thread_id=thread)

    llm.on_token = None
    return state


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def run(
    request: str = typer.Argument(..., help="Task or topic for the team"),
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
) -> None:
    """Run a multi-agent pipeline on REQUEST.

    \b
    Basic usage:
        antcrew run "Build JWT auth" --team dev --model claude

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
            active_team = _build_team(team, model, integrations=[], llm=_llm_ref)

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
            state = _run_with_stream(_ProjRunner(), request, thread, stream, llm=_llm_ref)
        else:
            console.print(
                f"\n[bold green]AntCrew[/] v0.4  —  team=[cyan]{team}[/]  model=[cyan]{model}[/]\n"
            )
            state = _run_with_stream(active_team, request, thread, stream, llm=_llm_ref)

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

    if save:
        from antcrew.utils.persistence import save_state
        save_state(state, save)
        console.print(f"\n[dim]State saved → [cyan]{save}[/][/dim]")

    _print_usage(_llm_ref)
    console.print("\n[bold green]Done![/]\n")


@app.command()
def init(
    template: str = typer.Option(
        "dev_team",
        "--template",
        help="Template to generate: dev_team | research_team | content_team",
    ),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Output directory"),
) -> None:
    """Generate a starter agentteam.yaml + main.py for a given template."""
    templates: dict[str, tuple[str, str]] = {
        "dev_team":       (_YAML_DEV,       _MAIN_DEV),
        "fullstack_team": (_YAML_FULLSTACK,  _MAIN_FULLSTACK),
        "research_team":  (_YAML_RESEARCH,   _MAIN_RESEARCH),
        "content_team":   (_YAML_CONTENT,    _MAIN_CONTENT),
        "custom":         (_YAML_CUSTOM,     _MAIN_CUSTOM),
    }

    if template not in templates:
        console.print(f"[red]Unknown template '{template}'.[/] Choose from: {list(templates)}")
        raise typer.Exit(1)

    yaml_content, main_content = templates[template]
    output.mkdir(parents=True, exist_ok=True)

    yaml_path = output / "agentteam.yaml"
    main_path = output / "main.py"

    yaml_path.write_text(yaml_content, encoding="utf-8")
    main_path.write_text(main_content, encoding="utf-8")

    console.print("\n[bold green]Generated:[/]")
    console.print(f"  [cyan]{yaml_path}[/]  — team configuration")
    console.print(f"  [cyan]{main_path}[/]  — entry point\n")
    console.print("Run with:")
    console.print(f"  [bold]antcrew run \"Your request\" --config {yaml_path}[/]")
    console.print(f"  [bold]python {main_path}[/]\n")


@app.command()
def setup(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name (skips the prompt)"),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Output directory"),
    filename: str = typer.Option("agentteam.yaml", "--filename", "-f", help="YAML filename"),
) -> None:
    """Conversational wizard that generates an agentteam.yaml for your project."""
    from antcrew.agents.setup import SetupAgent
    agent = SetupAgent()
    agent.run_wizard(name=name, output_dir=output, filename=filename)


cache_app = typer.Typer(help="Manage the LLM response cache.")
app.add_typer(cache_app, name="cache")


@cache_app.command("clear")
def cache_clear(
    db: Path = typer.Argument(
        ...,
        help="Path to the SQLite cache file (e.g. ~/.antcrew/cache.db)",
    ),
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a",
        help="Clear only entries for this agent (e.g. frontend_dev). Omit to clear all.",
    ),
) -> None:
    """Delete entries from a persistent cache file (all, or just one agent)."""
    from antcrew.models.cache import FileLLMCache
    db = Path(db).expanduser()
    if not db.exists():
        console.print(f"[yellow]Cache file not found:[/] {db}")
        raise typer.Exit(1)
    c = FileLLMCache(db)
    if agent:
        n = c.clear_agent(agent)
        console.print(f"[green]Cleared[/] {n} entr{'y' if n == 1 else 'ies'} for agent [cyan]{agent}[/] from [cyan]{db}[/]")
    else:
        size = c.size
        c.clear()
        console.print(f"[green]Cleared[/] {size} entr{'y' if size == 1 else 'ies'} from [cyan]{db}[/]")


@cache_app.command("stats")
def cache_stats(
    db: Path = typer.Argument(
        ...,
        help="Path to the SQLite cache file (e.g. ~/.antcrew/cache.db)",
    ),
) -> None:
    """Show the number of entries stored in a cache file."""
    from antcrew.models.cache import FileLLMCache
    db = Path(db).expanduser()
    if not db.exists():
        console.print(f"[yellow]Cache file not found:[/] {db}")
        raise typer.Exit(1)
    c = FileLLMCache(db)
    console.print(f"[cyan]{db}[/] — [bold]{c.size}[/] entries")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)"),
) -> None:
    """Start the AntCrew REST API server (requires pip install antcrew[server])."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn is required.[/] Install it: [bold]pip install antcrew[server][/]"
        )
        raise typer.Exit(1)

    from pathlib import Path as _Path
    _static_index = _Path(__file__).parent / "static" / "index.html"
    _has_dashboard = _static_index.exists()

    from antcrew import __version__ as _ver
    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    console.print(f"\n[bold green]AntCrew[/] v{_ver}")
    console.print(f"  API       → [cyan]http://{display_host}:{port}[/]")
    console.print(f"  Docs      → [dim]http://{display_host}:{port}/docs[/dim]")
    if _has_dashboard:
        console.print(f"  Dashboard → [bold cyan]http://{display_host}:{port}/ui/[/bold cyan]")
    console.print()
    uvicorn.run("antcrew.server:app", host=host, port=port, reload=reload)


def _extract_state_to_dir(state: dict, output_dir: "Path") -> None:
    """Write code/test/devops artifacts from a state dict to real files on disk."""
    from pathlib import Path as _P

    output_dir = _P(output_dir)
    artifacts: list[tuple[str, str]] = []

    def _collect(key: str):
        for a in state.get(key) or []:
            raw = a if isinstance(a, dict) else (a.model_dump() if hasattr(a, "model_dump") else {})
            if raw.get("file_path") and raw.get("content") is not None:
                artifacts.append((raw["file_path"], raw["content"]))

    _collect("code_artifacts")
    _collect("test_artifacts")
    _collect("devops_artifacts")

    if not artifacts:
        console.print("[yellow]No artifacts to write.[/]")
        return

    console.print(f"\n[bold green]Writing {len(artifacts)} file(s) → [cyan]{output_dir}/[/][/bold green]\n")
    for rel, content in artifacts:
        dest = output_dir / rel.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        console.print(f"  [green]✓[/] {dest}")
    console.print()


@app.command()
def interactive(
    request: str = typer.Argument(..., help="Task or topic for the team"),
    team: str = typer.Option("dev", "--team", "-t", help=f"Team to use: {_TEAM_CHOICES}"),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to agentteam.yaml"
    ),
    thread: str = typer.Option("default", "--thread", help="Thread ID for checkpointing"),
    save: Optional[Path] = typer.Option(
        None, "--save", "-s", help="Save final state to a JSON file (default: antcrew-output.json)"
    ),
    output_dir: Optional[Path] = typer.Option(
        Path("generated"), "--output-dir", "-o",
        help="Write generated files to this directory (default: ./generated). Pass 'none' to skip.",
    ),
    project_dir: Optional[list[str]] = typer.Option(
        None, "--project-dir", "-p",
        help=(
            "Existing project dir(s) to scan before the BA runs. "
            "Single: --project-dir ./src  "
            "Multi: --project-dir frontend:./fe --project-dir backend:./be"
        ),
    ),
) -> None:
    """Run a pipeline with human-in-the-loop review after each agent.

    After every agent you can: approve (continue), reject (stop), fix (send
    back to backend_dev for targeted fixes), edit (open JSON editor), or type
    free-text feedback to trigger conversational refinement.

    Generated files are written to ./generated/ automatically (or --output-dir).
    The raw state is saved to antcrew-output.json (or --save).
    """
    console.print(
        f"\n[bold green]AntCrew interactive[/]  —  "
        f"team=[cyan]{team}[/]  model=[cyan]{model}[/]\n"
    )

    try:
        if config:
            from antcrew import config as cfg_module
            active_team = cfg_module.load(config)
            team = type(active_team).__name__.lower().replace("team", "")
        else:
            active_team = _build_team(team, model, integrations=[])

        # CLI --project-dir overrides whatever is in the YAML.
        if project_dir and hasattr(active_team, "project_dir"):
            parsed: dict[str, str] = {}
            for entry in project_dir:
                if ":" in entry:
                    label, _, path = entry.partition(":")
                    parsed[label.strip()] = path.strip()
                else:
                    from pathlib import Path as _P
                    parsed[_P(entry).name] = entry
            if len(parsed) == 1:
                active_team.project_dir = next(iter(parsed.values()))
                active_team.project_dirs = None
                console.print(f"[dim]Scanning project: [cyan]{active_team.project_dir}[/][/dim]\n")
            elif parsed:
                active_team.project_dirs = parsed
                active_team.project_dir = None
                labels = ", ".join(f"[cyan]{k}[/]" for k in parsed)
                console.print(f"[dim]Scanning components: {labels}[/dim]\n")

        state = active_team.run_interactive(request, thread_id=thread)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"\n[red bold]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print()
    _print_state(state, team)

    # Always save state JSON.
    out_path = save or Path("antcrew-output.json")
    from antcrew.utils.persistence import save_state
    save_state(state, out_path)
    console.print(f"\n[dim]State saved → [cyan]{out_path}[/][/dim]")

    # Resolve output_dir: CLI flag > YAML > default ./generated
    yaml_output_dir = getattr(active_team, "output_dir", None)
    effective_output_dir = output_dir if str(output_dir).lower() != "none" else None
    if effective_output_dir is None and yaml_output_dir:
        effective_output_dir = Path(yaml_output_dir)
    elif effective_output_dir is None:
        effective_output_dir = Path("generated")

    if str(effective_output_dir).lower() != "none":
        _extract_state_to_dir(state, effective_output_dir)

    console.print("\n[bold green]Done![/]\n")


@app.command(name="eval")
def eval_cmd(
    cases_file: Path = typer.Argument(..., help="JSON file with one EvalCase or a list"),
    team: str = typer.Option("dev",  "--team",  "-t", help=f"Team to use: {_TEAM_CHOICES}"),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    judge_model: Optional[str] = typer.Option(
        None, "--judge-model", "-j",
        help="Model for LLM-as-judge scoring (omit to use structural metrics only)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save full JSON results to this file"
    ),
    fail_fast: bool = typer.Option(
        False, "--fail-fast", help="Stop after the first failing case"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-agent scores for every case"
    ),
) -> None:
    """Run evaluation cases from a JSON file and report structural + LLM-judge scores.

    CASES_FILE must contain a JSON object (single case) or array of objects.
    Each object maps to an EvalCase: 'request' is required, everything else
    is optional.

    Example cases.json:
    \b
        [
          {"request": "Build JWT auth", "name": "jwt", "expect_min_tickets": 2},
          {"request": "Add Stripe payments", "name": "stripe"}
        ]

    Exit code is 0 when all cases pass, 1 when any fails — useful in CI.
    """
    from antcrew.config import build_llm
    from antcrew.eval import EvalCase, EvalRunner
    from rich.table import Table

    # ── Load cases ────────────────────────────────────────────────────────────
    if not cases_file.exists():
        console.print(f"[red]File not found:[/] {cases_file}")
        raise typer.Exit(1)

    try:
        raw = json.loads(cases_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/] {exc}")
        raise typer.Exit(1)

    if isinstance(raw, dict):
        raw = [raw]

    try:
        cases = [EvalCase(**c) for c in raw]
    except (TypeError, ValueError) as exc:
        console.print(f"[red]Invalid case format:[/] {exc}")
        raise typer.Exit(1)

    n = len(cases)
    judge_info = f"  judge=[cyan]{judge_model}[/]" if judge_model else ""
    console.print(
        f"\n[bold green]AntCrew eval[/] — "
        f"team=[cyan]{team}[/]  model=[cyan]{model}[/]{judge_info}  "
        f"[dim]{n} case{'s' if n != 1 else ''}[/dim]\n"
    )

    # ── Build runner ──────────────────────────────────────────────────────────
    try:
        llm = build_llm(model)
        active_team = _build_team(team, model, integrations=[], llm=llm)
        judge_llm = build_llm(judge_model) if judge_model else None
        runner = EvalRunner(active_team, judge_llm=judge_llm)
    except Exception as exc:
        console.print(f"[red bold]Setup error:[/] {exc}")
        raise typer.Exit(1)

    # ── Run cases ─────────────────────────────────────────────────────────────
    reports = []
    any_failed = False

    for i, case in enumerate(cases, 1):
        label = case.name or case.request[:40]
        with console.status(f"[bold]Running [{i}/{n}] {label}…[/]"):
            try:
                report = runner.run_one(case)
            except Exception as exc:
                console.print(f"  [{i}/{n}] [red]ERROR[/] {label}: {exc}")
                any_failed = True
                if fail_fast:
                    break
                continue

        reports.append(report)

        status = "[bold green]PASS[/]" if report.passed else "[bold red]FAIL[/]"
        struct = f"{report.overall_score:.2f}"
        judge  = f"{report.judge_score:.2f}" if report.judge_results else "—"
        usage  = report.token_usage or {}
        tokens = f"{usage.get('total_input_tokens', 0) + usage.get('total_output_tokens', 0):,}"
        elapsed = f"{report.elapsed_ms:.0f}ms"

        console.print(
            f"  [{i}/{n}] [{status}]  "
            f"[dim]{label}[/dim]  "
            f"structural=[cyan]{struct}[/]  judge=[cyan]{judge}[/]  "
            f"tokens={tokens}  {elapsed}"
        )

        if verbose:
            for line in report.summary().splitlines()[1:]:
                console.print(f"       [dim]{line}[/dim]")
            if report.errors:
                for err in report.errors:
                    console.print(f"       [red]⚠[/] {err}")

        if not report.passed:
            any_failed = True
            if fail_fast:
                console.print("\n[yellow]--fail-fast: stopping after first failure.[/]")
                break

    if not reports:
        console.print("[yellow]No reports generated.[/]")
        raise typer.Exit(1)

    # ── Summary table ─────────────────────────────────────────────────────────
    console.print()
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    table.add_column("Name",       style="cyan",  no_wrap=True)
    table.add_column("Pass",       justify="center")
    table.add_column("Structural", justify="right")
    table.add_column("Judge",      justify="right")
    table.add_column("Tokens",     justify="right")
    table.add_column("ms",         justify="right")

    passed_count = sum(1 for r in reports if r.passed)

    for r in reports:
        label  = r.case.name or r.case.request[:30]
        tick   = "[green]✓[/]" if r.passed else "[red]✗[/]"
        struct = f"{r.overall_score:.2f}"
        judge  = f"{r.judge_score:.2f}" if r.judge_results else "—"
        usage  = r.token_usage or {}
        tokens = f"{usage.get('total_input_tokens', 0) + usage.get('total_output_tokens', 0):,}"
        ms     = f"{r.elapsed_ms:.0f}"
        table.add_row(label, tick, struct, judge, tokens, ms)

    console.print(table)

    total   = len(reports)
    overall = sum(r.overall_score for r in reports) / total if total else 0.0
    colour  = "green" if passed_count == total else "red"
    console.print(
        f"\n[{colour}]{passed_count}/{total} passed[/{colour}]  "
        f"— avg structural=[cyan]{overall:.2f}[/]\n"
    )

    # ── Save output ───────────────────────────────────────────────────────────
    if output:
        data = runner.to_json(reports)
        output.write_text(data, encoding="utf-8")
        console.print(f"[dim]Results saved → [cyan]{output}[/][/dim]\n")

    if any_failed:
        raise typer.Exit(1)


@app.command()
def show(
    path: Path = typer.Argument(..., help="Path to a JSON state file saved with --save"),
    output_json: bool = typer.Option(
        False, "--json", help="Print raw JSON instead of rich output"
    ),
) -> None:
    """Display a previously saved pipeline state."""
    from antcrew.utils.persistence import load_state

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    raw = load_state(path)

    console.print(f"\n[bold green]AntCrew show[/] — [cyan]{path}[/]\n")

    if output_json:
        console.print_json(json.dumps(raw))
        return

    # ── detect team type from available keys ─────────────────────────────────
    if raw.get("prd") or raw.get("tickets") or raw.get("code_artifacts"):
        _print_state_raw(raw, "dev")
    elif raw.get("research_document"):
        _print_state_raw(raw, "research")
    elif raw.get("content_piece"):
        _print_state_raw(raw, "content")
    else:
        _print_state_raw(raw, "dev")

    console.print()


@app.command()
def extract(
    path: Path = typer.Argument(..., help="JSON state file (--save output) or project JSON"),
    output: Path = typer.Option(
        Path("output"), "--output", "-o", help="Directory to write files into"
    ),
    include_tests: bool = typer.Option(True, "--tests/--no-tests", help="Also write test artifacts"),
    include_devops: bool = typer.Option(True, "--devops/--no-devops", help="Also write devops artifacts"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List files that would be written without writing"),
) -> None:
    """Write generated code artifacts from a saved state to disk as real files."""
    from antcrew.utils.persistence import load_state

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    raw = load_state(path)

    # Support both plain state files and project files (which nest state under "state").
    if "state" in raw and isinstance(raw["state"], dict):
        raw = raw["state"]

    artifacts: list[tuple[str, str]] = []  # (rel_path, content)

    for a in raw.get("code_artifacts") or []:
        if isinstance(a, dict) and a.get("file_path") and a.get("content") is not None:
            artifacts.append((a["file_path"], a["content"]))

    if include_tests:
        for a in raw.get("test_artifacts") or []:
            if isinstance(a, dict) and a.get("file_path") and a.get("content") is not None:
                artifacts.append((a["file_path"], a["content"]))

    if include_devops:
        for a in raw.get("devops_artifacts") or []:
            if isinstance(a, dict) and a.get("file_path") and a.get("content") is not None:
                artifacts.append((a["file_path"], a["content"]))

    if not artifacts:
        console.print("[yellow]No artifacts found in the state file.[/]")
        raise typer.Exit(0)

    console.print(f"\n[bold green]AntCrew extract[/] — {len(artifacts)} file(s) → [cyan]{output}/[/]\n")

    for rel, content in artifacts:
        dest = output / rel.lstrip("/")
        if dry_run:
            console.print(f"  [dim](dry-run)[/] {dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            console.print(f"  [green]✓[/] {dest}")

    if not dry_run:
        console.print(f"\n[bold green]Done![/] {len(artifacts)} file(s) written to [cyan]{output}/[/]\n")


@app.command()
def describe(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to agentteam.yaml"),
    team: str = typer.Option("dev", "--team", "-t", help=f"Team preset: {_TEAM_CHOICES}"),
) -> None:
    """Show pipeline agents, data flow (consumes/produces), and coherence check.

    \b
    Examples:
        antcrew describe --team dev
        antcrew describe --config agentteam.yaml
    """
    import json as _json

    from rich.table import Table

    # ── Parse config file (YAML/JSON) without instantiating the LLM ─────────
    cfg: dict = {}
    pipeline_name = team
    model_str = "claude"

    if config:
        if not config.exists():
            console.print(f"[red]Config file not found:[/] {config}")
            raise typer.Exit(1)
        try:
            raw_text = config.read_text(encoding="utf-8")
            if config.suffix.lower() == ".json":
                cfg = _json.loads(raw_text)
            else:
                try:
                    import yaml as _yaml  # type: ignore[import]
                    cfg = _yaml.safe_load(raw_text) or {}
                except ImportError:
                    console.print(
                        "[red]PyYAML required for YAML files.[/]  "
                        "Install: [bold]pip install pyyaml[/]"
                    )
                    raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Failed to read config:[/] {exc}")
            raise typer.Exit(1)

        pipeline_name = config.stem
        team = cfg.get("team", team).lower()
        model_str = cfg.get("model", model_str)

    # ── Agent class registry (no instantiation needed) ────────────────────────
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.agents.pm import PMAgent
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent
    from antcrew.agents.qa import QAAgent
    from antcrew.agents.reviewer import ReviewerAgent
    from antcrew.agents.devops import DevOpsAgent
    from antcrew.agents.doc_writer import DocWriterAgent
    from antcrew.agents.researcher import ResearcherAgent
    from antcrew.agents.idea import IdeaAgent
    from antcrew.agents.copywriter import CopywriterAgent
    from antcrew.agents.editor import EditorAgent
    from antcrew.agents.codebase_scanner import CodebaseScannerAgent
    from antcrew.agents.sprint_planner import SprintPlannerAgent

    _CLASSES: dict[str, type] = {
        "business_analyst": BusinessAnalystAgent,
        "pm":               PMAgent,
        "backend_dev":      BackendDevAgent,
        "frontend_dev":     FrontendDevAgent,
        "qa":               QAAgent,
        "reviewer":         ReviewerAgent,
        "devops":           DevOpsAgent,
        "doc_writer":       DocWriterAgent,
        "researcher":       ResearcherAgent,
        "idea":             IdeaAgent,
        "copywriter":       CopywriterAgent,
        "writer":           CopywriterAgent,   # alias used by research team
        "editor":           EditorAgent,
        "codebase_scanner": CodebaseScannerAgent,
        "sprint_planner":   SprintPlannerAgent,
    }

    _DEFAULT_ORDER: dict[str, list[str]] = {
        "dev":       ["business_analyst", "pm", "backend_dev"],
        "fullstack": [
            "codebase_scanner", "business_analyst", "pm", "sprint_planner",
            "backend_dev", "frontend_dev", "qa", "reviewer", "devops", "doc_writer",
        ],
        "research":  ["researcher", "writer"],
        "content":   ["idea", "copywriter", "editor"],
    }

    # ── Determine agent order ─────────────────────────────────────────────────
    if "flow" in cfg:
        # Unique ordered list: first appearance in each edge wins.
        seen: list[str] = []
        for step in cfg["flow"]:
            for node in list(step)[:2]:  # skip optional condition (3rd element)
                if node not in seen:
                    seen.append(str(node))
        ordered: list[str] = seen
    else:
        base = list(_DEFAULT_ORDER.get(team, ["business_analyst", "pm", "backend_dev"]))
        extra = [k for k in (cfg.get("agents") or {}) if k not in base]
        ordered = base + extra

    # ── Header ────────────────────────────────────────────────────────────────
    console.print(
        f"\n[bold green]Pipeline:[/] [cyan]{pipeline_name}[/]  "
        f"[dim]team={team}  model={model_str}[/dim]\n"
    )

    # ── Table ─────────────────────────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("Agent",    style="cyan",  no_wrap=True, min_width=18)
    table.add_column("Consumes", style="white", min_width=30)
    table.add_column("Produces", style="green")

    for agent_name in ordered:
        cls = _CLASSES.get(agent_name)
        consumed = list(getattr(cls, "consumes", [])) if cls else []
        produced = list(getattr(cls, "produces", [])) if cls else []
        table.add_row(
            agent_name,
            ", ".join(consumed) if consumed else "—",
            ", ".join(produced) if produced else "—",
        )

    console.print(table)
    console.print()

    # ── Coherence check (unknown agent names) ─────────────────────────────────
    unknown = [n for n in ordered if n not in _CLASSES]
    if unknown:
        console.print(
            f"[bold yellow]Coherencia:[/] {len(unknown)} unknown agent(s): "
            + ", ".join(unknown)
            + "\n"
        )
    else:
        console.print("[bold green]Coherencia:[/] OK\n")


def _print_state_raw(raw: dict, team: str) -> None:
    """Like _print_state but works on plain dicts from load_state."""
    from antcrew.core.artifacts import (
        CodeArtifact, ContentPiece, DevOpsArtifact,
        PRD, ResearchDocument, Ticket,
    )

    def _maybe(cls, data):
        if data is None:
            return None
        try:
            return cls.model_validate(data)
        except Exception:
            return None

    if team in ("dev", "fullstack"):
        prd = _maybe(PRD, raw.get("prd"))
        if prd:
            console.print(Panel(
                f"[bold]{prd.title}[/]\n{prd.summary}",
                title="PRD", border_style="blue",
            ))

        tickets_raw = raw.get("tickets") or []
        tickets = [t for t in (_maybe(Ticket, t) for t in tickets_raw) if t]
        if tickets:
            console.print(Panel(
                "\n".join(f"  [{t.priority.value}] {t.id}: {t.title}" for t in tickets),
                title=f"Tickets ({len(tickets)})", border_style="yellow",
            ))

        for a_raw in (raw.get("code_artifacts") or []):
            a = _maybe(CodeArtifact, a_raw)
            if a:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  ({a.ticket_id})", border_style="green",
                ))

        for a_raw in (raw.get("devops_artifacts") or []):
            a = _maybe(DevOpsArtifact, a_raw)
            if a:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  [dim](devops)[/dim]", border_style="cyan",
                ))

    elif team == "research":
        doc = _maybe(ResearchDocument, raw.get("research_document"))
        if doc:
            console.print(Panel(
                "\n".join(f"• {f}" for f in doc.key_findings),
                title=f"[bold]{doc.title}[/] — Key Findings", border_style="cyan",
            ))
        piece = _maybe(ContentPiece, raw.get("content_piece"))
        if piece and piece.body:
            console.print(Panel(
                piece.body[:2000] + (" …" if len(piece.body) > 2000 else ""),
                title=piece.title, border_style="blue",
            ))

    elif team == "content":
        piece = _maybe(ContentPiece, raw.get("content_piece"))
        if piece:
            console.print(Panel(
                piece.body[:3000] + (" …" if len(piece.body) > 3000 else ""),
                title=f"{piece.title}  [dim]({piece.word_count or '?'} words)[/dim]",
                border_style="magenta",
            ))

    if raw.get("test_results") is not None:
        _print_test_results(raw["test_results"])

    if raw.get("errors"):
        for err in raw["errors"]:
            console.print(f"[red]Error:[/] {err}")

    # ── metadata footer ───────────────────────────────────────────────────────
    request = raw.get("request", "")
    agent   = raw.get("current_agent", "")
    if request or agent:
        console.print(
            f"\n[dim]Request: {request}  |  Last agent: {agent}[/dim]"
        )


# ---------------------------------------------------------------------------
# antcrew project run / show / history
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Template strings
# ---------------------------------------------------------------------------

_YAML_DEV = """\
# AntCrew — Dev Team configuration
# Default pipeline: BusinessAnalyst → PM → BackendDev
team: dev
model: claude          # claude | gpt-4o | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides (Level 2)
# agents:
#   backend_dev:
#     model: ollama:llama3
#     approval_required: true
#     response_options: [approve, reject]
#   devops:
#     model: claude
#     approval_required: true

# Optional: HITL channel (uncomment one)
# channel:
#   type: console

# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# channel:
#   type: telegram
#   token: ${BOT_TOKEN}
#   chat_id: ${CHAT_ID}

# Optional: extended pipeline (Level 3)
# flow:
#   - [business_analyst, pm]
#   - [pm, backend_dev]
#   - [pm, frontend_dev]     # parallel frontend track
#   - [backend_dev, qa]
#   - [frontend_dev, qa]
#   - [qa, reviewer]
#   - [backend_dev, devops]  # add DevOps step

# Optional: persistent LLM response cache (avoids repeated API calls)
# cache: ~/.antcrew/cache.db

# Optional: persistent project sessions (state accumulates across runs)
# project: ./my-project.json
"""

_MAIN_DEV = '''\
import os
from antcrew import DevTeam, save_state
from antcrew.models.anthropic_model import AnthropicModel

team = DevTeam(model=AnthropicModel())
state = team.run("Build a REST API with JWT authentication")

# ── Print code artifacts ──────────────────────────────────────────────────────
if state.get("code_artifacts"):
    for artifact in state["code_artifacts"]:
        print(f"\\n--- {artifact.file_path} ---")
        print(artifact.content)

# ── Print DevOps artifacts ────────────────────────────────────────────────────
if state.get("devops_artifacts"):
    for artifact in state["devops_artifacts"]:
        print(f"\\n--- {artifact.file_path} ({artifact.language}) ---")
        print(artifact.content)

# ── Save state to JSON (optional) ────────────────────────────────────────────
save_state(state, "output/run.json")
print("\\nState saved to output/run.json")

# ── Sync tickets to Jira (optional) ──────────────────────────────────────────
# from antcrew import JiraIntegration
# jira = JiraIntegration(
#     url=os.environ["JIRA_URL"],
#     email=os.environ["JIRA_EMAIL"],
#     api_token=os.environ["JIRA_API_TOKEN"],
#     project_key=os.environ.get("JIRA_PROJECT_KEY", "DEV"),
# )
# pairs = jira.sync_tickets(state["tickets"] or [])
# for ticket, key in pairs:
#     print(f"  {ticket.id} → {key}")

# ── Open GitHub PR with code artifacts (optional) ────────────────────────────
# from antcrew import GitHubIntegration
# gh = GitHubIntegration(
#     token=os.environ["GITHUB_TOKEN"],
#     repo=os.environ["GITHUB_REPO"],   # "your-org/your-repo"
# )
# pr_url = gh.create_pr(state)
# print(f"PR: {pr_url}")
'''

_YAML_FULLSTACK = """\
# AntCrew — Full-Stack Team configuration
# Pipeline: BA → PM → BackendDev → FrontendDev → QA → Reviewer → DevOps → DocWriter
team: fullstack
model: claude          # claude | gpt-4o | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides
# agents:
#   pm:
#     model: claude-sonnet-4-6  # stronger model for product thinking
#     approval_required: true   # pause and review tickets before coding starts
#   frontend_dev:
#     model: gpt-4o             # GPT-4o for frontend code
#   reviewer:
#     approval_required: true   # mandatory human review before DevOps runs
#   devops:
#     model: ollama:llama3      # local model for CI/CD templates

# Optional: HITL channel (uncomment one)
# channel:
#   type: console

# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# Optional: custom pipeline (skip or reorder steps)
# flow:
#   - [business_analyst, pm]
#   - [pm, backend_dev]
#   - [pm, frontend_dev]          # run frontend in parallel with backend
#   - [backend_dev, qa]
#   - [frontend_dev, qa]
#   - [qa, reviewer, no_critical_bugs]
#   - [qa, backend_dev, has_critical_bugs]
#   - [reviewer, devops]
#   - [devops, doc_writer]
"""

_MAIN_FULLSTACK = '''\
import os
from antcrew import FullStackTeam, save_state
from antcrew.models import AnthropicModel

team = FullStackTeam(model=AnthropicModel())
state = team.run("Build a task management app with a REST API and React frontend")

# ── Backend code ─────────────────────────────────────────────────────────────
if state.get("code_artifacts"):
    print(f"\\nBackend: {len(state[\'code_artifacts\'])} files")
    for a in state["code_artifacts"]:
        print(f"  {a.file_path}")

# ── Frontend code ─────────────────────────────────────────────────────────────
# (frontend artifacts share the code_artifacts key)

# ── DevOps artifacts ──────────────────────────────────────────────────────────
if state.get("devops_artifacts"):
    print(f"\\nDevOps: {len(state[\'devops_artifacts\'])} files")
    for a in state["devops_artifacts"]:
        print(f"  {a.file_path}  ({a.language})")

# ── Documentation ─────────────────────────────────────────────────────────────
if state.get("doc_artifacts"):
    print(f"\\nDocs: {len(state[\'doc_artifacts\'])} files")
    for a in state["doc_artifacts"]:
        print(f"  {a.file_path}  ({a.doc_type})")

# ── Code review verdict ───────────────────────────────────────────────────────
if state.get("review"):
    print(f"\\nCode review: {state[\'review\'].verdict.upper()}")
    print(f"  {state[\'review\'].summary}")

# ── Save state ────────────────────────────────────────────────────────────────
save_state(state, "output/fullstack_run.json")
print("\\nState saved to output/fullstack_run.json")

# ── Publish docs to Confluence (optional) ─────────────────────────────────────
# from antcrew import ConfluenceIntegration
# confluence = ConfluenceIntegration(
#     url=os.environ["CONFLUENCE_URL"],
#     email=os.environ["CONFLUENCE_EMAIL"],
#     api_token=os.environ["CONFLUENCE_TOKEN"],
# )
# confluence.publish_docs(state, space_key="ENG")

# ── Open a GitHub PR (optional) ───────────────────────────────────────────────
# from antcrew import GitHubIntegration
# gh = GitHubIntegration(token=os.environ["GITHUB_TOKEN"], repo="myorg/myapp")
# pr_url = gh.create_pr(state)
# print(f"PR: {pr_url}")
'''

_YAML_RESEARCH = """\
# AntCrew — Research Team configuration
# Pipeline: ResearcherAgent → CopywriterAgent (writer)
team: research
model: claude          # claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides
# agents:
#   researcher:
#     model: ollama:llama3      # run researcher locally
#     approval_required: true   # pause for review after research
#   writer:
#     model: gpt-4o             # use GPT-4o for writing

# Optional: Console HITL channel (pauses in terminal for review)
# channel:
#   type: console

# Optional: Slack HITL channel
# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# Optional: custom flow (Level 3)
# Add an editor step after writing:
# flow:
#   - [researcher, writer]
#   - [writer, editor]
"""

_MAIN_RESEARCH = '''\
from antcrew import ResearchTeam
from antcrew.models import AnthropicModel

team = ResearchTeam(model=AnthropicModel())
state = team.run("What are the main challenges in deploying LLMs at scale?")

doc = state.get("research_document")
if doc:
    print(f"\\n=== {doc.title} ===")
    print(f"Topic: {doc.topic}")
    print()
    for finding in doc.key_findings:
        print(f"• {finding}")
    print()
    for section in doc.sections:
        print(f"## {section.heading}")
        print(section.content)
        print()

piece = state.get("content_piece")
if piece and piece.body:
    print(f"\\n--- Written Report ({piece.word_count or \'?\'} words) ---")
    print(piece.body)
'''

_YAML_CONTENT = """\
# AntCrew — Content Team configuration
# Pipeline: IdeaAgent → CopywriterAgent → EditorAgent
team: content
model: claude          # claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides
# agents:
#   idea:
#     model: claude
#     approval_required: true   # review the brief before writing
#   copywriter:
#     model: gpt-4o             # use GPT-4o for the body
#     approval_required: true   # review the draft before editing
#   editor:
#     approval_required: true   # review the final edit

# Optional: Console HITL channel (pauses in terminal for review)
# channel:
#   type: console

# Optional: Slack HITL channel
# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# Optional: skip the editor (two-stage pipeline)
# flow:
#   - [idea, copywriter]
"""

_MAIN_CONTENT = '''\
from antcrew import ContentTeam
from antcrew.models import AnthropicModel

team = ContentTeam(model=AnthropicModel())
state = team.run("Write a blog post about multi-agent AI frameworks for software engineers")

piece = state.get("content_piece")
if piece:
    print(f"\\n=== {piece.title} ===")
    print(f"Audience : {piece.target_audience}")
    print(f"Tone     : {piece.tone}")
    print(f"Words    : {piece.word_count or \'?\'}")
    print()
    print(piece.body)
'''


_YAML_CUSTOM = """\
# AntCrew — Custom Team configuration
# Define your own pipeline as a sequence of YAML-configured agents.
# No Python required: each step is a TemplateAgent run in order.
team: custom
model: claude          # claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated

steps:
  - name: planner
    system_prompt: |
      You are an expert project planner.
      Break the following task into a clear, numbered step-by-step plan.
      Output only the plan — no preamble, no explanations.
    output_key: plan

  - name: executor
    system_prompt: |
      You are a senior software engineer.
      Implement the following plan and return production-ready code with brief comments.

      Plan:
      {plan}
    input_key: plan
    output_key: code

  - name: reviewer
    system_prompt: |
      You are a code reviewer.
      Review the code below for correctness, security, and clarity.
      Provide concise, actionable feedback.

      Code:
      {code}
    input_key: code
    output_key: review

# Optional: per-step model override — add 'model:' inside the step block
# (not yet supported; use a Python Pipeline of CustomTeams for mixed models)

# Optional: LLM response cache
# cache: ~/.antcrew/cache.db

# Optional: per-run cost limit
# max_cost_usd: 2.00
"""

_MAIN_CUSTOM = '''\
from antcrew import CustomTeam
from antcrew.models import AnthropicModel

team = CustomTeam(
    steps=[
        {
            "name": "planner",
            "system_prompt": "Break the task into a numbered step-by-step plan.",
            "output_key": "plan",
        },
        {
            "name": "executor",
            "system_prompt": "Implement the following plan:\\n\\n{plan}",
            "input_key": "plan",
            "output_key": "code",
        },
        {
            "name": "reviewer",
            "system_prompt": "Review this code for correctness and security:\\n\\n{code}",
            "input_key": "code",
            "output_key": "review",
        },
    ],
    llm=AnthropicModel(),
)

result = team.run("Build a JWT authentication module")

print("\\n=== Plan ===")
print(result.get("plan", ""))
print("\\n=== Code ===")
print(result.get("code", ""))
print("\\n=== Review ===")
print(result.get("review", ""))
'''


# ---------------------------------------------------------------------------
# antcrew flow show / antcrew flow validate
# ---------------------------------------------------------------------------

@_flow_app.command("show")
def flow_show(
    flow_file: Path = typer.Argument(..., help="Flow YAML or JSON file"),
) -> None:
    """Display a flow definition as a formatted graph.

    The file can be a standalone list of edges OR a full team config with
    a 'flow:' key.  Both .yaml/.yml and .json are supported.
    """
    from antcrew.flow import load_flow, format_flow, validate_flow

    if not flow_file.exists():
        console.print(f"[red]File not found:[/] {flow_file}")
        raise typer.Exit(1)

    try:
        flow = load_flow(flow_file)
    except (ValueError, ImportError, Exception) as exc:
        console.print(f"[red]Could not load flow:[/] {exc}")
        raise typer.Exit(1)

    errors = validate_flow(flow)

    console.print(f"\n[bold green]Flow[/] — [cyan]{flow_file}[/]\n")
    console.print(format_flow(flow), markup=False, highlight=False)

    if errors:
        console.print("\n[yellow]Warnings:[/]")
        for err in errors:
            console.print(f"  [yellow]⚠[/] {err}")
    console.print()


@_flow_app.command("validate")
def flow_validate(
    flow_file: Path = typer.Argument(..., help="Flow YAML or JSON file"),
    strict: bool = typer.Option(
        False, "--strict", help="Exit 1 on any unknown agent name"
    ),
) -> None:
    """Validate a flow definition and report errors.

    Exit code 0 = valid, 1 = errors found (or --strict + warnings).
    """
    from antcrew.flow import load_flow, validate_flow

    if not flow_file.exists():
        console.print(f"[red]File not found:[/] {flow_file}")
        raise typer.Exit(1)

    try:
        flow = load_flow(flow_file)
    except (ValueError, ImportError) as exc:
        console.print(f"[red]Parse error:[/] {exc}")
        raise typer.Exit(1)

    errors = validate_flow(flow)

    if not errors:
        agents = len({n for step in flow for n in step[:2]})
        console.print(
            f"[bold green]✓ valid[/]  —  "
            f"[dim]{len(flow)} edge(s), {agents} agent(s)[/dim]"
        )
    else:
        console.print(f"[bold red]✗ {len(errors)} error(s):[/]")
        for err in errors:
            console.print(f"  [red]•[/] {err}")
        if strict or True:  # always exit 1 when errors found
            raise typer.Exit(1)


# ---------------------------------------------------------------------------
# antcrew trace — inspect TraceLog SQLite files
# ---------------------------------------------------------------------------

@app.command(name="trace")
def trace_cmd(
    db: Path = typer.Argument(..., help="TraceLog SQLite file (created with --trace)"),
    run_id: Optional[str] = typer.Option(
        None, "--run", "-r", help="Show agent calls for a specific run ID"
    ),
    thread: Optional[str] = typer.Option(
        None, "--thread", help="Show the latest run for a thread_id"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max runs to list"),
) -> None:
    """Inspect a TraceLog SQLite file — list runs or show per-agent call detail.

    \b
    List recent runs:
        antcrew trace ~/.antcrew/trace.db

    Show agent calls for a specific run:
        antcrew trace ~/.antcrew/trace.db --run <run_id>

    Show latest run for a thread:
        antcrew trace ~/.antcrew/trace.db --thread sprint-1
    """
    from antcrew.trace import TraceLog as _TraceLog
    from rich.table import Table

    if not db.exists():
        console.print(f"[red]File not found:[/] {db}")
        raise typer.Exit(1)

    tlog = _TraceLog(db)

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
    console.print(f"\n[dim]Use --run <id> or --thread <id> to inspect agent calls.[/dim]")


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

    tbl = Table(show_header=True, header_style="bold dim")
    tbl.add_column("#",             style="dim",    justify="right", no_wrap=True)
    tbl.add_column("Agent",         style="cyan",   no_wrap=True)
    tbl.add_column("Duration",      justify="right", no_wrap=True)
    tbl.add_column("In tokens",     justify="right")
    tbl.add_column("Out tokens",    justify="right")
    tbl.add_column("Cost",          justify="right")
    tbl.add_column("Prompt (first 80 chars)", max_width=80)

    for i, c in enumerate(calls, 1):
        dur = c.get("duration_ms") or 0.0
        dur_str = f"{dur:.0f}ms" if dur < 1000 else f"{dur/1000:.1f}s"
        call_cost = c.get("cost_usd") or 0.0
        cost_str = f"${call_cost:.4f}" if call_cost else "—"
        prompt = (c.get("prompt_snippet") or "")[:80]
        tbl.add_row(
            str(i),
            c["agent_name"],
            dur_str,
            str(c.get("input_tokens", 0)),
            str(c.get("output_tokens", 0)),
            cost_str,
            prompt,
        )

    console.print(tbl)


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


@app.command(name="publish")
def publish_cmd(
    state_file: Path = typer.Argument(
        ..., help="JSON state file produced by 'antcrew run --output …' or save_state()."
    ),
    github: bool = typer.Option(False, "--github", help="Open a GitHub PR with code artifacts."),
    github_token: Optional[str] = typer.Option(
        None, "--token", envvar="GITHUB_TOKEN", help="GitHub personal access token.",
    ),
    github_repo: Optional[str] = typer.Option(
        None, "--repo", help="GitHub repo in 'owner/repo' format.",
    ),
    github_base: str = typer.Option("main", "--base", help="Base branch for the PR."),
    confluence: bool = typer.Option(
        False, "--confluence", help="Publish PRD and docs to Confluence."
    ),
    confluence_url: Optional[str] = typer.Option(
        None, "--confluence-url", envvar="CONFLUENCE_URL",
        help="Atlassian base URL (e.g. https://yourorg.atlassian.net).",
    ),
    confluence_email: Optional[str] = typer.Option(
        None, "--confluence-email", envvar="CONFLUENCE_EMAIL",
    ),
    confluence_token: Optional[str] = typer.Option(
        None, "--confluence-token", envvar="CONFLUENCE_API_TOKEN",
    ),
    confluence_space: Optional[str] = typer.Option(
        None, "--space", help="Confluence space key (e.g. DEV).",
    ),
    confluence_parent: Optional[str] = typer.Option(
        None, "--parent", help="Optional parent page title in Confluence.",
    ),
) -> None:
    """Publish pipeline output to GitHub (PR) and/or Confluence.

    \b
    Open a GitHub PR with all generated code:
        antcrew publish run_output.json \\
          --github --repo my-org/my-repo

    \b
    Publish PRD + docs to Confluence:
        antcrew publish run_output.json \\
          --confluence --confluence-url https://myorg.atlassian.net \\
          --space DEV

    \b
    Both at once:
        antcrew publish run_output.json --github --repo org/repo --confluence ...
    """
    if not github and not confluence:
        console.print("[yellow]Nothing to publish.[/] Specify --github and/or --confluence.")
        raise typer.Exit(0)

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    # Load and re-hydrate the state dict into Pydantic objects
    from antcrew.utils.persistence import load_state as _load_state
    from antcrew.core.artifacts import (
        PRD, CodeArtifact, DevOpsArtifact, DocumentationArtifact,
        ResearchDocument, Ticket,
    )

    raw = _load_state(state_file)

    def _hydrate(raw_state: dict) -> dict:
        state: dict = dict(raw_state)
        if state.get("prd"):
            try:
                state["prd"] = PRD.model_validate(state["prd"])
            except Exception:
                pass
        for key, cls in [
            ("code_artifacts", CodeArtifact),
            ("devops_artifacts", DevOpsArtifact),
            ("doc_artifacts", DocumentationArtifact),
            ("tickets", Ticket),
        ]:
            if state.get(key):
                try:
                    state[key] = [cls.model_validate(a) for a in state[key]]
                except Exception:
                    pass
        if state.get("research_document"):
            try:
                state["research_document"] = ResearchDocument.model_validate(
                    state["research_document"]
                )
            except Exception:
                pass
        return state

    state = _hydrate(raw)

    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------
    if github:
        if not github_token:
            console.print(
                "[red]Error:[/] --token / $GITHUB_TOKEN is required for --github."
            )
            raise typer.Exit(1)
        if not github_repo:
            console.print("[red]Error:[/] --repo is required for --github.")
            raise typer.Exit(1)

        from antcrew.integrations.github import GitHubIntegration
        gh = GitHubIntegration(
            token=github_token, repo=github_repo, base_branch=github_base
        )
        with console.status("Opening GitHub PR…"):
            try:
                pr_url = gh.create_pr(state)
            except Exception as exc:
                console.print(f"[red]GitHub error:[/] {exc}")
                raise typer.Exit(1)
        console.print(f"[green]PR opened:[/] {pr_url}")

    # ------------------------------------------------------------------
    # Confluence
    # ------------------------------------------------------------------
    if confluence:
        missing = [
            f for f, v in [
                ("--confluence-url", confluence_url),
                ("--confluence-email", confluence_email),
                ("--confluence-token", confluence_token),
                ("--space", confluence_space),
            ]
            if not v
        ]
        if missing:
            console.print(
                f"[red]Error:[/] Missing required options for --confluence: "
                + ", ".join(missing)
            )
            raise typer.Exit(1)

        from antcrew.integrations.confluence import ConfluenceIntegration
        cf = ConfluenceIntegration(
            url=confluence_url,  # type: ignore[arg-type]
            email=confluence_email,  # type: ignore[arg-type]
            api_token=confluence_token,  # type: ignore[arg-type]
        )
        published: list[str] = []
        with console.status("Publishing to Confluence…"):
            try:
                if state.get("prd"):
                    page = cf.publish_prd(
                        state,
                        confluence_space,  # type: ignore[arg-type]
                        parent_title=confluence_parent,
                    )
                    if page:
                        published.append(f"PRD — {state['prd'].title}")

                if state.get("research_document"):
                    page = cf.publish_research(
                        state,
                        confluence_space,  # type: ignore[arg-type]
                        parent_title=confluence_parent,
                    )
                    if page:
                        doc = state["research_document"]
                        published.append(f"Research — {doc.title}")

                doc_pages = cf.publish_docs(
                    state,
                    confluence_space,  # type: ignore[arg-type]
                    parent_title=confluence_parent,
                )
                published.extend(f"Doc — {p.get('title', '?')}" for p in doc_pages)
            except Exception as exc:
                console.print(f"[red]Confluence error:[/] {exc}")
                raise typer.Exit(1)

        if published:
            console.print("[green]Published to Confluence:[/]")
            for item in published:
                console.print(f"  • {item}")
        else:
            console.print("[yellow]Nothing to publish to Confluence[/] — no PRD, research, or doc artifacts found.")


@app.command(name="benchmark")
def benchmark_cmd(
    cases_file: Path = typer.Argument(
        ...,
        help=(
            "JSON file with a list of benchmark cases.  Each entry: "
            '{"request": "...", "team": "dev", "label": "optional"}.'
        ),
    ),
    model_name: str = typer.Option("claude-haiku-4-5-20251001", "--model", "-m", help="Model ID."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write results JSON to this file."
    ),
    parallel: int = typer.Option(1, "--parallel", "-p", help="Number of cases to run in parallel."),
    timeout: float = typer.Option(300.0, "--timeout", help="Per-case timeout in seconds (0 = no limit)."),
) -> None:
    """Run a batch of pipeline requests and compare metrics.

    \b
    benchmark.json:
    [
      {"request": "Build a login module",   "team": "dev",     "label": "Login"},
      {"request": "Write about AI safety",  "team": "research","label": "Research"}
    ]

    \b
    antcrew benchmark benchmark.json
    antcrew benchmark benchmark.json --parallel 3 --output results.json
    """
    import concurrent.futures
    import time as _time
    from antcrew.config import build_llm

    if not cases_file.exists():
        console.print(f"[red]File not found:[/] {cases_file}")
        raise typer.Exit(1)

    try:
        raw = json.loads(cases_file.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Failed to parse cases file:[/] {exc}")
        raise typer.Exit(1)

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        console.print("[red]Cases file must contain a JSON array of case objects.[/]")
        raise typer.Exit(1)

    def _run_case(idx: int, case: dict) -> dict:
        label = case.get("label") or f"case-{idx + 1}"
        req   = case.get("request", "")
        t     = (case.get("team") or "dev").lower()

        if not req:
            return {"label": label, "status": "skipped", "reason": "empty request",
                    "cost_usd": 0.0, "elapsed_s": 0.0, "artifacts": {}}

        llm = build_llm(case.get("model") or model_name)

        def _mk_team():
            if t == "dev":
                from antcrew.teams.dev_team import DevTeam
                return DevTeam(model=llm)
            if t in ("fullstack", "full"):
                from antcrew.teams.fullstack_team import FullStackTeam
                return FullStackTeam(model=llm)
            if t == "research":
                from antcrew.teams.research_team import ResearchTeam
                return ResearchTeam(model=llm)
            if t == "content":
                from antcrew.teams.content_team import ContentTeam
                return ContentTeam(model=llm)
            raise ValueError(f"Unknown team: {t!r}")

        start = _time.monotonic()
        try:
            team_inst = _mk_team()
            result = team_inst.run(req, thread_id=f"bench-{idx}")
            elapsed = _time.monotonic() - start
            state = result.state
            artifacts: dict[str, int] = {}
            for key in ("tickets", "code_artifacts", "test_artifacts",
                        "devops_artifacts", "doc_artifacts"):
                v = state.get(key)
                if isinstance(v, list):
                    artifacts[key] = len(v)
            if state.get("prd"):
                artifacts["prd"] = 1
            if state.get("research_document"):
                artifacts["research_document"] = 1
            if state.get("content_piece"):
                artifacts["content_piece"] = 1
            return {
                "label": label,
                "request": req,
                "team": t,
                "status": "ok",
                "cost_usd": result.cost_usd,
                "elapsed_s": round(elapsed, 2),
                "artifacts": artifacts,
            }
        except Exception as exc:
            elapsed = _time.monotonic() - start
            return {
                "label": label,
                "request": req,
                "team": t,
                "status": "error",
                "reason": str(exc),
                "cost_usd": 0.0,
                "elapsed_s": round(elapsed, 2),
                "artifacts": {},
            }

    console.print(
        f"\n[bold green]antcrew benchmark[/]  "
        f"{len(raw)} case(s)  parallel={parallel}  model={model_name}\n"
    )

    results: list[dict] = [{}] * len(raw)
    with console.status("Running benchmark…") as status:
        if parallel <= 1:
            for i, case in enumerate(raw):
                status.update(f"Running {i + 1}/{len(raw)}…")
                results[i] = _run_case(i, case)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
                futs = {pool.submit(_run_case, i, c): i for i, c in enumerate(raw)}
                for fut in concurrent.futures.as_completed(futs):
                    idx = futs[fut]
                    results[idx] = fut.result()

    # ── Results table ─────────────────────────────────────────────────────────
    from rich.table import Table

    tbl = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    tbl.add_column("#",         style="dim",   width=3)
    tbl.add_column("Label",     min_width=14)
    tbl.add_column("Team",      width=9)
    tbl.add_column("Status",    width=7)
    tbl.add_column("Elapsed",   width=8, justify="right")
    tbl.add_column("Cost USD",  width=9, justify="right")
    tbl.add_column("Artifacts", min_width=18)

    total_cost = 0.0
    total_time = 0.0
    ok_count   = 0

    for i, r in enumerate(results, 1):
        status_str = r.get("status", "?")
        status_col = (
            "[green]ok[/green]"    if status_str == "ok"      else
            "[yellow]skip[/yellow]" if status_str == "skipped" else
            "[red]error[/red]"
        )
        arts = r.get("artifacts") or {}
        arts_str = "  ".join(f"{k}:{v}" for k, v in arts.items()) or "[dim]—[/dim]"
        cost = r.get("cost_usd") or 0.0
        elapsed = r.get("elapsed_s") or 0.0
        total_cost += cost
        total_time += elapsed
        if status_str == "ok":
            ok_count += 1

        tbl.add_row(
            str(i),
            r.get("label", ""),
            r.get("team", ""),
            status_col,
            f"{elapsed:.1f}s",
            f"${cost:.4f}",
            arts_str,
        )

    console.print(tbl)
    console.print(
        f"\n[dim]Total: {ok_count}/{len(raw)} ok  "
        f"time={total_time:.1f}s  cost=${total_cost:.4f}[/dim]\n"
    )

    if output:
        output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        console.print(f"[dim]Results written → [cyan]{output}[/][/dim]\n")


@app.command(name="watch")
def watch_cmd(
    watch_path: Path = typer.Argument(..., help="File or directory to watch for changes."),
    request: Optional[str] = typer.Option(
        None, "--request", "-r",
        help="Request to run on each change. Defaults to the file's content.",
    ),
    team: str = typer.Option("dev", "--team", "-t", help="Team to run: dev, fullstack, research, content."),
    model_name: str = typer.Option("claude-haiku-4-5-20251001", "--model", "-m", help="Model ID."),
    output: Path = typer.Option(
        Path("antcrew-watch-latest.json"), "--output", "-o",
        help="Where to write each run's state (overwritten on each change).",
    ),
    diff: bool = typer.Option(True, "--diff/--no-diff", help="Show artifact diff after each re-run."),
    debounce: float = typer.Option(2.0, "--debounce", help="Seconds to wait after a change before re-running."),
) -> None:
    """Re-run the pipeline whenever a watched file changes.

    \b
    Watch a spec file and re-run DevTeam on every save:
        antcrew watch spec.md --request "implement this spec"

    \b
    Watch a directory (any file change triggers a re-run):
        antcrew watch src/ --team fullstack

    \b
    Requires:  pip install antcrew[watch]
    """
    watch_path = watch_path.resolve()
    if not watch_path.exists():
        console.print(f"[red]Path not found:[/] {watch_path}")
        raise typer.Exit(1)

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print(
            "[red]watchdog is not installed.[/] "
            "Run: [cyan]pip install antcrew\\[watch][/]"
        )
        raise typer.Exit(1)

    import threading
    import time as _time
    import difflib
    from antcrew.utils.persistence import save_state as _save, load_state as _load
    from antcrew.config import build_llm

    llm = build_llm(model_name)

    def _build_team():
        t = team.lower()
        if t == "dev":
            from antcrew.teams.dev_team import DevTeam
            return DevTeam(model=llm)
        if t in ("fullstack", "full"):
            from antcrew.teams.fullstack_team import FullStackTeam
            return FullStackTeam(model=llm)
        if t == "research":
            from antcrew.teams.research_team import ResearchTeam
            return ResearchTeam(model=llm)
        if t == "content":
            from antcrew.teams.content_team import ContentTeam
            return ContentTeam(model=llm)
        console.print(f"[red]Unknown team:[/] {team}")
        raise typer.Exit(1)

    def _read_request() -> str:
        if request:
            return request
        if watch_path.is_file():
            try:
                return watch_path.read_text(encoding="utf-8", errors="replace")[:4000].strip()
            except Exception:
                pass
        return f"Process changes in {watch_path.name}"

    def _run_once(run_n: int) -> Optional[dict]:
        req = _read_request()
        console.print(
            f"\n[bold green]antcrew watch[/]  run #{run_n}  "
            f"[dim]{watch_path.name}[/dim]\n"
        )
        try:
            team_inst = _build_team()
            with console.status(f"Running {team}…"):
                result = team_inst.run(req, thread_id=f"watch-{run_n}")
            state = dict(result.state)
            _save(state, output)
            console.print(f"[green]Done.[/]  cost={result.cost_usd:.4f}  → [cyan]{output}[/]\n")
            return state
        except Exception as exc:
            console.print(f"[red]Run failed:[/] {exc}\n")
            return None

    def _show_diff(prev: dict, curr: dict) -> None:
        # Code artifact diff
        def _fmap(state):
            arts = state.get("code_artifacts") or []
            return {
                a["file_path"]: a.get("content", "")
                for a in arts if isinstance(a, dict) and a.get("file_path")
            }
        fa, fb = _fmap(prev), _fmap(curr)
        if fa == fb:
            console.print("[dim]Code artifacts unchanged.[/dim]\n")
            return
        all_files = sorted(set(fa) | set(fb))
        for f in all_files:
            if f not in fa:
                console.print(f"  [green]+[/] {f}  [dim]\\[new][/dim]")
            elif f not in fb:
                console.print(f"  [red]−[/] {f}  [dim]\\[removed][/dim]")
            elif fa[f] != fb[f]:
                lines = list(difflib.unified_diff(
                    fa[f].splitlines(keepends=True),
                    fb[f].splitlines(keepends=True),
                    fromfile=f"prev/{f}", tofile=f"curr/{f}", n=2,
                ))
                console.print(f"  [yellow]~[/] {f}")
                for line in lines[2:]:  # skip --- +++
                    line = line.rstrip("\n")
                    if line.startswith("+"):
                        console.print(f"    [green]{line}[/green]")
                    elif line.startswith("-"):
                        console.print(f"    [red]{line}[/red]")
        console.print()

    # ── debounce + run state ─────────────────────────────────────────────────
    _lock = threading.Lock()
    _pending: list[bool] = [False]
    _run_n: list[int] = [0]
    _prev_state: list[Optional[dict]] = [None]

    def _schedule_run():
        with _lock:
            _pending[0] = True

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                _schedule_run()

        def on_created(self, event):
            if not event.is_directory:
                _schedule_run()

    watch_dir = str(watch_path if watch_path.is_dir() else watch_path.parent)
    observer = Observer()
    observer.schedule(_Handler(), path=watch_dir, recursive=watch_path.is_dir())
    observer.start()

    console.print(
        f"[bold green]antcrew watch[/]  watching [cyan]{watch_path}[/]  "
        f"team=[cyan]{team}[/]  debounce={debounce}s\n"
        "Press [bold]Ctrl+C[/bold] to stop.\n"
    )

    # Immediate first run
    _run_n[0] += 1
    _prev_state[0] = _run_once(_run_n[0])

    try:
        while True:
            _time.sleep(0.5)
            with _lock:
                if not _pending[0]:
                    continue
                _pending[0] = False

            _time.sleep(debounce)
            _run_n[0] += 1
            curr = _run_once(_run_n[0])
            if curr is not None and diff and _prev_state[0] is not None:
                _show_diff(_prev_state[0], curr)
            _prev_state[0] = curr
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]\n")
    finally:
        observer.stop()
        observer.join()


@app.command(name="export")
def export_cmd(
    path: Path = typer.Argument(..., help="JSON state file to export."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Destination zip path (default: <stem>.zip next to the source file).",
    ),
    include_tests: bool = typer.Option(True, "--tests/--no-tests", help="Include test artifacts."),
    include_devops: bool = typer.Option(True, "--devops/--no-devops", help="Include devops artifacts."),
    include_docs: bool = typer.Option(True, "--docs/--no-docs", help="Include doc artifacts."),
    include_state: bool = typer.Option(True, "--state/--no-state", help="Include the raw state JSON."),
) -> None:
    """Export a saved run to a zip archive containing all generated files.

    \b
    antcrew export run_output.json
    antcrew export run_output.json --output my_project.zip --no-tests
    """
    import zipfile
    from antcrew.utils.persistence import load_state as _load_state

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    raw = _load_state(path)
    # Support project files that nest state under "state"
    state = raw.get("state", raw) if isinstance(raw.get("state"), dict) else raw

    zip_path = output or path.with_suffix(".zip")

    def _artifacts(key: str) -> list[dict]:
        items = state.get(key) or []
        return [a for a in items if isinstance(a, dict) and a.get("file_path")]

    entries: list[tuple[str, str]] = []  # (arcname, content)

    for art in _artifacts("code_artifacts"):
        entries.append((f"src/{art['file_path']}", art.get("content") or ""))

    if include_tests:
        for art in _artifacts("test_artifacts"):
            entries.append((f"tests/{art['file_path']}", art.get("content") or ""))

    if include_devops:
        for art in _artifacts("devops_artifacts"):
            entries.append((f"devops/{art['file_path']}", art.get("content") or ""))

    if include_docs:
        for art in _artifacts("doc_artifacts"):
            entries.append((f"docs/{art['file_path']}", art.get("content") or ""))

    # Research document → docs/research.md
    if include_docs and state.get("research_document"):
        rd = state["research_document"]
        if isinstance(rd, dict):
            body = rd.get("body") or rd.get("summary") or ""
            title = rd.get("title") or "research"
            entries.append((f"docs/{title}.md", body))

    if include_state:
        entries.append(("state.json", json.dumps(raw, indent=2, default=str)))

    if not entries:
        console.print("[yellow]Nothing to export[/] — no artifacts found in this run.")
        raise typer.Exit(0)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in entries:
            zf.writestr(arcname, content)

    size_kb = zip_path.stat().st_size / 1024
    console.print(
        f"[green]Exported[/] {len(entries)} files → [cyan]{zip_path}[/]  "
        f"[dim]({size_kb:.1f} KB)[/dim]"
    )
    for arcname, _ in entries:
        console.print(f"  [dim]{arcname}[/dim]")


@app.command(name="diff")
def diff_cmd(
    run_a: Path = typer.Argument(..., help="First state JSON file (baseline)."),
    run_b: Path = typer.Argument(..., help="Second state JSON file (comparison)."),
    files: bool = typer.Option(True, "--files/--no-files", help="Show per-file content diffs."),
    context: int = typer.Option(3, "--context", "-c", help="Unified diff context lines."),
) -> None:
    """Compare two saved pipeline runs side-by-side.

    \b
    antcrew diff run_v1.json run_v2.json
    antcrew diff run_v1.json run_v2.json --no-files   # metadata only
    """
    import difflib
    from antcrew.utils.persistence import load_state as _load_state
    from rich.rule import Rule
    from rich.table import Table

    for p in (run_a, run_b):
        if not p.exists():
            console.print(f"[red]File not found:[/] {p}")
            raise typer.Exit(1)

    a = _load_state(run_a)
    b = _load_state(run_b)

    def _val(state: dict, key: str):
        v = state.get(key)
        if isinstance(v, dict) and v:
            return v
        return v

    console.print(f"\n[bold green]antcrew diff[/]  [cyan]{run_a.name}[/]  →  [cyan]{run_b.name}[/]\n")

    # ── request ──────────────────────────────────────────────────────────────
    req_a = a.get("request") or ""
    req_b = b.get("request") or ""
    if req_a != req_b:
        console.print(Rule("[bold]request[/]"))
        console.print(f"  [red]A:[/] {req_a}")
        console.print(f"  [green]B:[/] {req_b}")
        console.print()

    # ── PRD ──────────────────────────────────────────────────────────────────
    prd_a = a.get("prd") or {}
    prd_b = b.get("prd") or {}
    if isinstance(prd_a, dict) and isinstance(prd_b, dict) and (prd_a or prd_b):
        prd_changed = any(prd_a.get(k) != prd_b.get(k) for k in ("title", "summary"))
        if prd_changed:
            console.print(Rule("[bold]PRD[/]"))
            for field in ("title", "summary"):
                va, vb = prd_a.get(field, ""), prd_b.get(field, "")
                if va != vb:
                    console.print(f"  [dim]{field}:[/]")
                    console.print(f"    [red]A:[/] {va}")
                    console.print(f"    [green]B:[/] {vb}")
            console.print()

    # ── Tickets ───────────────────────────────────────────────────────────────
    tix_a: list[dict] = a.get("tickets") or []
    tix_b: list[dict] = b.get("tickets") or []
    if isinstance(tix_a, list) and isinstance(tix_b, list):
        ids_a = {t.get("id") if isinstance(t, dict) else None for t in tix_a}
        ids_b = {t.get("id") if isinstance(t, dict) else None for t in tix_b}
        added   = ids_b - ids_a
        removed = ids_a - ids_b
        if added or removed or len(tix_a) != len(tix_b):
            delta_str = ""
            if added:
                delta_str += f"  [green]+{len(added)} added[/]"
            if removed:
                delta_str += f"  [red]-{len(removed)} removed[/]"
            console.print(Rule(f"[bold]Tickets[/]  A:{len(tix_a)}  B:{len(tix_b)}{delta_str}"))
            for t in tix_b:
                if isinstance(t, dict) and t.get("id") in added:
                    console.print(f"  [green]+[/] {t.get('id','?')}  {t.get('title','')}")
            for t in tix_a:
                if isinstance(t, dict) and t.get("id") in removed:
                    console.print(f"  [red]−[/] {t.get('id','?')}  {t.get('title','')}")
            console.print()

    # ── Code artifacts ────────────────────────────────────────────────────────
    def _artifact_map(lst) -> dict[str, str]:
        if not lst:
            return {}
        out = {}
        for a in lst:
            fp = a.get("file_path") if isinstance(a, dict) else None
            ct = a.get("content") if isinstance(a, dict) else None
            if fp:
                out[fp] = ct or ""
        return out

    ca_a = _artifact_map(a.get("code_artifacts"))
    ca_b = _artifact_map(b.get("code_artifacts"))
    if ca_a or ca_b:
        all_files = sorted(set(ca_a) | set(ca_b))
        added_f   = [f for f in all_files if f not in ca_a]
        removed_f = [f for f in all_files if f not in ca_b]
        changed_f = [f for f in all_files if f in ca_a and f in ca_b and ca_a[f] != ca_b[f]]
        same_f    = [f for f in all_files if f in ca_a and f in ca_b and ca_a[f] == ca_b[f]]

        parts = []
        if added_f:
            parts.append(f"[green]+{len(added_f)} added[/]")
        if removed_f:
            parts.append(f"[red]-{len(removed_f)} removed[/]")
        if changed_f:
            parts.append(f"[yellow]~{len(changed_f)} changed[/]")

        summary = "  " + "  ".join(parts) if parts else ""
        console.print(Rule(f"[bold]Code files[/]  A:{len(ca_a)}  B:{len(ca_b)}{summary}"))

        for f in sorted(added_f):
            console.print(f"  [green]+[/] {f}  [dim]\\[new][/dim]")
        for f in sorted(removed_f):
            console.print(f"  [red]−[/] {f}  [dim]\\[removed][/dim]")
        for f in sorted(changed_f):
            console.print(f"  [yellow]~[/] {f}  [dim]\\[modified][/dim]")
        for f in same_f:
            console.print(f"  [dim]=[/dim] [dim]{f}  \\[unchanged][/dim]")

        if files and changed_f:
            console.print()
            for f in sorted(changed_f):
                lines_a = (ca_a[f] or "").splitlines(keepends=True)
                lines_b = (ca_b[f] or "").splitlines(keepends=True)
                diff_lines = list(difflib.unified_diff(
                    lines_a, lines_b,
                    fromfile=f"A/{f}", tofile=f"B/{f}",
                    n=context,
                ))
                if diff_lines:
                    console.print(f"\n  [bold]{f}[/]")
                    for line in diff_lines:
                        line = line.rstrip("\n")
                        if line.startswith("+++") or line.startswith("---"):
                            console.print(f"  [dim]{line}[/dim]")
                        elif line.startswith("+"):
                            console.print(f"  [green]{line}[/green]")
                        elif line.startswith("-"):
                            console.print(f"  [red]{line}[/red]")
                        elif line.startswith("@@"):
                            console.print(f"  [cyan]{line}[/cyan]")
                        else:
                            console.print(f"  {line}")

        console.print()

    # ── Summary ───────────────────────────────────────────────────────────────
    any_diff = (
        req_a != req_b
        or prd_a != prd_b
        or tix_a != tix_b
        or ca_a != ca_b
    )
    if not any_diff:
        console.print("[dim]No differences found.[/dim]\n")
    else:
        console.print("[dim]─── end of diff ───[/dim]\n")


# ---------------------------------------------------------------------------
# antcrew test — run test artifacts from a saved pipeline state
# ---------------------------------------------------------------------------

@app.command(name="test")
def test_cmd(
    state_file: Path = typer.Argument(
        ...,
        help="JSON state file produced by 'antcrew run --save' or 'antcrew interactive'.",
    ),
    runner: str = typer.Option(
        "local", "--runner", "-r",
        help="Execution backend: local (same Python, temp dir) | docker (isolated container).",
    ),
    timeout: int = typer.Option(
        60, "--timeout", help="Seconds before killing pytest (default: 60)."
    ),
    keep: Optional[Path] = typer.Option(
        None, "--keep", "-k",
        help="Also write test + code files to this directory and keep them after the run.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Always print full pytest output (default: only on failure).",
    ),
    no_code: bool = typer.Option(
        False, "--no-code",
        help="Skip code artifacts — run test files only (useful when the project already exists).",
    ),
) -> None:
    """Run test artifacts from a saved pipeline state.

    \b
    Basic usage:
        antcrew test antcrew-output.json

    \b
    Isolated Docker run with longer timeout:
        antcrew test antcrew-output.json --runner docker --timeout 120

    \b
    Keep generated files on disk for inspection:
        antcrew test antcrew-output.json --keep ./test-output

    \b
    Run only the test files (project already checked out separately):
        antcrew test antcrew-output.json --no-code

    Exit code is 0 when all tests pass, 1 otherwise.
    """
    from antcrew.core.artifacts import CodeArtifact, TestArtifact
    from antcrew.sandbox import LocalRunner, DockerRunner
    from antcrew.sandbox.runner import _write_artifacts
    from antcrew.utils.persistence import load_state

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    try:
        raw = load_state(state_file)
    except Exception as exc:
        console.print(f"[red]Failed to load state file:[/] {exc}")
        raise typer.Exit(1)

    # Project files nest state under "state"
    if "state" in raw and isinstance(raw.get("state"), dict):
        raw = raw["state"]

    test_arts_raw = raw.get("test_artifacts") or []
    code_arts_raw = [] if no_code else (raw.get("code_artifacts") or [])

    if not test_arts_raw:
        console.print(
            f"[yellow]No test artifacts found in[/] [cyan]{state_file}[/]\n"
            "[dim]Run a pipeline first (DevTeam or FullStackTeam with QA enabled).[/dim]"
        )
        raise typer.Exit(0)

    try:
        test_artifacts = [
            TestArtifact.model_validate(a) if isinstance(a, dict) else a
            for a in test_arts_raw
        ]
        code_artifacts = [
            CodeArtifact.model_validate(a) if isinstance(a, dict) else a
            for a in code_arts_raw
        ]
    except Exception as exc:
        console.print(f"[red]Failed to parse artifacts:[/] {exc}")
        raise typer.Exit(1)

    runner_label = runner.strip().lower()
    console.print(
        f"\n[bold green]antcrew test[/]  [cyan]{state_file}[/]  "
        f"[dim]{len(test_artifacts)} test file(s)"
        + (f"  +{len(code_artifacts)} code file(s)" if code_artifacts else "")
        + f"  runner={runner_label}[/dim]\n"
    )

    # Optionally persist files so the user can inspect them.
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        _write_artifacts(keep, test_artifacts, code_artifacts)
        console.print(f"[dim]Files written → [cyan]{keep}/[/][/dim]\n")

    # Build runner
    if runner_label == "docker":
        active_runner = DockerRunner(timeout=timeout)
    elif runner_label == "local":
        active_runner = LocalRunner(timeout=timeout)
    else:
        console.print(
            f"[red]Unknown runner:[/] {runner_label!r}  (choose 'local' or 'docker')"
        )
        raise typer.Exit(1)

    with console.status("[bold]Running tests…[/]"):
        result = active_runner.run(test_artifacts, code_artifacts=code_artifacts)

    _print_test_results(result)

    if verbose or not result.success:
        output = result.output or ""
        tail = output[-3000:] if len(output) > 3000 else output
        if tail.strip():
            console.print(Panel(
                tail,
                title="pytest output",
                border_style="dim",
            ))

    if not result.success:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# antcrew graph — visualise Supervisor flow
# ---------------------------------------------------------------------------

@app.command(name="graph")
def graph_cmd(
    config: Path = typer.Option(
        None, "--config", "-c",
        help="YAML or JSON config/flow file (agentteam.yaml, flow.json, …)",
    ),
    team: str = typer.Option(
        None, "--team", "-t",
        help="Built-in team name: dev | fullstack | research | content",
    ),
    fmt: str = typer.Option(
        "ascii", "--format", "-f",
        help="Output format: ascii | mermaid",
    ),
) -> None:
    """Visualise the Supervisor agent flow as ASCII art or a Mermaid diagram.

    \b
    # Built-in team
    antcrew graph --team dev
    antcrew graph --team fullstack --format mermaid

    \b
    # Custom flow from config file
    antcrew graph --config agentteam.yaml
    antcrew graph --config flow.json --format mermaid
    """
    from antcrew.graph import render_ascii, render_mermaid, _get_builtin_flow

    fmt = fmt.lower().strip()
    if fmt not in ("ascii", "mermaid"):
        console.print(f"[red]Unknown format:[/] {fmt!r}  (choose ascii or mermaid)")
        raise typer.Exit(1)

    # ── Resolve flow ─────────────────────────────────────────────────────────
    flow: list[tuple] | None = None

    if config is not None:
        if not config.exists():
            console.print(f"[red]Config file not found:[/] {config}")
            raise typer.Exit(1)
        try:
            from antcrew.flow import load_flow as _load_flow
            flow = _load_flow(config)
        except Exception as exc:
            console.print(f"[red]Could not load flow from {config}:[/] {exc}")
            raise typer.Exit(1)

    elif team is not None:
        flow = _get_builtin_flow(team)
        if flow is None:
            console.print(
                f"[red]Unknown team:[/] {team!r}\n"
                "  Available: dev, fullstack, research, content"
            )
            raise typer.Exit(1)

    else:
        # Try agentteam.yaml in cwd as default
        default = Path("agentteam.yaml")
        if default.exists():
            try:
                from antcrew.flow import load_flow as _load_flow
                flow = _load_flow(default)
            except Exception as exc:
                console.print(f"[red]Could not load {default}:[/] {exc}")
                raise typer.Exit(1)
        else:
            console.print(
                "[yellow]No flow source specified.[/]\n"
                "  Use --team <name> or --config <file>.\n"
                "  Run [cyan]antcrew graph --help[/] for details."
            )
            raise typer.Exit(1)

    if not flow:
        console.print("[yellow]Flow is empty — nothing to render.[/]")
        raise typer.Exit(0)

    # ── Render ───────────────────────────────────────────────────────────────
    agents = {str(n) for step in flow for n in list(step)[:2]}
    edges = len(flow)
    source_label = (
        str(config) if config else
        f"{team} (built-in)" if team else
        "agentteam.yaml"
    )

    console.print(
        f"\n[bold green]antcrew graph[/]  [cyan]{source_label}[/]  "
        f"[dim]({len(agents)} agents, {edges} edges)[/dim]\n"
    )

    if fmt == "mermaid":
        console.print(render_mermaid(flow), markup=False, highlight=False)
        console.print(
            "\n[dim]Paste the block above into https://mermaid.live or a "
            "```mermaid``` fenced block in GitHub Markdown.[/dim]"
        )
    else:
        console.print(render_ascii(flow), markup=False, highlight=False)

    console.print()


# ---------------------------------------------------------------------------
# antcrew lint — static config / flow validation
# ---------------------------------------------------------------------------

@app.command(name="lint")
def lint_cmd(
    config: Path = typer.Argument(
        Path("agentteam.yaml"),
        help="YAML or JSON config file to lint (default: agentteam.yaml)",
    ),
    strict: bool = typer.Option(
        False, "--strict", "-s",
        help="Exit 1 on warnings as well as errors (default: only errors cause exit 1).",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Suppress info-level messages; only show warnings and errors.",
    ),
) -> None:
    """Statically validate an agentteam.yaml or flow.json config.

    \b
    Checks (no LLM calls, no API keys required):
      errors   — unknown team/model/channel/runner, flow cycles, bad max_cost_usd
      warnings — unknown agent names, unresolved ${VAR} tokens, unknown presets
      info     — missing optional keys that have defaults

    \b
    antcrew lint
    antcrew lint agentteam.yaml
    antcrew lint agentteam.yaml --strict   # warnings also cause exit 1
    antcrew lint flow.json --quiet         # hide info messages
    """
    from antcrew.linter import lint_config

    if not config.exists():
        console.print(f"[red]File not found:[/] {config}")
        raise typer.Exit(1)

    issues = lint_config(config)

    errors   = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos    = [i for i in issues if i.severity == "info"]

    if not issues:
        console.print(f"[bold green]✓ {config}[/]  [dim]no issues found[/dim]")
        return

    console.print(f"\n[bold]antcrew lint[/]  [cyan]{config}[/]\n")

    for issue in issues:
        if issue.severity == "error":
            loc = f"  [dim]{issue.path}[/dim]" if issue.path else ""
            console.print(f"  [bold red]✗[/] {issue.message}{loc}")
        elif issue.severity == "warning":
            loc = f"  [dim]{issue.path}[/dim]" if issue.path else ""
            console.print(f"  [yellow]⚠[/] {issue.message}{loc}")
        elif not quiet:
            loc = f"  [dim]{issue.path}[/dim]" if issue.path else ""
            console.print(f"  [dim]ℹ {issue.message}{loc}[/dim]")

    console.print()
    parts = []
    if errors:
        parts.append(f"[red]{len(errors)} error(s)[/]")
    if warnings:
        parts.append(f"[yellow]{len(warnings)} warning(s)[/]")
    if infos and not quiet:
        parts.append(f"[dim]{len(infos)} info[/dim]")
    console.print("  " + ", ".join(parts))
    console.print()

    if errors or (strict and warnings):
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# antcrew history — aggregate TraceLog statistics and run browser
# ---------------------------------------------------------------------------

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
        from datetime import datetime as _dt, timezone as _tz
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


@app.command(name="validate")
def validate_cmd(
    config: Path = typer.Argument(..., help="Path to agentteam.yaml or agentteam.json"),
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit 1 even on warnings (not just errors).",
    ),
) -> None:
    """Validate a team YAML/JSON config without running it.

    \b
    Checks:
      • YAML / JSON syntax
      • Required fields (name, system_prompt) for every agent step
      • Agent instantiation using SimulatedLLM (no API calls)
      • Step references (output_key used in {interpolation} later in the pipeline)

    \b
    Examples:
        antcrew validate team.yaml
        antcrew validate team.yaml --strict
    """
    import yaml as _yaml
    from antcrew.models.simulated import SimulatedLLM as _Sim

    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Parse file ─────────────────────────────────────────────────────────
    if not config.exists():
        console.print(f"[red]✗[/] File not found: [cyan]{config}[/]")
        raise typer.Exit(1)

    try:
        text = config.read_text(encoding="utf-8")
        if config.suffix.lower() == ".json":
            cfg = json.loads(text)
        else:
            cfg = _yaml.safe_load(text)
        if not isinstance(cfg, dict):
            raise ValueError("Top-level value must be a YAML mapping / JSON object.")
    except Exception as exc:
        console.print(f"[red]✗[/] Parse error: {exc}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/] File parsed: [cyan]{config}[/]")

    # ── 2. Detect team type ────────────────────────────────────────────────────
    team_type = cfg.get("team", "custom")
    model_name = cfg.get("model", "(not set)")
    console.print(f"[green]✓[/] Team type: [cyan]{team_type}[/]  model: [cyan]{model_name}[/]")

    if team_type != "custom":
        # For built-in teams, just try load_context with SimulatedLLM substitution.
        # We can't fully validate without the model, but we can check the YAML loads.
        console.print(
            f"[dim]  (deep validation for '{team_type}' teams is not supported yet; "
            "YAML structure looks OK)[/dim]"
        )
        console.print("\n[bold green]✓ Config looks valid[/]\n")
        return

    # ── 3. CustomTeam step-by-step validation ─────────────────────────────────
    raw_steps = cfg.get("steps")
    if not raw_steps:
        console.print("[red]✗[/] 'steps:' key is missing or empty.")
        raise typer.Exit(1)

    from rich.table import Table

    tbl = Table(show_header=True, header_style="bold", box=None, show_edge=False)
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Name", style="cyan")
    tbl.add_column("Type", style="dim")
    tbl.add_column("output_key", style="green")
    tbl.add_column("Flags", style="yellow")

    step_idx = 0
    produced_keys: set[str] = {"request"}  # keys available before any step runs

    def _validate_one(raw: dict, label: str, idx_str: str) -> None:
        name = raw.get("name", "")
        if not name:
            errors.append(f"Step {idx_str}: missing required field 'name'.")
        if not raw.get("system_prompt", "").strip():
            errors.append(f"Step {idx_str} '{name}': missing required field 'system_prompt'.")

        out_key = raw.get("output_key") or (f"{name}_output" if name else "")
        flags = []
        if raw.get("output_json"):
            flags.append("json")
        if raw.get("interpolate") is False:
            flags.append("no-interp")
        if raw.get("max_retries"):
            flags.append(f"retry×{raw['max_retries']}")
        if raw.get("condition"):
            cond = raw["condition"]
            cond_keys = [cond] if isinstance(cond, str) else cond
            for ck in cond_keys:
                if ck not in produced_keys:
                    warnings.append(
                        f"Step {idx_str} '{name}': condition key '{ck}' is not "
                        "produced by any earlier step."
                    )
            flags.append(f"if:{','.join(cond_keys)}")

        # Check {placeholder} references in system_prompt
        import re as _re
        _INTERP_RE = _re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
        prompt = raw.get("system_prompt", "")
        for m in _INTERP_RE.finditer(prompt):
            key = m.group(1)
            if key not in produced_keys:
                warnings.append(
                    f"Step {idx_str} '{name}': interpolation {{'{key}'}}"
                    " references a key not yet produced by earlier steps."
                )

        tbl.add_row(idx_str, name, label, out_key, " ".join(flags))
        if out_key:
            produced_keys.add(out_key)

    for item in raw_steps:
        step_idx += 1
        if isinstance(item, dict) and "parallel" in item:
            parallel_cfgs = item["parallel"]
            if not parallel_cfgs:
                errors.append(f"Step {step_idx}: empty 'parallel:' group.")
                continue
            # Show first row as group header, then members
            for j, pcfg in enumerate(parallel_cfgs):
                sub_label = f"parallel" if j == 0 else ""
                idx_str = f"{step_idx}.{j + 1}"
                if isinstance(pcfg, dict):
                    _validate_one(pcfg, sub_label, idx_str)
        else:
            if isinstance(item, dict):
                _validate_one(item, "seq", str(step_idx))
            else:
                errors.append(
                    f"Step {step_idx}: expected a dict, got {type(item).__name__}."
                )

    console.print()
    console.print(tbl)
    console.print()

    # ── 4. Instantiation check ────────────────────────────────────────────────
    if not errors:
        try:
            from antcrew.teams.custom_team import CustomTeam
            CustomTeam(list(raw_steps), _Sim())
            console.print("[green]✓[/] All agents instantiated successfully (SimulatedLLM)")
        except Exception as exc:
            errors.append(f"Instantiation failed: {exc}")

    # ── 5. Report ─────────────────────────────────────────────────────────────
    for w in warnings:
        console.print(f"[yellow]⚠[/] {w}")
    for e in errors:
        console.print(f"[red]✗[/] {e}")

    if errors or (strict and warnings):
        total = len(errors) + (len(warnings) if strict else 0)
        console.print(
            f"\n[bold red]✗ Validation failed[/] ({total} issue{'s' if total != 1 else ''})\n"
        )
        raise typer.Exit(1)

    if warnings:
        console.print(
            f"\n[bold yellow]⚠ Config valid with {len(warnings)} warning(s)[/]\n"
        )
    else:
        console.print("\n[bold green]✓ Config is valid[/]\n")


if __name__ == "__main__":
    app()
