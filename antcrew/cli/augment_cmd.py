"""antcrew augment-cobol — add AI to a COBOL program without rewriting it."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import _MODEL_HELP, app, console


@app.command(name="augment-cobol")
def augment_cobol_cmd(
    cobol_file: Path = typer.Argument(
        ...,
        help="Path to the COBOL source file (.cbl, .cob, .cpy).",
        exists=True,
    ),
    requirement: str = typer.Option(
        ..., "--requirement", "-r",
        help="What AI capability to add (e.g. 'Add ML fraud scoring').",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help=f"LLM to use for wrapper generation. {_MODEL_HELP}. "
             "Omit to generate a static template wrapper.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Directory to write the generated files. Defaults to the COBOL file's directory.",
    ),
    show_guide: bool = typer.Option(False, "--guide", help="Also print the deployment guide."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print output without writing files."),
) -> None:
    """Augment a COBOL program with AI capabilities.

    Analyses the COBOL source, generates a Python AI wrapper and a COBOL bridge
    caller, and writes them to the output directory.

    Examples::

        antcrew augment-cobol ORDPRC.cbl --requirement "Add ML fraud scoring"
        antcrew augment-cobol ORDPRC.cbl -r "Add ML fraud scoring" -m claude-3-5-sonnet
    """
    from antcrew.augment.cobol import COBOLAugment

    llm = None
    if model:
        try:
            from antcrew.config import build_llm
            llm = build_llm(model)
        except Exception as exc:
            console.print(f"[yellow]Warning: could not load LLM '{model}': {exc}. "
                          "Falling back to static template.[/yellow]")

    console.print(f"[bold]Analysing[/bold] {cobol_file.name}…")

    aug = COBOLAugment(llm=llm)

    try:
        result = aug.augment(str(cobol_file), requirement)
    except Exception as exc:
        console.print(f"[red]Error during augmentation: {exc}[/red]")
        raise typer.Exit(1)

    analysis = result.analysis
    console.print(
        f"[green]✓[/green] Program: [bold]{analysis.program_id}[/bold] — "
        f"{len(analysis.paragraphs)} paragraphs, "
        f"{len(analysis.data_items)} data items"
    )

    out = output_dir or cobol_file.parent
    stem = _safe_stem(analysis.program_id)
    wrapper_path = out / f"{stem}_ai.py"
    caller_path = out / f"{stem}_ai_caller.cbl"
    guide_path = out / f"{stem}_deployment_guide.md"

    if dry_run:
        console.rule("[cyan]Python AI wrapper[/cyan]")
        console.print(result.python_wrapper[:2000] + ("…" if len(result.python_wrapper) > 2000 else ""))
        console.rule("[cyan]COBOL caller[/cyan]")
        console.print(result.cobol_caller)
        if show_guide:
            console.rule("[cyan]Deployment guide[/cyan]")
            console.print(result.deployment_guide)
        return

    out.mkdir(parents=True, exist_ok=True)
    wrapper_path.write_text(result.python_wrapper, encoding="utf-8")
    caller_path.write_text(result.cobol_caller, encoding="utf-8")
    guide_path.write_text(result.deployment_guide, encoding="utf-8")

    console.print("[green]Written:[/green]")
    console.print(f"  Python wrapper  → {wrapper_path}")
    console.print(f"  COBOL caller    → {caller_path}")
    console.print(f"  Deployment guide→ {guide_path}")

    if show_guide:
        console.rule("[cyan]Deployment guide[/cyan]")
        console.print(result.deployment_guide)


def _safe_stem(program_id: str) -> str:
    return program_id.lower().replace("-", "_").replace(" ", "_")
