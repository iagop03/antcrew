"""antcrew write-back command — apply generated artifacts to their real paths."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console


def _resolve_root(state: dict, explicit: "Optional[Path]") -> Path:
    """Return the best project_root: explicit flag > state's project_dir > CWD."""
    if explicit is not None:
        return explicit.resolve()
    # CodebaseScannerAgent stores project_dirs as {"label": "/path"}
    project_dirs = state.get("project_dirs") or {}
    if project_dirs:
        first_path = next(iter(project_dirs.values()), None)
        if first_path and Path(first_path).is_dir():
            return Path(first_path).resolve()
    # Fallback: single project_dir
    project_dir = state.get("project_dir")
    if project_dir and Path(project_dir).is_dir():
        return Path(project_dir).resolve()
    return Path.cwd()


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
    patch_mode: bool = typer.Option(
        False, "--patch-mode",
        help=(
            "Apply only changed hunks to existing files instead of replacing them "
            "entirely. Equal lines are kept from the original, preserving exact "
            "formatting for code the agent did not intentionally modify."
        ),
    ),
) -> None:
    """Write generated artifacts from a saved state back to the project filesystem.

    By default, artifact file paths are written relative to the current directory
    (or --project-root). Files that already exist are shown as a unified diff and
    require confirmation unless --yes is passed.

    Use --patch-mode for brownfield projects: only the lines that actually changed
    are written; identical lines are kept from the original file.

    \b
    Examples:
        # Dry run — show what would change
        antcrew write-back run.json --dry-run

        # Write to current directory, confirm each modified file
        antcrew write-back run.json

        # Brownfield: apply only changed hunks, skip confirmation
        antcrew write-back run.json --project-root ~/myproject --patch-mode --yes

        # Write to an existing project, skip confirmation
        antcrew write-back run.json --project-root ~/myproject --yes
    """
    from antcrew.core.writeback import write_back
    from antcrew.utils.persistence import load_state

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    try:
        state = load_state(state_file)
    except Exception as exc:
        console.print(f"[red]Failed to load state:[/] {exc}")
        raise typer.Exit(1)

    root = _resolve_root(state, project_root)

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
        patch_mode=patch_mode,
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
