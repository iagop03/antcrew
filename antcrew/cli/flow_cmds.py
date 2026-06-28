"""Flow sub-app commands (flow show / flow validate)."""
from __future__ import annotations

from pathlib import Path

import typer

from antcrew.cli._app import _flow_app, console

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


# ---------------------------------------------------------------------------
# antcrew trace — inspect TraceLog SQLite files
# ---------------------------------------------------------------------------

