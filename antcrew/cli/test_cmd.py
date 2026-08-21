"""antcrew test — prompt mutation regression testing against a TraceLog."""
from __future__ import annotations

import json as _json
import sys as _sys
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import _MODEL_HELP, app, console


@app.command(name="test")
def test_cmd(
    db: Path = typer.Argument(..., help="TraceLog SQLite file (created with --full-trace)"),
    run_id: str = typer.Option(..., "--run", "-r", help="Run ID to replay"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name whose prompt to replace"),
    prompt_file: Optional[Path] = typer.Option(
        None, "--prompt", "-p",
        help="File containing the new system prompt.",
    ),
    prompt_text: Optional[str] = typer.Option(
        None, "--prompt-text",
        help="Inline new system prompt (mutually exclusive with --prompt).",
    ),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    threshold: float = typer.Option(
        0.20, "--threshold",
        help="Exit code 1 when diff_pct exceeds this value (0.0–1.0). Default: 0.20 (20%).",
    ),
    output_json: bool = typer.Option(False, "--json", help="Write results as JSON to stdout."),
) -> None:
    """Prompt regression testing via replay_with_mutation().

    Replays a recorded run substituting the system prompt for one agent and
    reports the diff percentage. Exits 1 when diff_pct > threshold — use as a
    CI/CD gate to catch prompt regressions before they reach production.

    \b
    Requirements:
        The run must have been recorded with --full-trace.

    \b
    File-based prompt:
        antcrew test ~/.antcrew/trace.db --run <id> --agent BA --prompt v2.txt

    \b
    Inline prompt:
        antcrew test ~/.antcrew/trace.db --run <id> --agent BA \\
          --prompt-text "You are a Business Analyst. Be concise."

    \b
    CI gate (fail when diff > 15%):
        antcrew test trace.db --run $RUN_ID --agent BA --prompt v2.txt --threshold 0.15

    \b
    JSON output for scripting:
        antcrew test trace.db --run $RUN_ID --agent BA --prompt v2.txt --json
    """
    from rich.table import Table

    from antcrew.trace import ReplayError, TraceLog

    # Validate inputs
    if not db.exists():
        console.print(f"[red]TraceLog not found:[/] {db}")
        raise typer.Exit(1)
    if prompt_file is None and prompt_text is None:
        console.print("[red]Provide --prompt <file> or --prompt-text <text>.[/]")
        raise typer.Exit(1)
    if prompt_file is not None and prompt_text is not None:
        console.print("[red]--prompt and --prompt-text are mutually exclusive.[/]")
        raise typer.Exit(1)

    if prompt_file is not None:
        if not prompt_file.exists():
            console.print(f"[red]Prompt file not found:[/] {prompt_file}")
            raise typer.Exit(1)
        new_prompt: str = prompt_file.read_text(encoding="utf-8")
    else:
        new_prompt = prompt_text  # type: ignore[assignment]

    from antcrew.config import build_llm as _build_llm

    llm = _build_llm(model)

    tlog = TraceLog(db)
    run = tlog.get_run(run_id)
    if run is None:
        console.print(f"[red]Run not found:[/] {run_id}")
        tlog.close()
        raise typer.Exit(1)

    console.print(
        f"\n[bold green]antcrew test[/]  "
        f"run=[cyan]{run_id[:8]}…[/]  agent=[yellow]{agent}[/]  "
        f"threshold=[dim]{threshold:.0%}[/dim]\n"
    )

    try:
        result = tlog.replay_with_mutation(run_id, agent, new_prompt, llm)
    except ReplayError as exc:
        console.print(f"[red]ReplayError:[/] {exc}")
        tlog.close()
        raise typer.Exit(1)
    finally:
        tlog.close()

    mutated_calls = [r for r in result["runs"] if r.get("mutated")]
    avg_diff_pct = (
        sum(r.get("diff_pct", 0.0) for r in mutated_calls) / len(mutated_calls)
        if mutated_calls else 0.0
    )
    passed = avg_diff_pct <= threshold

    if output_json:
        out = {
            "run_id": run_id,
            "agent_name": agent,
            "threshold": threshold,
            "diff_pct": avg_diff_pct,
            "passed": passed,
            "total_changed": result["total_changed"],
            "calls": result["runs"],
        }
        _sys.stdout.write(_json.dumps(out, indent=2, default=str) + "\n")
        if not passed:
            raise typer.Exit(1)
        return

    tbl = Table(show_header=True, header_style="bold dim")
    tbl.add_column("Agent",   style="cyan",  no_wrap=True)
    tbl.add_column("Mutated", justify="center")
    tbl.add_column("Diff %",  justify="right")
    tbl.add_column("Match",   justify="center")
    tbl.add_column("Cost",    justify="right")

    for r in result["runs"]:
        mutated_str = "[yellow]✎[/]" if r.get("mutated") else "—"
        diff_pct = r.get("diff_pct", 0.0)
        diff_str = f"{diff_pct:.1%}" if r.get("mutated") else "—"
        matched_str = "[green]✓[/]" if r.get("matched", True) else "[red]✗[/]"
        cost_str = f"${r.get('cost_usd', 0):.4f}"
        tbl.add_row(r["agent_name"], mutated_str, diff_str, matched_str, cost_str)

    console.print(tbl)

    color = "green" if passed else "red"
    icon = "✓ PASS" if passed else "✗ FAIL"
    console.print(
        f"\n[{color} bold]{icon}[/]  "
        f"diff={avg_diff_pct:.1%}  threshold={threshold:.0%}  "
        f"changed={result['total_changed']}/{len(result['runs'])} agents\n"
    )

    if not passed:
        raise typer.Exit(1)
