"""antcrew dag — validate and display the agent DAG for a team config."""
from __future__ import annotations

from pathlib import Path

import typer

from antcrew.cli._app import app, console


@app.command("dag")
def dag_cmd(
    config: Path = typer.Argument(
        ...,
        help="Path to agentteam.yaml (or .json) config file.",
    ),
    strict: bool = typer.Option(
        True, "--strict/--no-strict",
        help="Exit with error code on validation failure (default: yes).",
    ),
) -> None:
    """Validate and display the agent dependency graph for a team config.

    Reads the config, inspects each agent's ``consumes``/``produces`` lists,
    and prints a table showing the data flow.  Highlights missing dependencies
    in red.

    Example::

        antcrew dag agentteam.yaml
        antcrew dag agentteam.yaml --no-strict   # print violations but exit 0
    """
    from rich.table import Table
    from rich.text import Text

    if not config.exists():
        console.print(f"[red]Config file not found:[/] {config}")
        raise typer.Exit(1)

    try:
        import json as _json

        import yaml as _yaml
        raw = config.read_text(encoding="utf-8")
        cfg: dict = _yaml.safe_load(raw) if config.suffix.lower() in (".yaml", ".yml") else _json.loads(raw)
    except Exception as exc:
        console.print(f"[red]Failed to parse config:[/] {exc}")
        raise typer.Exit(1)

    # Extract agents from config — CustomTeam and role-based teams differ.
    team_type = cfg.get("team", "dev").lower()

    agents: list = []
    if team_type == "custom":
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.custom_team import _parse_steps
        steps_raw = cfg.get("steps") or []
        try:
            groups = _parse_steps(steps_raw, SimulatedLLM())
            agents = [s.agent for group in groups for s in group]
        except Exception as exc:
            console.print(f"[red]Failed to parse steps:[/] {exc}")
            raise typer.Exit(1)

    elif team_type in ("dev", "fullstack"):
        from antcrew.agents.backend_dev import BackendDevAgent
        from antcrew.agents.business import BusinessAnalystAgent
        from antcrew.agents.pm import PMAgent
        from antcrew.models.simulated import SimulatedLLM
        llm = SimulatedLLM()
        pipeline = [BusinessAnalystAgent(llm), PMAgent(llm), BackendDevAgent(llm)]
        if team_type == "fullstack":
            from antcrew.agents.devops import DevOpsAgent
            from antcrew.agents.doc_writer import DocWriterAgent
            from antcrew.agents.frontend_dev import FrontendDevAgent
            from antcrew.agents.qa import QAAgent
            from antcrew.agents.reviewer import ReviewerAgent
            pipeline += [FrontendDevAgent(llm), QAAgent(llm), ReviewerAgent(llm), DevOpsAgent(llm), DocWriterAgent(llm)]
        agents = pipeline

    else:
        console.print(
            f"[yellow]DAG analysis for team type '{team_type}' is not supported.[/]\n"
            "Supported: custom, dev, fullstack"
        )
        raise typer.Exit(0)

    if not agents:
        console.print("[yellow]No agents found to analyse.[/]")
        raise typer.Exit(0)

    # Run validation
    from antcrew.core.validation import validate_agent_dag
    violations = validate_agent_dag(agents, initial_keys={"request"}, strict=False)

    # Build display table
    available: set[str] = {"request"}
    table = Table(title=f"Agent DAG — {config.name}", show_lines=True)
    table.add_column("Agent", style="bold cyan", no_wrap=True)
    table.add_column("Consumes", style="dim")
    table.add_column("Produces", style="green")
    table.add_column("Status")

    for agent in agents:
        name = getattr(agent, "name", type(agent).__name__)
        consumes = list(getattr(agent, "consumes", []) or [])
        produces = list(getattr(agent, "produces", []) or [])
        missing = [k for k in consumes if k not in available]

        if missing:
            status = Text(f"✗ missing: {', '.join(missing)}", style="red bold")
        else:
            status = Text("✓ ok", style="green")

        consumes_text = Text()
        for k in consumes:
            if k not in available:
                consumes_text.append(k, style="red bold")
            else:
                consumes_text.append(k, style="dim")
            consumes_text.append(" ")

        table.add_row(name, consumes_text, ", ".join(produces) or "—", status)
        available.update(produces)

    console.print()
    console.print(table)

    if violations:
        console.print(f"\n[red]✗ {len(violations)} violation(s) found:[/]")
        for v in violations:
            console.print(f"  [red]•[/] {v}")
        if strict:
            raise typer.Exit(1)
    else:
        console.print(f"\n[green]✓ DAG is valid — {len(agents)} agent(s), all dependencies satisfied.[/]")
