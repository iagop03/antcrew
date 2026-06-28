"""Graph and lint commands."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console
from antcrew.cli._shared import _print_state

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

