"""antcrew engine — capability-driven project builder.

Usage:
    antcrew engine "Build a REST API for a todo app" --model claude --output ./my-project
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from antcrew.cli._app import app, console, _MODEL_HELP
from antcrew.engine import (
    ArtifactId, ArtifactKind,
    CapabilityRegistry, Condition, ConditionId, Constraints,
    DesiredProjectState, EventLog, Goal, MemoryStore, Operator,
)
from antcrew.capabilities import (
    Architect, CodeGenerator, CodeReviewer,
    SpecExtractor, TaskPlanner, TestGenerator, TestRunner,
)
from antcrew.capabilities.validators import (
    AllTasksCompletedValidator, CodeReviewedValidator,
    TestsExistValidator, TestsPassValidator, artifact_validators,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_registry(llm) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(SpecExtractor(llm=llm))
    registry.register(Architect(llm=llm))
    registry.register(TaskPlanner(llm=llm))
    registry.register(CodeGenerator(llm=llm))
    registry.register(TestGenerator(llm=llm))
    registry.register(TestRunner())
    registry.register(CodeReviewer(llm=llm))
    return registry


def _build_validators() -> list:
    return [
        *artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
            ("task_graph",   "task_graph_exists"),
        ),
        AllTasksCompletedValidator(),
        TestsExistValidator(),
        TestsPassValidator(),
        CodeReviewedValidator(),
    ]


def _build_goal(
    description: str,
    tech_stack: tuple[str, ...],
    conditions: list[str],
    full_pipeline: bool,
) -> Goal:
    default_conditions = [
        ("requirements_exists", "requirements document written"),
        ("architecture_exists", "architecture designed"),
        ("task_graph_exists",   "tasks planned"),
        ("implementation_exists", "all tasks implemented"),
        ("tests_exist",  "test suite written"),
        ("tests_pass",   "tests passing"),
        ("code_reviewed", "code reviewed and approved"),
    ] if full_pipeline else [
        ("requirements_exists", "requirements document written"),
        ("architecture_exists", "architecture designed"),
        ("task_graph_exists",   "tasks planned"),
    ]

    if conditions:
        cond_set = frozenset(
            Condition(ConditionId(c.strip()), c.strip()) for c in conditions
        )
    else:
        cond_set = frozenset(Condition(ConditionId(cid), desc) for cid, desc in default_conditions)

    return Goal(
        description=description,
        desired_state=DesiredProjectState(cond_set),
        constraints=Constraints(tech_stack=tech_stack) if tech_stack else Constraints(),
    )


def _write_output(store: MemoryStore, output_dir: Path) -> list[Path]:
    written: list[Path] = []
    for kind in (ArtifactKind.SOURCE, ArtifactKind.TEST, ArtifactKind.DOCUMENTATION, ArtifactKind.CONFIG):
        for artifact in store.list(kind):
            file_path = artifact.metadata.get("file_path") or f"{artifact.id}"
            dest = output_dir / file_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = artifact.content if isinstance(artifact.content, str) else json.dumps(artifact.content, indent=2)
            dest.write_text(content, encoding="utf-8")
            written.append(dest)
    return written


def _write_reports(store: MemoryStore, output_dir: Path) -> None:
    for artifact in store.list(ArtifactKind.REPORT):
        dest = output_dir / ".antcrew" / f"{artifact.id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = artifact.content if isinstance(artifact.content, str) else json.dumps(artifact.content, indent=2)
        dest.write_text(content, encoding="utf-8")


def _print_summary(store: MemoryStore, output_dir: Path | None, written: list[Path]) -> None:
    table = Table(title="Engine Run Summary", show_header=True, header_style="bold dim")
    table.add_column("Artifact kind",  style="cyan", no_wrap=True)
    table.add_column("Count",          justify="right")

    for kind in ArtifactKind:
        items = store.list(kind)
        if items:
            table.add_row(kind.value, str(len(items)))

    console.print()
    console.print(table)

    if output_dir and written:
        console.print(f"\n[green]Wrote {len(written)} file(s) to[/] [bold]{output_dir}[/]")
        for p in written[:10]:
            console.print(f"  [dim]{p.relative_to(output_dir)}[/]")
        if len(written) > 10:
            console.print(f"  [dim]… and {len(written) - 10} more[/]")

    review = store.read(ArtifactId("review_report"))
    if review and isinstance(review.content, dict):
        verdict = review.content.get("verdict", "unknown")
        colour  = "green" if verdict == "approved" else "yellow"
        console.print(Panel(
            review.content.get("summary", ""),
            title=f"Code Review: [{colour}]{verdict.upper()}[/{colour}]",
            border_style=colour,
        ))

    test_report = store.read(ArtifactId("test_report"))
    if test_report and isinstance(test_report.content, dict):
        passed  = test_report.content.get("passed", False)
        colour  = "green" if passed else "red"
        icon    = "✓" if passed else "✗"
        output  = test_report.content.get("output", "")[-800:]
        console.print(Panel(output, title=f"[{colour}]Tests {icon}[/{colour}]", border_style=colour))


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@app.command("engine")
def engine_cmd(
    goal_description: str = typer.Argument(..., metavar="GOAL", help="Natural language goal for the project."),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    output: Path | None = typer.Option(None, "--output", "-o", help="Directory to write source/test artifacts. Skipped if not set."),
    tech: list[str] = typer.Option([], "--tech", "-t", help="Tech stack hints, e.g. --tech Python --tech FastAPI"),
    condition: list[str] = typer.Option([], "--condition", "-c", help="Override desired conditions (condition_id)."),
    full: bool = typer.Option(True, "--full/--plan-only", help="Run full pipeline (default) or stop after task planning."),
    max_iter: int = typer.Option(50, "--max-iter", help="Max Operator iterations before STUCK error."),
) -> None:
    """Run the capability-driven engine to build a software project from a goal.

    Example:

        antcrew engine "Build a REST API for a todo app" --tech Python --tech FastAPI --output ./todo-api
    """
    from antcrew.config import build_llm

    llm = build_llm(model)

    goal      = _build_goal(goal_description, tuple(tech), condition, full)
    store     = MemoryStore()
    log       = EventLog()
    registry  = _build_registry(llm)
    validators = _build_validators()
    operator  = Operator(registry, validators, log, max_iterations=max_iter)

    console.print(Panel(
        f"[bold]{goal_description}[/]\n"
        + (f"[dim]Tech: {', '.join(tech)}[/]" if tech else ""),
        title="[cyan]antcrew engine[/]",
        border_style="cyan",
    ))

    dispatched: list[str] = []

    def _on_dispatch(event) -> None:
        dispatched.append(event.capability_name)
        console.print(f"  [cyan]→[/] [bold]{event.capability_name}[/]")

    def _on_complete(event) -> None:
        status = "[green]✓[/]" if event.succeeded else "[red]✗[/]"
        console.print(f"  {status} [dim]{event.capability_name}[/] in {event.execution_time:.1f}s")

    def _on_satisfied(event) -> None:
        console.print(f"  [green]✔ condition:[/] {event.condition_id}")

    log.subscribe("capability_dispatched", _on_dispatch)
    log.subscribe("capability_completed",  _on_complete)
    log.subscribe("condition_satisfied",   _on_satisfied)

    try:
        final_state = operator.run(store, goal)
    except Exception as exc:
        console.print(f"\n[red bold]Engine error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    written: list[Path] = []
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        written = _write_output(store, output)
        _write_reports(store, output)

    _print_summary(store, output, written)
    console.print(f"\n[green bold]Done.[/] {len(final_state.satisfied)} condition(s) satisfied.")
