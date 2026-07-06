"""antcrew engine -- capability-driven project builder.

Usage:
    antcrew engine "Build a REST API" --tech Python --output ./my-project
    antcrew engine --resume --output ./my-project          # continue from last run
    antcrew engine-status ./my-project                     # inspect a store
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from antcrew.cli._app import app, console, _MODEL_HELP
from antcrew.engine import (
    ArtifactId, ArtifactKind,
    CapabilityRegistry, Condition, ConditionId, Constraints,
    DesiredProjectState, EventLog, FilesystemStore, Goal, MemoryStore, Operator,
)
from antcrew.capabilities import (
    Architect, CodeGenerator, CodeReviewer,
    SpecExtractor, TaskPlanner, TestGenerator, TestRunner,
)
from antcrew.capabilities.validators import (
    AllTasksCompletedValidator, CodeReviewedValidator,
    TestsExistValidator, TestsPassValidator, artifact_validators,
)

_GOAL_META_REL = Path(".antcrew") / "goal.json"


# ---------------------------------------------------------------------------
# Registry / validators
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


# ---------------------------------------------------------------------------
# Goal construction
# ---------------------------------------------------------------------------

def _build_goal(
    description: str,
    tech_stack:  tuple[str, ...],
    conditions:  list[str],
    full:        bool,
) -> Goal:
    default_conditions = [
        ("requirements_exists",   "requirements document written"),
        ("architecture_exists",   "architecture designed"),
        ("task_graph_exists",     "tasks planned"),
        ("implementation_exists", "all tasks implemented"),
        ("tests_exist",           "test suite written"),
        ("tests_pass",            "tests passing"),
        ("code_reviewed",         "code reviewed and approved"),
    ] if full else [
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


# ---------------------------------------------------------------------------
# Goal metadata persistence
# ---------------------------------------------------------------------------

def _save_goal_meta(output: Path, description: str, tech: list[str],
                    conditions: list[str], full: bool) -> None:
    meta = {
        "description": description,
        "tech":        tech,
        "conditions":  conditions,
        "full":        full,
    }
    meta_path = output / _GOAL_META_REL
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _load_goal_meta(output: Path) -> dict | None:
    meta_path = output / _GOAL_META_REL
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Output helpers (MemoryStore fallback)
# ---------------------------------------------------------------------------

def _write_output(store: MemoryStore, output_dir: Path) -> list[Path]:
    written: list[Path] = []
    for kind in (ArtifactKind.SOURCE, ArtifactKind.TEST,
                 ArtifactKind.DOCUMENTATION, ArtifactKind.CONFIG):
        for artifact in store.list(kind):
            file_path = artifact.metadata.get("file_path") or str(artifact.id)
            dest = output_dir / file_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = (artifact.content if isinstance(artifact.content, str)
                       else json.dumps(artifact.content, indent=2))
            dest.write_text(content, encoding="utf-8")
            written.append(dest)
    return written


def _write_reports(store: MemoryStore, output_dir: Path) -> None:
    for artifact in store.list(ArtifactKind.REPORT):
        dest = output_dir / ".antcrew" / f"{artifact.id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = (artifact.content if isinstance(artifact.content, str)
                   else json.dumps(artifact.content, indent=2))
        dest.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Rich display helpers
# ---------------------------------------------------------------------------

def _print_summary(store, output_dir: Path | None, written: list[Path]) -> None:
    table = Table(title="Engine Run Summary", show_header=True, header_style="bold dim")
    table.add_column("Artifact kind", style="cyan", no_wrap=True)
    table.add_column("Count",         justify="right")

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
            console.print(f"  [dim]... and {len(written) - 10} more[/]")

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
        passed = test_report.content.get("passed", False)
        colour = "green" if passed else "red"
        icon   = "[checkmark]" if passed else "x"
        output = test_report.content.get("output", "")[-800:]
        console.print(Panel(output,
                            title=f"[{colour}]Tests {'passed' if passed else 'failed'}[/{colour}]",
                            border_style=colour))


# ---------------------------------------------------------------------------
# antcrew engine
# ---------------------------------------------------------------------------

@app.command("engine")
def engine_cmd(
    goal_description: Optional[str] = typer.Argument(
        None, metavar="GOAL",
        help="Natural language goal. Optional when --resume is set.",
    ),
    model:     str  = typer.Option("claude",  "--model", "-m",           help=_MODEL_HELP),
    output:    Optional[Path] = typer.Option(None, "--output", "-o",
                                             help="Directory for artifacts (enables FilesystemStore)."),
    tech:      list[str] = typer.Option([], "--tech", "-t",
                                        help="Tech stack hints, e.g. --tech Python --tech FastAPI"),
    condition: list[str] = typer.Option([], "--condition", "-c",
                                        help="Override desired conditions (condition_id)."),
    full:      bool = typer.Option(True,  "--full/--plan-only",
                                   help="Full pipeline (default) or stop after planning."),
    max_iter:  int  = typer.Option(50,    "--max-iter",
                                   help="Max Operator iterations before STUCK error."),
    resume:    bool = typer.Option(False, "--resume/--no-resume",
                                   help="Resume from an existing --output directory."),
) -> None:
    """Run the capability-driven engine to build a software project from a goal.

    First run:

        antcrew engine "Build a REST API" --tech Python --tech FastAPI --output ./my-api

    Resume (pick up where it left off):

        antcrew engine --resume --output ./my-api

    Inspect what is already built:

        antcrew engine-status ./my-api
    """
    from antcrew.config import build_llm

    # ---- resolve goal -------------------------------------------------------
    if resume and output is not None:
        meta = _load_goal_meta(output)
        if goal_description is None:
            if meta is None:
                console.print("[red]--resume: no goal.json found in the output directory.[/]")
                raise typer.Exit(code=1)
            goal_description = meta["description"]
            tech      = tech      or meta.get("tech",       [])
            condition = condition or meta.get("conditions", [])
            full      = meta.get("full", full)
            console.print(f"[dim]Resuming:[/] {goal_description}")
        else:
            console.print("[dim]--resume with a new goal: running from existing store state.[/]")
    elif goal_description is None:
        console.print("[red]Provide a GOAL argument or use --resume with a prior --output dir.[/]")
        raise typer.Exit(code=1)

    # ---- build ---------------------------------------------------------------
    llm        = build_llm(model)
    goal       = _build_goal(goal_description, tuple(tech), condition, full)
    store      = FilesystemStore(output) if output is not None else MemoryStore()
    log        = EventLog()
    registry   = _build_registry(llm)
    validators = _build_validators()
    operator   = Operator(registry, validators, log, max_iterations=max_iter)

    # ---- display header ------------------------------------------------------
    resume_note = " [dim](resuming)[/dim]" if resume else ""
    console.print(Panel(
        f"[bold]{goal_description}[/]{resume_note}\n"
        + (f"[dim]Tech: {', '.join(tech)}[/]" if tech else ""),
        title="[cyan]antcrew engine[/]",
        border_style="cyan",
    ))

    # ---- event hooks ---------------------------------------------------------
    def _on_dispatch(event) -> None:
        console.print(f"  [cyan]>[/] [bold]{event.capability_name}[/]")

    def _on_complete(event) -> None:
        ok     = event.result is None or event.result.succeeded
        status = "[green]ok[/]" if ok else "[red]fail[/]"
        t      = event.result.execution_time if event.result else 0.0
        console.print(f"  {status} [dim]{event.capability_name}[/] ({t:.1f}s)")

    def _on_satisfied(event) -> None:
        console.print(f"  [green]condition:[/] {event.condition_id}")

    log.subscribe("capability_dispatched", _on_dispatch)
    log.subscribe("capability_completed",  _on_complete)
    log.subscribe("condition_satisfied",   _on_satisfied)

    # ---- run -----------------------------------------------------------------
    try:
        final_state = operator.run(store, goal)
    except Exception as exc:
        console.print(f"\n[red bold]Engine error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    # ---- persist goal meta so --resume works next time ----------------------
    if output is not None:
        _save_goal_meta(output, goal_description, list(tech), list(condition), full)

    # ---- post-run: flush MemoryStore to disk if needed ----------------------
    written: list[Path] = []
    if output is not None and isinstance(store, MemoryStore):
        output.mkdir(parents=True, exist_ok=True)
        written = _write_output(store, output)
        _write_reports(store, output)
    elif output is not None:
        written = [
            output / (a.metadata.get("file_path") or str(a.id))
            for kind in (ArtifactKind.SOURCE, ArtifactKind.TEST)
            for a in store.list(kind)
        ]

    _print_summary(store, output, written)
    console.print(f"\n[green bold]Done.[/] {len(final_state.satisfied)} condition(s) satisfied.")


# ---------------------------------------------------------------------------
# antcrew engine-status
# ---------------------------------------------------------------------------

@app.command("engine-status")
def engine_status_cmd(
    project_dir: Path = typer.Argument(..., metavar="DIR",
                                       help="Directory previously built with antcrew engine --output."),
) -> None:
    """Inspect the state of a project built by the engine.

    Shows artifacts in the store, satisfied conditions, and review/test results.

        antcrew engine-status ./my-api
    """
    if not project_dir.exists():
        console.print(f"[red]Directory not found:[/] {project_dir}")
        raise typer.Exit(code=1)

    store = FilesystemStore(project_dir)
    manifest_path = project_dir / ".antcrew" / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[yellow]No engine manifest found in {project_dir}[/]")
        console.print("[dim]Run 'antcrew engine ... --output <dir>' first.[/]")
        raise typer.Exit(code=1)

    # ---- goal meta ----------------------------------------------------------
    meta = _load_goal_meta(project_dir)
    if meta:
        console.print(Panel(
            f"[bold]{meta['description']}[/]\n"
            + (f"[dim]Tech: {', '.join(meta.get('tech', []))}[/]"
               if meta.get("tech") else ""),
            title="[cyan]Goal[/]",
            border_style="cyan",
        ))

    # ---- artifact inventory -------------------------------------------------
    art_table = Table(title="Artifacts", show_header=True, header_style="bold dim")
    art_table.add_column("Kind",     style="cyan",  no_wrap=True)
    art_table.add_column("ID",       style="white")
    art_table.add_column("Size",     justify="right", style="dim")

    total = 0
    for kind in ArtifactKind:
        artifacts = store.list(kind)
        for art in artifacts:
            content = art.content
            size = (f"{len(content)} chars" if isinstance(content, str)
                    else f"{len(json.dumps(content))} chars")
            art_table.add_row(kind.value, str(art.id), size)
            total += 1

    if total == 0:
        console.print("[yellow]Store is empty.[/]")
        raise typer.Exit()

    console.print()
    console.print(art_table)

    # ---- condition status ---------------------------------------------------
    validators = _build_validators()
    cond_table = Table(title="Conditions", show_header=True, header_style="bold dim")
    cond_table.add_column("Condition",  style="white",  no_wrap=True)
    cond_table.add_column("Status",     justify="center")
    cond_table.add_column("Details",    style="dim")

    for v in validators:
        result = v.validate(store)
        icon   = "[green]PASS[/]" if result.satisfied else "[red]FAIL[/]"
        detail = ", ".join(f"{k}={val}" for k, val in (result.observations or {}).items())
        cond_table.add_row(str(result.condition_id), icon, detail[:60])

    console.print()
    console.print(cond_table)

    # ---- review panel -------------------------------------------------------
    review = store.read(ArtifactId("review_report"))
    if review and isinstance(review.content, dict):
        verdict = review.content.get("verdict", "unknown")
        colour  = "green" if verdict == "approved" else "yellow"
        findings = review.content.get("findings", [])
        body = review.content.get("summary", "")
        if findings:
            body += f"\n\n[dim]{len(findings)} finding(s)[/dim]"
        console.print(Panel(body,
                            title=f"Code Review: [{colour}]{verdict.upper()}[/{colour}]",
                            border_style=colour))

    # ---- test report --------------------------------------------------------
    test_report = store.read(ArtifactId("test_report"))
    if test_report and isinstance(test_report.content, dict):
        passed = test_report.content.get("passed", False)
        colour = "green" if passed else "red"
        label  = "Tests passed" if passed else "Tests failed"
        output = test_report.content.get("output", "")[-600:]
        console.print(Panel(output, title=f"[{colour}]{label}[/{colour}]",
                            border_style=colour))

    console.print(f"\n[dim]{total} artifact(s) in {project_dir}[/]")
