"""antcrew sprint — standalone sprint planner CLI command."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from antcrew.cli._app import app, console


@app.command(name="sprint")
def sprint_cmd(
    tickets_file: Optional[Path] = typer.Argument(
        None,
        help="JSON file with ticket list (array of strings or objects with a 'title' key). "
             "Omit to read from stdin.",
    ),
    sprint_size: int = typer.Option(4, "--size", "-s", help="Tickets per sprint."),
    output_json: bool = typer.Option(False, "--json", help="Output sprints as JSON."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write JSON output to file."),
) -> None:
    """Divide a ticket backlog into fixed-size sprints.

    \b
    Read tickets from a JSON file:
        antcrew sprint backlog.json --size 5

    \b
    Read tickets from stdin (pipe from antcrew run):
        echo '["Add auth", "Add billing", "Write tests"]' | antcrew sprint --json

    \b
    backlog.json can be a plain array:
        ["Add auth", "Implement billing", "Write tests", "Set up CI", "Deploy"]

    or an array of objects:
        [{"title": "Add auth", "priority": "high"}, ...]
    """
    import sys

    # ── Load tickets ──────────────────────────────────────────────────────────
    if tickets_file is not None:
        if not tickets_file.exists():
            console.print(f"[red]File not found:[/] {tickets_file}")
            raise typer.Exit(1)
        try:
            raw = json.loads(tickets_file.read_text(encoding="utf-8"))
        except Exception as exc:
            console.print(f"[red]Failed to parse tickets file:[/] {exc}")
            raise typer.Exit(1)
    else:
        try:
            raw = json.loads(sys.stdin.read())
        except Exception as exc:
            console.print(f"[red]Failed to parse stdin as JSON:[/] {exc}")
            raise typer.Exit(1)

    if isinstance(raw, dict) and "tickets" in raw:
        raw = raw["tickets"]
    if not isinstance(raw, list):
        console.print("[red]Tickets must be a JSON array.[/]")
        raise typer.Exit(1)

    tickets = [
        (t.get("title") or t.get("name") or str(t)) if isinstance(t, dict) else str(t)
        for t in raw
        if t
    ]

    if not tickets:
        console.print("[yellow]No tickets found.[/]")
        raise typer.Exit(0)

    # ── Partition into sprints ────────────────────────────────────────────────
    sprints: list[dict] = []
    for i in range(0, len(tickets), sprint_size):
        batch = tickets[i : i + sprint_size]
        sprints.append({
            "sprint": len(sprints) + 1,
            "tickets": batch,
            "count": len(batch),
        })

    # ── Output ────────────────────────────────────────────────────────────────
    if output_json or output is not None:
        result = {
            "total_tickets": len(tickets),
            "sprint_size": sprint_size,
            "total_sprints": len(sprints),
            "sprints": sprints,
        }
        payload = json.dumps(result, indent=2)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            if not output_json:
                console.print(f"[green]Sprints written to[/] {output}")
                return
        typer.echo(payload)
        return

    console.print(
        f"\n[bold]Sprint plan[/]  "
        f"[dim]{len(tickets)} tickets · {sprint_size} per sprint → {len(sprints)} sprint(s)[/]\n"
    )
    for sp in sprints:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("", style="dim")
        table.add_column("")
        for j, t in enumerate(sp["tickets"], 1):
            table.add_row(f"{j}.", t)
        console.print(Panel(table, title=f"[bold cyan]Sprint {sp['sprint']}[/]", expand=False))
