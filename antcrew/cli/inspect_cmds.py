"""Show, extract, describe, and agents commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console, _TEAM_CHOICES
from antcrew.cli._shared import _print_state_raw

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

    if output_json:
        typer.echo(json.dumps(raw, indent=2))
        return

    console.print(f"\n[bold green]AntCrew show[/] — [cyan]{path}[/]\n")

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
    trace: Optional[Path] = typer.Option(None, "--trace", help="TraceLog DB to show historical average cost"),
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

    # ── Historical cost from TraceLog (optional) ──────────────────────────────
    trace_path = trace or Path.home() / ".antcrew" / "trace.db"
    if trace_path.exists():
        try:
            from antcrew.trace import TraceLog
            tl = TraceLog(str(trace_path))
            stats = tl.stats()
            total = stats.get("total_runs", 0)
            if total:
                avg = stats.get("avg_cost_usd", 0.0)
                total_c = stats.get("total_cost_usd", 0.0)
                console.print(
                    f"[dim]Historical cost ({total} run{'s' if total != 1 else ''}): "
                    f"avg [cyan]${avg:.4f}[/]  total [cyan]${total_c:.4f}[/]  "
                    f"(from {trace_path})[/dim]\n"
                )
        except Exception:
            pass


@app.command(name="agents")
def agents_cmd(
    output_json: bool = typer.Option(False, "--json", help="Output as JSON array."),
) -> None:
    """List all built-in agent types with their role descriptions."""
    from antcrew.agents.registry import AGENT_REGISTRY, get_agent_class

    entries = []
    for name, (_, cls_name) in AGENT_REGISTRY.items():
        role = ""
        try:
            cls = get_agent_class(name)
            role = getattr(cls, "role_description", "") or ""
        except Exception:
            role = ""
        entries.append({"name": name, "class": cls_name, "role": role})

    if output_json:
        typer.echo(json.dumps(entries, indent=2))
        return

    from rich.table import Table
    tbl = Table(show_header=True, header_style="bold", box=None, show_edge=False)
    tbl.add_column("Name", style="cyan", min_width=20)
    tbl.add_column("Class", style="dim", min_width=22)
    tbl.add_column("Role description", style="white")
    for e in entries:
        tbl.add_row(e["name"], e["class"], e["role"])

    console.print("\n[bold]Built-in agent types[/bold]\n")
    console.print(tbl)
    console.print(
        "\n[dim]Custom agents: set [cyan]team: custom[/] with a [cyan]steps:[/] list "
        "and declare [cyan]system_prompt:[/] inline or via [cyan]system_prompt_file:[/].[/dim]\n"
    )

    from antcrew.agents.template_agent import POST_PROCESS_TRANSFORMS
    transforms = sorted(POST_PROCESS_TRANSFORMS)
    console.print(
        f"[dim]post_process transforms available: {', '.join(transforms)}[/dim]\n"
    )


