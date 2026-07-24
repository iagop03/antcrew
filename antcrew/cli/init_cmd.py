"""antcrew init command."""
from __future__ import annotations

from pathlib import Path

import typer

from antcrew.cli._app import app, console
from antcrew.cli._templates import (
    _MAIN_CONTENT,
    _MAIN_CUSTOM,
    _MAIN_DEV,
    _MAIN_FULLSTACK,
    _MAIN_RESEARCH,
    _YAML_CONTENT,
    _YAML_CUSTOM,
    _YAML_DEV,
    _YAML_FULLSTACK,
    _YAML_RESEARCH,
)


@app.command()
def init(
    template: str = typer.Option(
        "dev_team",
        "--template",
        help="Template to generate: dev_team | research_team | content_team",
    ),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Output directory"),
) -> None:
    """Generate a starter agentteam.yaml + main.py for a given template."""
    templates: dict[str, tuple[str, str]] = {
        "dev_team":       (_YAML_DEV,       _MAIN_DEV),
        "fullstack_team": (_YAML_FULLSTACK,  _MAIN_FULLSTACK),
        "research_team":  (_YAML_RESEARCH,   _MAIN_RESEARCH),
        "content_team":   (_YAML_CONTENT,    _MAIN_CONTENT),
        "custom":         (_YAML_CUSTOM,     _MAIN_CUSTOM),
    }

    if template not in templates:
        console.print(f"[red]Unknown template '{template}'.[/] Choose from: {list(templates)}")
        raise typer.Exit(1)

    yaml_content, main_content = templates[template]
    output.mkdir(parents=True, exist_ok=True)

    yaml_path = output / "agentteam.yaml"
    main_path = output / "main.py"

    yaml_path.write_text(yaml_content, encoding="utf-8")
    main_path.write_text(main_content, encoding="utf-8")

    console.print("\n[bold green]Generated:[/]")
    console.print(f"  [cyan]{yaml_path}[/]  — team configuration")
    console.print(f"  [cyan]{main_path}[/]  — entry point\n")
    console.print("Run with:")
    console.print(f"  [bold]antcrew run \"Your request\" --config {yaml_path}[/]")
    console.print(f"  [bold]python {main_path}[/]\n")


