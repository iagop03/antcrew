"""antcrew test — run QA test artifacts from a saved pipeline state."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel

from antcrew.cli._app import app, console


def _print_test_results(tr) -> None:
    if tr is None:
        return
    if hasattr(tr, "success"):
        success = tr.success
        summary = tr.summary()
    else:
        passed = int(tr.get("passed", 0))
        failed = int(tr.get("failed", 0))
        errors = int(tr.get("errors", 0))
        success = bool(tr.get("success", failed == 0 and errors == 0))
        ms = float(tr.get("duration_ms", 0))
        parts = []
        if passed:
            parts.append(f"{passed} passed")
        if failed:
            parts.append(f"{failed} failed")
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        summary = (", ".join(parts) or "no tests ran") + f" in {ms:.0f}ms"

    colour = "green" if success else "red"
    icon = "✓" if success else "✗"
    console.print(Panel(summary, title=f"[{colour}]Tests {icon}[/{colour}]", border_style=colour))


@app.command(name="test")
def test_cmd(
    state_file: Path = typer.Argument(
        ...,
        help="JSON state file produced by 'antcrew run --save' or 'antcrew interactive'.",
    ),
    runner: str = typer.Option(
        "local", "--runner", "-r",
        help="Execution backend: local (same Python, temp dir) | docker (isolated container).",
    ),
    timeout: int = typer.Option(
        60, "--timeout", help="Seconds before killing pytest (default: 60)."
    ),
    keep: Optional[Path] = typer.Option(
        None, "--keep", "-k",
        help="Also write test + code files to this directory and keep them after the run.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Always print full pytest output (default: only on failure).",
    ),
    no_code: bool = typer.Option(
        False, "--no-code",
        help="Skip code artifacts — run test files only (useful when the project already exists).",
    ),
) -> None:
    """Run test artifacts from a saved pipeline state.

    \b
    Basic usage:
        antcrew test antcrew-output.json

    \b
    Isolated Docker run with longer timeout:
        antcrew test antcrew-output.json --runner docker --timeout 120

    \b
    Keep generated files on disk for inspection:
        antcrew test antcrew-output.json --keep ./test-output

    \b
    Run only the test files (project already checked out separately):
        antcrew test antcrew-output.json --no-code

    Exit code is 0 when all tests pass, 1 otherwise.

    For prompt mutation regression testing use 'antcrew regtest'.
    """
    from antcrew.core.artifacts import CodeArtifact, TestArtifact
    from antcrew.sandbox import DockerRunner, LocalRunner
    from antcrew.sandbox.runner import _write_artifacts
    from antcrew.utils.persistence import load_state

    if not state_file.exists():
        console.print(f"[red]State file not found:[/] {state_file}")
        raise typer.Exit(1)

    try:
        raw = load_state(state_file)
    except Exception as exc:
        console.print(f"[red]Failed to load state file:[/] {exc}")
        raise typer.Exit(1)

    if "state" in raw and isinstance(raw.get("state"), dict):
        raw = raw["state"]

    test_arts_raw = raw.get("test_artifacts") or []
    code_arts_raw = [] if no_code else (raw.get("code_artifacts") or [])

    if not test_arts_raw:
        console.print(
            f"[yellow]No test artifacts found in[/] [cyan]{state_file}[/]\n"
            "[dim]Run a pipeline first (DevTeam or FullStackTeam with QA enabled).[/dim]"
        )
        raise typer.Exit(0)

    try:
        test_artifacts = [
            TestArtifact.model_validate(a) if isinstance(a, dict) else a
            for a in test_arts_raw
        ]
        code_artifacts = [
            CodeArtifact.model_validate(a) if isinstance(a, dict) else a
            for a in code_arts_raw
        ]
    except Exception as exc:
        console.print(f"[red]Failed to parse artifacts:[/] {exc}")
        raise typer.Exit(1)

    runner_label = runner.strip().lower()
    console.print(
        f"\n[bold green]antcrew test[/]  [cyan]{state_file}[/]  "
        f"[dim]{len(test_artifacts)} test file(s)"
        + (f"  +{len(code_artifacts)} code file(s)" if code_artifacts else "")
        + f"  runner={runner_label}[/dim]\n"
    )

    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        _write_artifacts(keep, test_artifacts, code_artifacts)
        console.print(f"[dim]Files written → [cyan]{keep}/[/][/dim]\n")

    if runner_label == "docker":
        active_runner = DockerRunner(timeout=timeout)
    elif runner_label == "local":
        active_runner = LocalRunner(timeout=timeout)
    else:
        console.print(
            f"[red]Unknown runner:[/] {runner_label!r}  (choose 'local' or 'docker')"
        )
        raise typer.Exit(1)

    with console.status("[bold]Running tests…[/]"):
        result = active_runner.run(test_artifacts, code_artifacts=code_artifacts)

    _print_test_results(result)

    if verbose or not result.success:
        output = result.output or ""
        tail = output[-3000:] if len(output) > 3000 else output
        if tail.strip():
            console.print(Panel(tail, title="pytest output", border_style="dim"))

    if not result.success:
        raise typer.Exit(1)
