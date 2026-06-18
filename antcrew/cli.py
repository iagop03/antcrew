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

def _run_with_stream(team, request: str, thread: str, stream: bool, llm=None) -> dict:
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

        console.print_json(json.dumps(state, default=_ser))
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
) -> None:
    """Delete all entries from a persistent cache file."""
    from antcrew.models.cache import FileLLMCache
    db = Path(db).expanduser()
    if not db.exists():
        console.print(f"[yellow]Cache file not found:[/] {db}")
        raise typer.Exit(1)
    c = FileLLMCache(db)
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

    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    console.print("\n[bold green]AntCrew[/] v0.4")
    console.print(f"  API    → [cyan]http://{display_host}:{port}[/]")
    console.print(f"  Docs   → [dim]http://{display_host}:{port}/docs[/dim]")
    if _has_dashboard:
        console.print(f"  Dashboard → [bold cyan]http://{display_host}:{port}/ui/[/bold cyan]")
    else:
        console.print(
            "  Dashboard → [yellow]not built[/yellow]  "
            "[dim](cd dashboard && npm install && npm run build)[/dim]"
        )
    console.print()
    uvicorn.run("antcrew.server:app", host=host, port=port, reload=reload)


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
        None, "--save", "-s", help="Save final state to a JSON file"
    ),
) -> None:
    """Run a pipeline with human-in-the-loop review after each agent.

    After every agent you can: approve (continue), reject (stop),
    edit (open JSON editor), or type free-text feedback to trigger
    conversational refinement (agents that support it will revise
    their output in-place before the pipeline moves on).
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

        state = active_team.run_interactive(request, thread_id=thread)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"\n[red bold]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print()
    _print_state(state, team)

    if save:
        from antcrew.utils.persistence import save_state
        save_state(state, save)
        console.print(f"\n[dim]State saved → [cyan]{save}[/][/dim]")

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


if __name__ == "__main__":
    app()
