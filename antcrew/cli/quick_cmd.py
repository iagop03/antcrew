"""antcrew quick — run inline agents without writing any Python."""
from __future__ import annotations

from typing import Optional

import typer
from rich.panel import Panel
from rich.markdown import Markdown

from antcrew.cli._app import _MODEL_HELP, app, console


@app.command("quick")
def quick(
    request: str = typer.Argument(..., help="Goal or question for the agent team"),
    agents: list[str] = typer.Argument(
        ...,
        help="One or more agent specs: 'Role: goal description'",
    ),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    output_json: bool = typer.Option(False, "--json", help="Print final state as JSON"),
    push: Optional[str] = typer.Option(
        None,
        "--push",
        help="Platform URL (https://your-platform) to dispatch and track the run remotely",
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", envvar="ANTCREW_API_KEY",
        help="Platform API key (required with --push)",
    ),
) -> None:
    """Run a quick inline agent team — no Python class needed.

    Each AGENT argument is a spec string: 'Role: description of what this agent does'.
    Agents are chained sequentially; each sees the previous agent's output.

    Examples:

    \\b
    # Single agent
    antcrew quick "What are the main AI agent frameworks?" \\
        "Researcher: Find and compare the top AI agent frameworks"

    \\b
    # Multi-agent pipeline
    antcrew quick "Summarize advances in RAG" \\
        "Researcher: Find recent papers on RAG and extract key findings" \\
        "Analyst: Identify the 3 most impactful advances" \\
        "Writer: Write a clear 300-word summary for a technical audience"
    """
    if not agents:
        console.print("[red]Error:[/red] at least one agent spec is required.")
        raise typer.Exit(1)

    if push:
        _push_quick_run(push, api_key, request, agents, model)
        return

    from antcrew.agents.quick_agent import QuickTeam
    from antcrew import build_llm

    llm = build_llm(model)
    team = QuickTeam(specs=agents, llm=llm)

    console.rule(f"[bold cyan]Quick Team[/bold cyan] — {len(agents)} agent(s)")
    for i, spec in enumerate(agents, 1):
        role = spec.split(":", 1)[0].strip()
        console.print(f"  [dim]{i}.[/dim] [cyan]{role}[/cyan]")
    console.print()

    with console.status("[bold green]Running…[/bold green]"):
        state = team.run(request)

    result = state.get("result", "")

    if output_json:
        import json
        console.print_json(json.dumps(state, indent=2, default=str))
        return

    console.print()
    console.print(Panel(Markdown(result), title="[bold green]Result[/bold green]", expand=False))

    cost = getattr(llm, "_total_cost", None)
    if cost is not None:
        console.print(f"\n[dim]Cost: ${cost:.4f}[/dim]")


def _push_quick_run(
    platform_url: str,
    api_key: Optional[str],
    request: str,
    agents: list[str],
    model: str,
) -> None:
    """Dispatch inline agents to the platform as a QuickTeam."""
    if not api_key:
        console.print("[red]Error:[/red] --api-key is required with --push.")
        raise typer.Exit(1)

    import httpx

    platform_url = platform_url.rstrip("/")
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # Build a CustomTeam-style steps payload from agent specs
    steps = []
    for i, spec in enumerate(agents):
        role, _, goal = spec.partition(":")
        role = role.strip()
        goal = (goal or role).strip()
        steps.append({
            "name": role.lower().replace(" ", "_"),
            "system_prompt": f"You are a {role}. {goal}",
            "input_key": "_quick_context" if i > 0 else "request",
            "output_key": "result" if i == len(agents) - 1 else f"_quick_{i}",
        })

    payload = {
        "team": "custom",
        "request": request,
        "model": model,
        "steps": steps,
    }

    try:
        resp = httpx.post(f"{platform_url}/run/pipeline", json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        run_id = data.get("run_id", "?")
        console.print(f"[green]Dispatched[/green] run [bold]{run_id}[/bold] to {platform_url}")
        console.print(f"  Track: {platform_url}/runs/{run_id}")
    except Exception as exc:
        console.print(f"[red]Error pushing to platform:[/red] {exc}")
        raise typer.Exit(1)
