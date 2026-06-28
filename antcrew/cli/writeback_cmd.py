"""antcrew write-back command — apply generated artifacts to their real paths."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console


@app.command(name="write-back")
def writeback_cmd(
    state_file: Path = typer.Argument(..., help="Saved state JSON file (from antcrew run --save)"),
    project_root: Optional[Path] = typer.Option(
        None, "--project-root", "-p",
        help="Root directory to write files into (default: current directory).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Show what would be written without writing anything.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Overwrite existing files without confirmation.",
    ),
) -> None:
    """Write generated artifacts from a saved state back to the project filesystem.

    By default, artifact file paths are written relative to the current directory
    (or --project-root). Files that already exist are shown as a unified diff and
    require confirmation unless --yes is passed.

    \b
    Examples:
        # Dry run — show what would change
        antcrew write-back run.json --dry-run

        # Write to current directory, confirm each modified file
        antcrew write-back run.json

        # Write to an existing project, skip confirmation
        antcrew write-back run.json --project-root ~/myproject --yes

        # Typical brownfield workflow:
        antcrew run "Add rate limiting to the auth API" --team fullstack \\
          --config agentteam.yaml --save run.json
        antcrew write-back run.json --project-root ~/myproject --dry-run
        antcrew write-back run.json --project-root ~/myproject
    """
    from antcrew.utils.persistence import load_state
    from antcrew.core.writeback import write_back

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    root = (project_root or Path.cwd()).resolve()

    try:
        state = load_state(state_file)
    except Exception as exc:
        console.print(f"[red]Failed to load state:[/] {exc}")
        raise typer.Exit(1)

    if dry_run:
        console.print(f"\n[bold]Dry run[/bold] — artifacts that would be written to [cyan]{root}[/cyan]:\n")
    else:
        console.print(f"\nWriting artifacts to [cyan]{root}[/cyan]\n")

    def _print(msg: str) -> None:
        console.print(msg)

    def _confirm(prompt: str) -> bool:
        return typer.confirm(prompt, default=False)

    result = write_back(
        state,
        root,
        dry_run=dry_run,
        yes=yes,
        confirm_fn=None if yes else _confirm,
        print_fn=_print,
    )

    if dry_run:
        total = len(result.entries)
        console.print(
            f"\n[dim]{total} artifact(s) would be written "
            f"({sum(1 for e in result.entries if e.operation == 'create')} new, "
            f"{sum(1 for e in result.entries if e.operation == 'modify')} modify).[/dim]"
        )
    else:
        console.print(
            f"\n[bold green]Done![/]  "
            f"{result.total_written} written  "
            f"({len(result.created)} new, {len(result.modified)} modified"
            + (f", {len(result.skipped)} skipped" if result.skipped else "")
            + ")\n"
        )
