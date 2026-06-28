"""Interactive and eval commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.panel import Panel

from antcrew.cli._app import app, console, _MODEL_HELP, _TEAM_CHOICES
from antcrew.cli._shared import _build_team, _print_state, _print_test_results, _print_usage

def _extract_state_to_dir(state: dict, output_dir: "Path") -> None:
    """Write code/test/devops artifacts from a state dict to real files on disk."""
    from pathlib import Path as _P

    output_dir = _P(output_dir)
    artifacts: list[tuple[str, str]] = []

    def _collect(key: str):
        for a in state.get(key) or []:
            raw = a if isinstance(a, dict) else (a.model_dump() if hasattr(a, "model_dump") else {})
            if raw.get("file_path") and raw.get("content") is not None:
                artifacts.append((raw["file_path"], raw["content"]))

    _collect("code_artifacts")
    _collect("test_artifacts")
    _collect("devops_artifacts")

    if not artifacts:
        console.print("[yellow]No artifacts to write.[/]")
        return

    console.print(f"\n[bold green]Writing {len(artifacts)} file(s) → [cyan]{output_dir}/[/][/bold green]\n")
    for rel, content in artifacts:
        dest = output_dir / rel.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        console.print(f"  [green]✓[/] {dest}")
    console.print()


@app.command()
def interactive(
    request: str = typer.Argument(..., help="Task or topic for the team"),
    team: str = typer.Option("dev", "--team", "-t", help=f"Team to use: {_TEAM_CHOICES}"),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to agentteam.yaml"
    ),
    thread: str = typer.Option("default", "--thread", help="Thread ID for checkpointing"),
    save: Optional[Path] = typer.Option(
        None, "--save", "-s", help="Save final state to a JSON file (default: antcrew-output.json)"
    ),
    output_dir: Optional[Path] = typer.Option(
        Path("generated"), "--output-dir", "-o",
        help="Write generated files to this directory (default: ./generated). Pass 'none' to skip.",
    ),
    project_dir: Optional[list[str]] = typer.Option(
        None, "--project-dir", "-p",
        help=(
            "Existing project dir(s) to scan before the BA runs. "
            "Single: --project-dir ./src  "
            "Multi: --project-dir frontend:./fe --project-dir backend:./be"
        ),
    ),
) -> None:
    """Run a pipeline with human-in-the-loop review after each agent.

    After every agent you can: approve (continue), reject (stop), fix (send
    back to backend_dev for targeted fixes), edit (open JSON editor), or type
    free-text feedback to trigger conversational refinement.

    Generated files are written to ./generated/ automatically (or --output-dir).
    The raw state is saved to antcrew-output.json (or --save).
    """
    console.print(
        f"\n[bold green]AntCrew interactive[/]  —  "
        f"team=[cyan]{team}[/]  model=[cyan]{model}[/]\n"
    )

    try:
        if config:
            from antcrew import config as cfg_module
            active_team = cfg_module.load(config)
            team = type(active_team).__name__.lower().replace("team", "")
        else:
            active_team = _build_team(team, model, integrations=[])

        # CLI --project-dir overrides whatever is in the YAML.
        if project_dir and hasattr(active_team, "project_dir"):
            parsed: dict[str, str] = {}
            for entry in project_dir:
                if ":" in entry:
                    label, _, path = entry.partition(":")
                    parsed[label.strip()] = path.strip()
                else:
                    from pathlib import Path as _P
                    parsed[_P(entry).name] = entry
            if len(parsed) == 1:
                active_team.project_dir = next(iter(parsed.values()))
                active_team.project_dirs = None
                console.print(f"[dim]Scanning project: [cyan]{active_team.project_dir}[/][/dim]\n")
            elif parsed:
                active_team.project_dirs = parsed
                active_team.project_dir = None
                labels = ", ".join(f"[cyan]{k}[/]" for k in parsed)
                console.print(f"[dim]Scanning components: {labels}[/dim]\n")

        state = active_team.run_interactive(request, thread_id=thread)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"\n[red bold]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print()
    _print_state(state, team)

    # Always save state JSON.
    out_path = save or Path("antcrew-output.json")
    from antcrew.utils.persistence import save_state
    save_state(state, out_path)
    console.print(f"\n[dim]State saved → [cyan]{out_path}[/][/dim]")

    # Resolve output_dir: CLI flag > YAML > default ./generated
    yaml_output_dir = getattr(active_team, "output_dir", None)
    effective_output_dir = output_dir if str(output_dir).lower() != "none" else None
    if effective_output_dir is None and yaml_output_dir:
        effective_output_dir = Path(yaml_output_dir)
    elif effective_output_dir is None:
        effective_output_dir = Path("generated")

    if str(effective_output_dir).lower() != "none":
        _extract_state_to_dir(state, effective_output_dir)

    console.print("\n[bold green]Done![/]\n")


@app.command(name="eval")
def eval_cmd(
    cases_file: Path = typer.Argument(..., help="JSON file with one EvalCase or a list"),
    team: str = typer.Option("dev",  "--team",  "-t", help=f"Team to use: {_TEAM_CHOICES}"),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    judge_model: Optional[str] = typer.Option(
        None, "--judge-model", "-j",
        help="Model for LLM-as-judge scoring (omit to use structural metrics only)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save full JSON results to this file"
    ),
    fail_fast: bool = typer.Option(
        False, "--fail-fast", help="Stop after the first failing case"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-agent scores for every case"
    ),
) -> None:
    """Run evaluation cases from a JSON file and report structural + LLM-judge scores.

    CASES_FILE must contain a JSON object (single case) or array of objects.
    Each object maps to an EvalCase: 'request' is required, everything else
    is optional.

    Example cases.json:
    \b
        [
          {"request": "Build JWT auth", "name": "jwt", "expect_min_tickets": 2},
          {"request": "Add Stripe payments", "name": "stripe"}
        ]

    Exit code is 0 when all cases pass, 1 when any fails — useful in CI.
    """
    from antcrew.config import build_llm
    from antcrew.eval import EvalCase, EvalRunner
    from rich.table import Table

    # ── Load cases ────────────────────────────────────────────────────────────
    if not cases_file.exists():
        console.print(f"[red]File not found:[/] {cases_file}")
        raise typer.Exit(1)

    try:
        raw = json.loads(cases_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/] {exc}")
        raise typer.Exit(1)

    if isinstance(raw, dict):
        raw = [raw]

    try:
        cases = [EvalCase(**c) for c in raw]
    except (TypeError, ValueError) as exc:
        console.print(f"[red]Invalid case format:[/] {exc}")
        raise typer.Exit(1)

    n = len(cases)
    judge_info = f"  judge=[cyan]{judge_model}[/]" if judge_model else ""
    console.print(
        f"\n[bold green]AntCrew eval[/] — "
        f"team=[cyan]{team}[/]  model=[cyan]{model}[/]{judge_info}  "
        f"[dim]{n} case{'s' if n != 1 else ''}[/dim]\n"
    )

    # ── Build runner ──────────────────────────────────────────────────────────
    try:
        llm = build_llm(model)
        active_team = _build_team(team, model, integrations=[], llm=llm)
        judge_llm = build_llm(judge_model) if judge_model else None
        runner = EvalRunner(active_team, judge_llm=judge_llm)
    except Exception as exc:
        console.print(f"[red bold]Setup error:[/] {exc}")
        raise typer.Exit(1)

    # ── Run cases ─────────────────────────────────────────────────────────────
    reports = []
    any_failed = False

    for i, case in enumerate(cases, 1):
        label = case.name or case.request[:40]
        with console.status(f"[bold]Running [{i}/{n}] {label}…[/]"):
            try:
                report = runner.run_one(case)
            except Exception as exc:
                console.print(f"  [{i}/{n}] [red]ERROR[/] {label}: {exc}")
                any_failed = True
                if fail_fast:
                    break
                continue

        reports.append(report)

        status = "[bold green]PASS[/]" if report.passed else "[bold red]FAIL[/]"
        struct = f"{report.overall_score:.2f}"
        judge  = f"{report.judge_score:.2f}" if report.judge_results else "—"
        usage  = report.token_usage or {}
        tokens = f"{usage.get('total_input_tokens', 0) + usage.get('total_output_tokens', 0):,}"
        elapsed = f"{report.elapsed_ms:.0f}ms"

        console.print(
            f"  [{i}/{n}] [{status}]  "
            f"[dim]{label}[/dim]  "
            f"structural=[cyan]{struct}[/]  judge=[cyan]{judge}[/]  "
            f"tokens={tokens}  {elapsed}"
        )

        if verbose:
            for line in report.summary().splitlines()[1:]:
                console.print(f"       [dim]{line}[/dim]")
            if report.errors:
                for err in report.errors:
                    console.print(f"       [red]⚠[/] {err}")

        if not report.passed:
            any_failed = True
            if fail_fast:
                console.print("\n[yellow]--fail-fast: stopping after first failure.[/]")
                break

    if not reports:
        console.print("[yellow]No reports generated.[/]")
        raise typer.Exit(1)

    # ── Summary table ─────────────────────────────────────────────────────────
    console.print()
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    table.add_column("Name",       style="cyan",  no_wrap=True)
    table.add_column("Pass",       justify="center")
    table.add_column("Structural", justify="right")
    table.add_column("Judge",      justify="right")
    table.add_column("Tokens",     justify="right")
    table.add_column("ms",         justify="right")

    passed_count = sum(1 for r in reports if r.passed)

    for r in reports:
        label  = r.case.name or r.case.request[:30]
        tick   = "[green]✓[/]" if r.passed else "[red]✗[/]"
        struct = f"{r.overall_score:.2f}"
        judge  = f"{r.judge_score:.2f}" if r.judge_results else "—"
        usage  = r.token_usage or {}
        tokens = f"{usage.get('total_input_tokens', 0) + usage.get('total_output_tokens', 0):,}"
        ms     = f"{r.elapsed_ms:.0f}"
        table.add_row(label, tick, struct, judge, tokens, ms)

    console.print(table)

    total   = len(reports)
    overall = sum(r.overall_score for r in reports) / total if total else 0.0
    colour  = "green" if passed_count == total else "red"
    console.print(
        f"\n[{colour}]{passed_count}/{total} passed[/{colour}]  "
        f"— avg structural=[cyan]{overall:.2f}[/]\n"
    )

    # ── Save output ───────────────────────────────────────────────────────────
    if output:
        data = runner.to_json(reports)
        output.write_text(data, encoding="utf-8")
        console.print(f"[dim]Results saved → [cyan]{output}[/][/dim]\n")

    if any_failed:
        raise typer.Exit(1)


