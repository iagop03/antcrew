"""antcrew discover — conversational requirements gathering before running a pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import _MODEL_HELP, _TEAM_CHOICES, app, console


@app.command()
def discover(
    project: str = typer.Option("", "--project", "-p", help="Project name to start with"),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    save: Optional[Path] = typer.Option(
        None, "--save", "-s",
        help="Save the DiscoveryContext to a JSON file after the session.",
    ),
    run_after: bool = typer.Option(
        False, "--run",
        help="After discovery is complete, immediately start a pipeline run.",
    ),
    team: str = typer.Option(
        "dev", "--team", "-t",
        help=f"Team to use when --run is set: {_TEAM_CHOICES}",
    ),
    max_rounds: int = typer.Option(
        7, "--max-rounds",
        help="Maximum number of Q&A rounds before discovery auto-completes.",
    ),
) -> None:
    """Conversational requirements gathering — build a PRD through dialogue.

    The agent asks one question at a time until it has enough information to
    write a full PRD. Type 'done' or 'q' at any point to stop early.

    Example:
    \b
        antcrew discover --project "TaskFlow" --save context.json
        antcrew discover --run --team fullstack
    """
    from antcrew import build_llm
    from antcrew.agents.discovery import DiscoveryAgent
    from antcrew.core.artifacts import DiscoveryContext

    try:
        llm = build_llm(model)
    except Exception as exc:
        console.print(f"[red bold]Model error:[/] {exc}")
        raise typer.Exit(1)

    agent = DiscoveryAgent(llm)
    context = DiscoveryContext(
        project_name=project,
        is_complete=False,
    )

    console.print(
        f"\n[bold green]AntCrew Discovery[/]  —  "
        f"model=[cyan]{model}[/]  max_rounds=[dim]{max_rounds}[/]\n"
    )
    console.print(
        "[dim]I'll ask a few questions to understand what you want to build.\n"
        "Type [bold]done[/bold] or [bold]q[/bold] at any point to finish early.[/dim]\n"
    )

    round_num = 0

    while not context.is_complete and round_num < max_rounds:
        try:
            result = agent.ask_next(context)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/]")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Error generating question:[/] {exc}")
            break

        if result.get("is_complete"):
            break

        question = result.get("next_question", "").strip()
        if not question:
            break

        round_num += 1
        console.print(f"[bold cyan][{round_num}/{max_rounds}][/bold cyan] {question}")

        try:
            answer = typer.prompt("", prompt_suffix=" → ")
        except (KeyboardInterrupt, typer.Abort):
            console.print("\n[yellow]Aborted.[/]")
            raise typer.Exit(1)

        if answer.strip().lower() in ("done", "q", "quit", "exit", ""):
            context = context.model_copy(update={"is_complete": True})
            break

        try:
            context = agent.ingest(context, question=question, answer=answer)
        except Exception as exc:
            console.print(f"[red]Error processing answer:[/] {exc}")
            break

    # Mark complete regardless
    if not context.is_complete:
        context = context.model_copy(update={"is_complete": True})

    # Summary
    console.print()
    console.rule("[bold green]Discovery complete[/]")
    _print_context_summary(context)

    # Save context if requested
    if save:
        try:
            save_path = Path(save)
            save_path.write_text(context.model_dump_json(indent=2), encoding="utf-8")
            console.print(f"\n[dim]Context saved → [cyan]{save_path}[/][/dim]")
        except Exception as exc:
            console.print(f"[red]Could not save context:[/] {exc}")

    # Generate and show PRD preview
    console.print("\n[bold]Generating PRD from discovery context…[/bold]")
    try:
        prd = agent.finalize(context)
        console.print(
            f"\n[bold]PRD:[/bold] {prd.title}\n"
            f"[dim]{prd.summary}[/dim]\n"
        )
        if prd.functional_requirements:
            console.print("[bold]Functional requirements:[/bold]")
            for req in prd.functional_requirements:
                console.print(f"  • {req}")
    except Exception as exc:
        console.print(f"[yellow]PRD generation failed:[/] {exc}")
        prd = None

    # Optionally start a pipeline run
    if run_after and prd:
        console.print(f"\n[bold green]Starting {team} pipeline…[/bold green]\n")
        try:
            from antcrew.cli._shared import _build_team, _print_state
            from antcrew.utils.persistence import save_state

            active_team = _build_team(team, model, integrations=[])
            request = f"{prd.title}: {prd.summary}"
            result_state = active_team.run(request)

            _print_state(result_state, team)

            out_path = save or Path("antcrew-output.json")
            if save:
                out_path = save.with_suffix(".run.json")
            save_state(result_state, out_path)
            console.print(f"\n[dim]State saved → [cyan]{out_path}[/][/dim]")
        except Exception as exc:
            console.print(f"\n[red bold]Run error:[/] {exc}")
            raise typer.Exit(1)

    console.print("\n[bold green]Done![/]\n")


def _print_context_summary(context: DiscoveryContext) -> None:
    from rich.table import Table

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold dim", width=22)
    table.add_column("Value")

    def _row(label: str, value: object) -> None:
        if isinstance(value, list):
            text = ", ".join(str(v) for v in value) if value else "[dim]—[/dim]"
        else:
            text = str(value) if value else "[dim]—[/dim]"
        table.add_row(label, text)

    _row("Project", context.project_name)
    _row("Problem", context.problem_statement)
    _row("Target users", context.target_users)
    _row("Key features", context.key_features)
    _row("Tech stack", context.tech_stack)
    _row("Constraints", context.constraints)
    _row("Out of scope", context.out_of_scope)
    _row("Q&A rounds", str(len(context.qa_pairs)))

    console.print(table)
