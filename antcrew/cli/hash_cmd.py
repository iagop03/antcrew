"""Verify governance hashes for agents defined in a YAML/JSON config."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console


@app.command(name="verify-hash")
def verify_hash_cmd(
    config: Path = typer.Argument(..., help="Path to agentteam.yaml or agentteam.json"),
    expected: Optional[str] = typer.Option(
        None, "--expected", "-e",
        help="Team hash to compare against. Exits 0 on match, 1 on mismatch.",
    ),
) -> None:
    """Compute governance hashes for agents in a YAML config.

    \b
    Instantiates each agent with SimulatedLLM (no API calls required) and
    prints the per-agent governance_hash plus the aggregated team_hash.

    \b
    Use --expected to gate a deploy on hash equality:

    \b
        antcrew verify-hash team.yaml --expected sha256:a1b2c3d4e5f6789a

    \b
    Examples:
        antcrew verify-hash team.yaml
        antcrew verify-hash team.yaml --expected sha256:a1b2c3d4e5f6789a
    """
    import json

    import yaml as _yaml

    from antcrew.core.agent import compute_team_hash as _compute_team_hash
    from antcrew.models.simulated import SimulatedLLM as _Sim

    # ── 1. Parse ───────────────────────────────────────────────────────────────
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

    # ── 2. Only custom teams are supported ────────────────────────────────────
    team_type = cfg.get("team", "custom")
    if team_type != "custom":
        console.print(
            f"[yellow]⚠[/] Team type '{team_type}' is not a CustomTeam — "
            "governance hashes are computed per-agent step and require a 'custom' team."
        )
        raise typer.Exit(1)

    raw_steps = cfg.get("steps")
    if not raw_steps:
        console.print("[red]✗[/] 'steps:' key is missing or empty.")
        raise typer.Exit(1)

    # ── 3. Instantiate agents via SimulatedLLM ────────────────────────────────
    try:
        from antcrew.teams.custom_team import CustomTeam
        team = CustomTeam(list(raw_steps), _Sim(), base_dir=config.parent)
        agents = team._agents
    except Exception as exc:
        console.print(f"[red]✗[/] Instantiation failed: {exc}")
        raise typer.Exit(1)

    # ── 4. Print table ────────────────────────────────────────────────────────
    from rich.table import Table

    tbl = Table(show_header=True, header_style="bold", box=None, show_edge=False)
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Agent", style="cyan")
    tbl.add_column("Stage", style="dim")
    tbl.add_column("Governance Hash", style="green", no_wrap=True)

    for i, agent in enumerate(agents, 1):
        tbl.add_row(str(i), agent.name, agent.stage or "—", agent.governance_hash)

    console.print()
    console.print(tbl)
    console.print()

    # ── 5. Team hash ──────────────────────────────────────────────────────────
    team_hash = "sha256:" + _compute_team_hash(agents)
    console.print(f"[bold]Team hash:[/bold] [green]{team_hash}[/green]")
    console.print(f"[dim]{len(agents)} agent{'s' if len(agents) != 1 else ''} · {config}[/dim]")
    console.print()

    # ── 6. Optional comparison ────────────────────────────────────────────────
    if expected is not None:
        norm_expected = expected.removeprefix("sha256:")
        norm_current = team_hash.removeprefix("sha256:")
        if norm_current == norm_expected:
            console.print("[bold green]✓ Hash matches expected value.[/bold green]")
        else:
            console.print(
                f"[bold red]✗ Hash mismatch![/bold red]\n"
                f"  Expected: [yellow]{expected}[/yellow]\n"
                f"  Current:  [red]{team_hash}[/red]\n"
                "\nConfiguration has changed since the expected hash was recorded."
            )
            raise typer.Exit(1)
    else:
        console.print(
            "[dim]Tip: pass [/dim][cyan]--expected <hash>[/cyan][dim] "
            "to gate a deploy on hash equality.[/dim]"
        )
