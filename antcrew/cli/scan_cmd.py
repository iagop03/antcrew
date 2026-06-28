"""antcrew scan — preview what CodebaseScannerAgent would see in a project directory."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console, _MODEL_HELP


@app.command(name="scan")
def scan_cmd(
    paths: list[str] = typer.Argument(
        ...,
        help="Directories to scan. Use 'label:path' for named components "
             "(e.g. backend:./src frontend:./client).",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help=f"Run LLM analysis after scanning. {_MODEL_HELP}. Omit to show file tree only.",
    ),
    no_tree: bool = typer.Option(False, "--no-tree", help="Skip the file tree display."),
    output_json: bool = typer.Option(False, "--json", help="Output analysis as JSON (suppresses Rich output)."),
) -> None:
    """Preview what CodebaseScannerAgent sees in one or more project directories.

    Without --model, shows the file tree and key files that would be sent to the LLM.
    With --model, also runs the LLM analysis and shows the full CodebaseAnalysis.

    \b
    Examples:
        # File tree only — no LLM, no API key needed
        antcrew scan ./src

        # Named components
        antcrew scan backend:./src frontend:./client

        # Full analysis with LLM
        antcrew scan ./src --model claude

        # Local model, no API key
        antcrew scan ./src --model ollama:llama3
    """
    import json as _json

    from rich.panel import Panel
    from rich.table import Table

    from antcrew.agents.codebase_scanner import (
        _IGNORE_DIRS, _KEY_FILES, _MAX_TREE_LINES,
        _build_tree,
    )

    # Parse "label:path" or bare "path" entries.
    # colon > 1 skips Windows drive letters (C:\...).
    dirs: dict[str, Path] = {}
    for entry in paths:
        colon = entry.find(":")
        if colon > 1:
            label = entry[:colon].strip()
            path_str = entry[colon + 1:].strip()
            dirs[label] = Path(path_str).expanduser().resolve()
        else:
            p = Path(entry).expanduser().resolve()
            dirs[p.name] = p

    bad = [f"{label}:{path}" for label, path in dirs.items() if not path.is_dir()]
    if bad:
        console.print(f"[red]Not a directory:[/] {', '.join(bad)}")
        raise typer.Exit(1)

    json_results: list[dict] = []

    for label, root in dirs.items():
        if not output_json:
            console.print(f"\n[bold cyan]{label}[/bold cyan]  [dim]{root}[/dim]\n")

        # ── File tree ──────────────────────────────────────────────────────────
        tree = _build_tree(root, _IGNORE_DIRS)
        lines = tree.splitlines()
        found_keys = [name for name in _KEY_FILES if (root / name).exists()]

        if not output_json and not no_tree:
            truncated = len(lines) >= _MAX_TREE_LINES
            display = "\n".join(lines[:50])
            if truncated or len(lines) > 50:
                display += f"\n[dim]… ({len(lines)} total lines)[/dim]"
            console.print(Panel(display, title="File tree", border_style="dim", expand=False))

        # ── Key files found ────────────────────────────────────────────────────
        if not output_json:
            if found_keys:
                console.print(f"[dim]Key files:[/dim] {', '.join(found_keys)}")
            else:
                console.print("[dim]No key files detected (README, package.json, pyproject.toml, …)[/dim]")

        component: dict = {
            "label": label,
            "path": str(root),
            "file_count": len(lines),
            "key_files": found_keys,
        }

        # ── LLM analysis (optional) ────────────────────────────────────────────
        if model:
            from antcrew.config import build_llm
            from antcrew.agents.codebase_scanner import _scan_one, CodebaseScannerAgent

            if not output_json:
                console.print(f"\n[dim]Running LLM analysis with {model}…[/dim]")
            llm = build_llm(model)
            agent = CodebaseScannerAgent(llm=llm)
            analysis = _scan_one(agent, label, str(root))

            if analysis is None:
                if not output_json:
                    console.print("[red]Analysis failed.[/red]")
                component["error"] = "analysis_failed"
            else:
                if not output_json:
                    tbl = Table(show_header=False, box=None, padding=(0, 2))
                    tbl.add_column("Field", style="dim", no_wrap=True)
                    tbl.add_column("Value")
                    if analysis.tech_stack:
                        tbl.add_row("Tech stack", ", ".join(analysis.tech_stack))
                    if analysis.existing_modules:
                        tbl.add_row("Modules", ", ".join(analysis.existing_modules[:8]))
                    if analysis.entry_points:
                        tbl.add_row("Entry points", ", ".join(analysis.entry_points[:5]))
                    if analysis.test_coverage_summary:
                        tbl.add_row("Tests", analysis.test_coverage_summary)
                    if analysis.what_exists:
                        tbl.add_row("What exists", analysis.what_exists)
                    if analysis.what_is_missing:
                        tbl.add_row("What's missing", analysis.what_is_missing)
                    if analysis.continuation_context:
                        tbl.add_row("Context", analysis.continuation_context)
                    console.print(tbl)
                component.update(analysis.model_dump(exclude_none=True))

        json_results.append(component)

    if output_json:
        out = json_results[0] if len(json_results) == 1 else {"components": json_results}
        typer.echo(_json.dumps(out, indent=2))
    else:
        console.print()
