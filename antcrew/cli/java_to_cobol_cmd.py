"""antcrew java-to-cobol — translate a Java source file to COBOL."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

_COBOL_EXTS = {".cbl", ".cob", ".cpy", ".copy"}
_MAX_JAVA_SIZE = 1 * 1024 * 1024  # 1 MB


@app.command(name="java-to-cobol")
def java_to_cobol_cmd(
    java_file: Path = typer.Argument(
        ...,
        help="Path to the Java source file (.java).",
        exists=True,
    ),
    standards_file: Optional[Path] = typer.Option(
        None, "--standards", "-s",
        help="COBOL file (.cbl/.cob/.cpy) or standards doc (.md/.txt) to learn naming from.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Directory to write the generated COBOL. Defaults to ./cobol_output/.",
    ),
    model: str = typer.Option(
        "claude", "--model", "-m",
        help="LLM to use (e.g. claude, claude-sonnet-5, openai:gpt-4o).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print output without writing files."),
    no_normalize: bool = typer.Option(False, "--no-normalize", help="Skip COBOLNormalizer pass."),
    refine_file: Optional[Path] = typer.Option(
        None, "--refine", "-r",
        help="Existing .cbl to refine instead of translating from scratch.",
    ),
    feedback: Optional[str] = typer.Option(
        None, "--feedback", "-f",
        help="Feedback text for --refine mode.",
    ),
) -> None:
    """Translate a Java source file to COBOL.

    Optionally learns naming conventions from an existing COBOL file or
    standards doc. Applies COBOLNormalizer and structural validation after
    translation.

    Requires polytranslate: pip install polytranslate

    Examples::

        antcrew java-to-cobol OrderProcessor.java
        antcrew java-to-cobol OrderProcessor.java --standards CLAIMS.cbl
        antcrew java-to-cobol OrderProcessor.java --standards COBOL_STANDARDS.md -o ./output
        antcrew java-to-cobol OrderProcessor.java --refine out.cbl --feedback "missing date check"
        antcrew java-to-cobol OrderProcessor.java --dry-run
    """
    try:
        from polytranslate.translators.java_to_cobol import JavaToCOBOLTranslator
    except ImportError:
        console.print(
            "[red]polytranslate is not installed.[/red]\n"
            "Run: [bold]pip install polytranslate[/bold]"
        )
        raise typer.Exit(1)

    # Validate Java input file
    java_size = java_file.stat().st_size
    if java_size == 0:
        console.print(f"[red]Java file is empty: {java_file}[/red]")
        raise typer.Exit(1)
    if java_size > _MAX_JAVA_SIZE:
        console.print(
            f"[red]Java file too large ({java_size / 1024:.0f} KB > 1 MB): {java_file}[/red]\n"
            "Split the file into smaller classes before translating."
        )
        raise typer.Exit(1)

    java_code = java_file.read_text(encoding="utf-8")
    if not java_code.strip():
        console.print(f"[red]Java file contains only whitespace: {java_file}[/red]")
        raise typer.Exit(1)

    try:
        from antcrew.config import build_llm
        llm = build_llm(model)
    except Exception as exc:
        console.print(f"[red]Could not load model '{model}': {exc}[/red]")
        raise typer.Exit(1)

    translator = JavaToCOBOLTranslator(llm=llm)

    if standards_file:
        console.print(f"[bold]Loading standards[/bold] from {standards_file.name}…")
        try:
            translator.load_standards(str(standards_file))
            s = translator.standards
            console.print(
                f"  prefixes: {s.var_prefixes}  |  "
                f"pattern: {s.paragraph_pattern}  |  "
                f"nesting: {s.max_nesting_levels}"
            )
        except Exception as exc:
            console.print(f"[red]Failed to load standards: {exc}[/red]")
            raise typer.Exit(1)

    normalize = not no_normalize

    if refine_file:
        if not feedback:
            console.print("[red]--feedback is required when using --refine[/red]")
            raise typer.Exit(1)
        current_cobol = refine_file.read_text(encoding="utf-8")
        console.print(f"[bold]Refining[/bold] {refine_file.name} with feedback…")
        try:
            cobol_code = translator.refine(java_code, current_cobol, feedback, normalize=normalize)
        except Exception as exc:
            console.print(f"[red]Refinement error: {exc}[/red]")
            raise typer.Exit(1)
    else:
        lines = len(java_code.splitlines())
        if lines > 150:
            console.print(f"[yellow]Large file ({lines} lines) — translating in chunks…[/yellow]")
        else:
            console.print(f"[bold]Translating[/bold] {java_file.name} → COBOL…")
        try:
            cobol_code = translator.translate(java_code, normalize=normalize)
        except TimeoutError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Translation error: {exc}[/red]")
            raise typer.Exit(1)

    # Structural validation
    validation = translator.validate(cobol_code)
    if validation.errors:
        console.print("[red bold]Validation errors:[/red bold]")
        for e in validation.errors:
            console.print(f"  [red]✗ {e}[/red]")
    if validation.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in validation.warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")
    if validation.valid and not validation.warnings:
        console.print("[green]✓ COBOL structure valid[/green]")

    if dry_run:
        console.rule(f"[cyan]{java_file.stem}.cbl[/cyan]")
        console.print(cobol_code[:4000] + ("…" if len(cobol_code) > 4000 else ""))
        return

    out = output_dir or Path(".") / "cobol_output"
    out.mkdir(parents=True, exist_ok=True)
    cobol_path = out / (java_file.stem + ".cbl")
    cobol_path.write_text(cobol_code, encoding="utf-8")

    console.print(f"[green]Written:[/green] {cobol_path}")
    console.print("[yellow]Review the generated code before using in production.[/yellow]")
