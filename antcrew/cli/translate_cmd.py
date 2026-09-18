"""antcrew translate-cobol — translate a COBOL program to Python, Java, or Go."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

_TARGETS = ["python", "java", "go"]


@app.command(name="translate-cobol")
def translate_cobol_cmd(
    cobol_file: Path = typer.Argument(
        ...,
        help="Path to the COBOL source file (.cbl, .cob, .cpy).",
        exists=True,
    ),
    target: str = typer.Option(
        "python", "--target", "-t",
        help=f"Target language: {', '.join(_TARGETS)}.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Directory to write the generated files. Defaults to ./translated/{program_id}/.",
    ),
    package: str = typer.Option(
        "com.example.legacy", "--package",
        help="Java package name (ignored for other targets).",
    ),
    module: str = typer.Option(
        "github.com/example/legacy", "--module",
        help="Go module path (ignored for other targets).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print output without writing files."),
) -> None:
    """Translate a COBOL program to Python, Java, or Go.

    Requires antcrew-translators: pip install antcrew-translators

    Examples::

        antcrew translate-cobol ORDPRC.cbl
        antcrew translate-cobol ORDPRC.cbl --target java --package com.acme.batch
        antcrew translate-cobol ORDPRC.cbl --target go --module github.com/acme/legacy
    """
    try:
        from translators.languages.cobol import CobolParser
    except ImportError:
        console.print(
            "[red]antcrew-translators is not installed.[/red]\n"
            "Run: [bold]pip install antcrew-translators[/bold]"
        )
        raise typer.Exit(1)

    if target not in _TARGETS:
        console.print(f"[red]Unknown target '{target}'. Choose from: {', '.join(_TARGETS)}[/red]")
        raise typer.Exit(1)

    generator = _build_generator(target, package, module)

    console.print(f"[bold]Parsing[/bold] {cobol_file.name}…")
    try:
        ast = CobolParser().parse_file(str(cobol_file))
    except Exception as exc:
        console.print(f"[red]Parse error: {exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Generating[/bold] {target} from {ast.program_id}…")
    try:
        files = generator.generate(ast)
    except Exception as exc:
        console.print(f"[red]Generation error: {exc}[/red]")
        raise typer.Exit(1)

    if dry_run:
        for f in files:
            console.rule(f"[cyan]{f.filename}[/cyan]")
            console.print(f.content[:3000] + ("…" if len(f.content) > 3000 else ""))
        return

    out = output_dir or (Path(".") / "translated" / ast.program_id.lower())
    out.mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Writing to {out}/[/green]")
    for f in files:
        written = f.write(out)
        console.print(f"  {written.name}")

    console.print(
        f"\n[green]✓[/green] {len(files)} file(s) written — "
        f"review generated code before using in production."
    )


def _build_generator(target: str, package: str, module: str):
    if target == "java":
        from translators.targets.java import JavaGenerator
        return JavaGenerator(package=package)
    if target == "go":
        from translators.targets.golang import GoGenerator
        return GoGenerator(module=module)
    from translators.targets.python import PythonGenerator
    return PythonGenerator()
