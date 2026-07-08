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
    Artifact, ArtifactId, ArtifactKind,
    CapabilityRegistry, Condition, ConditionId, Constraints,
    DesiredProjectState, EventLog, FilesystemStore, Goal, MemoryStore, Operator,
)
from antcrew.capabilities import (
    Architect, BugFixer, CodeGenerator, CodeRegenerator, CodeReviewer,
    DependencyInstaller, DocGenerator, ReviewFixer, TaskPlanner, TestGenerator, TestRunner,
)
from antcrew.capabilities.validators import (
    AllTasksCompletedValidator, CodeReviewedValidator, DependenciesInstalledValidator,
    DocumentationExistsValidator, TestsExistValidator, TestsPassValidator, artifact_validators,
)

_GOAL_META_REL = Path(".antcrew") / "goal.json"


# ---------------------------------------------------------------------------
# Registry / validators
# ---------------------------------------------------------------------------

def _load_capability_models(config_file: "Optional[Path]") -> "dict[str, str]":
    """Parse the ``capabilities:`` block from an engine YAML config file.

    Returns a mapping of capability_name → model_string.
    Returns an empty dict when no config file is given or when the file has
    no ``capabilities:`` block.

    Example YAML::

        model: claude-sonnet-4-6          # default (can also be set via --model)
        capabilities:
          spec_extractor:
            model: claude-haiku-4-5-20251001
          bug_fixer:
            model: claude-opus-4-8
          code_regenerator:
            model: claude-opus-4-8
    """
    if config_file is None:
        return {}
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except ImportError:
        import json as _json
        raw = _json.loads(config_file.read_text(encoding="utf-8"))
    caps = raw.get("capabilities", {}) if isinstance(raw, dict) else {}
    return {
        name: str(cfg["model"])
        for name, cfg in caps.items()
        if isinstance(cfg, dict) and "model" in cfg
    }


def _build_registry(
    llm,
    *,
    capability_models: "dict[str, str] | None" = None,
    max_tasks: int = 12,
) -> CapabilityRegistry:
    """Build the capability registry.

    *llm* is the default model for all capabilities.
    *capability_models* is an optional mapping of capability_name → model_string
    that overrides the default for specific capabilities. Any capability not listed
    uses *llm*. Model strings follow the same syntax as --model / build_llm():
    'claude', 'claude-haiku-4-5-20251001', 'gpt-4o', 'ollama:llama3', etc.

    Example via YAML (loaded with --config):
        capabilities:
          spec_extractor:
            model: claude-haiku-4-5-20251001
          bug_fixer:
            model: claude-opus-4-8
    """
    from antcrew.config import build_llm as _build_llm

    # Pre-resolve per-capability LLMs once (avoids repeated build_llm calls).
    _overrides: dict[str, object] = {
        name: _build_llm(model_str)
        for name, model_str in (capability_models or {}).items()
    }

    def _llm(cap_name: str):
        return _overrides.get(cap_name, llm)

    registry = CapabilityRegistry()
    registry.register(Architect(llm=_llm("architect")))
    registry.register(TaskPlanner(llm=_llm("task_planner"), max_tasks=max_tasks))
    registry.register(CodeGenerator(llm=_llm("code_generator")))
    registry.register(DependencyInstaller(llm=_llm("dependency_installer")))
    registry.register(TestGenerator(llm=_llm("test_generator")))
    registry.register(TestRunner())
    registry.register(BugFixer(llm=_llm("bug_fixer")))
    registry.register(CodeRegenerator(llm=_llm("code_regenerator")))
    registry.register(CodeReviewer(llm=_llm("code_reviewer")))
    registry.register(ReviewFixer(llm=_llm("review_fixer")))
    registry.register(DocGenerator(llm=_llm("doc_generator")))
    return registry


def _load_existing_codebase(store, source_dir: Path, goal_description: str) -> int:
    """Seed the store with .py files from *source_dir* + stub planning artifacts."""
    _SKIP = frozenset([".antcrew", "__pycache__", "venv", ".venv", ".git", "node_modules"])

    loaded = 0
    for py_file in sorted(source_dir.rglob("*.py")):
        try:
            rel = py_file.relative_to(source_dir)
        except ValueError:
            continue
        if any(part in _SKIP for part in rel.parts):
            continue
        rel_str = str(rel).replace("\\", "/")
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        store.write(Artifact(
            id       = ArtifactId(f"src/{rel_str}"),
            kind     = ArtifactKind.SOURCE,
            content  = content,
            metadata = {"file_path": rel_str, "source": "from_dir"},
        ))
        loaded += 1

    store.write(Artifact(
        id       = ArtifactId("requirements"),
        kind     = ArtifactKind.REQUIREMENTS,
        content  = (
            f"# Requirements\n\nExisting project loaded from `{source_dir}`.\n\n"
            f"Goal: {goal_description}"
        ),
        metadata = {"file_path": ".antcrew/requirements.md"},
    ))
    store.write(Artifact(
        id       = ArtifactId("architecture"),
        kind     = ArtifactKind.ARCHITECTURE,
        content  = (
            f"# Architecture\n\nExisting codebase — see source files loaded from `{source_dir}`."
        ),
        metadata = {"file_path": ".antcrew/architecture.md"},
    ))
    store.write(Artifact(
        id       = ArtifactId("task_graph"),
        kind     = ArtifactKind.TASK_GRAPH,
        content  = {
            "tasks": [{
                "id":          "existing_implementation",
                "description": goal_description,
                "status":      "done",
            }],
        },
        metadata = {"file_path": ".antcrew/task_graph.json"},
    ))
    return loaded


def _wire_hitl(registry: CapabilityRegistry, llm, hitl_after: list[str],
               validators: list, event_log,
               hitl_max_rejections: int = 5) -> dict[str, int]:
    """Register HitlReviewer capabilities and patch downstream needs."""
    import dataclasses
    from antcrew.capabilities import HitlReviewer

    def _make_console_callback(cap_name: str):
        def request_review(content) -> dict:
            console.print(
                f"\n[yellow bold]HITL review required:[/] [cyan]{cap_name}[/]\n"
                f"[dim]The {cap_name} artifact is ready for review.[/]\n"
                f"[dim]Artifact content:[/]\n{str(content)[:1000]}\n"
            )
            decision = typer.prompt(
                "Decision",
                default="approve",
                prompt_suffix=" (approve/reject): ",
            ).strip().lower()
            feedback = ""
            if decision != "approve":
                feedback = typer.prompt("Feedback for the re-run", default="").strip()
            return {"verdict": "approve" if decision == "approve" else "reject",
                    "feedback": feedback}
        return request_review

    hitl_total_limits: dict[str, int] = {}
    for cap_name in hitl_after:
        # Detect the actual artifact ID and triggering condition from the registry.
        # Upstream capability may write under a different name than cap_name
        # (e.g. Architect writes ArtifactId("architecture") and produces
        # ConditionId("architecture_exists"), not "architect" / "architect_exists").
        source_exec = next((e for e in registry.all() if e.descriptor.name == cap_name), None)
        triggers_condition: str | None = None
        artifact_id:        str | None = None
        if source_exec:
            exists_conds = [
                str(c) for c in source_exec.descriptor.produces
                if str(c).endswith("_exists")
            ]
            if exists_conds:
                triggers_condition = exists_conds[0]
                artifact_id        = triggers_condition[: -len("_exists")]

        callback = _make_console_callback(cap_name)
        reviewer = HitlReviewer(
            reviewed_capability=cap_name,
            request_review=callback,
            artifact_id=artifact_id,
            triggers_condition=triggers_condition,
        )
        registry.register(reviewer)
        validators += artifact_validators((f"{cap_name}_approval", f"{cap_name}_approved"))
        hitl_total_limits[f"hitl_{cap_name}"] = hitl_max_rejections

        old_cond  = ConditionId(triggers_condition or f"{cap_name}_exists")
        new_cond  = ConditionId(f"{cap_name}_approved")
        hitl_name = f"hitl_{cap_name}"
        for executor in registry.all():
            if executor.descriptor.name == hitl_name:
                continue
            if old_cond in executor.descriptor.needs:
                executor.descriptor = dataclasses.replace(
                    executor.descriptor,
                    needs=executor.descriptor.needs - frozenset([old_cond]) | frozenset([new_cond]),
                )
    return hitl_total_limits


def _build_validators() -> list:
    return [
        *artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
            ("task_graph",   "task_graph_exists"),
        ),
        AllTasksCompletedValidator(),
        DependenciesInstalledValidator(),
        TestsExistValidator(),
        TestsPassValidator(),
        CodeReviewedValidator(),
        DocumentationExistsValidator(),
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
        ("requirements_exists",    "requirements document written"),
        ("architecture_exists",    "architecture designed"),
        ("task_graph_exists",      "tasks planned"),
        ("implementation_exists",  "all tasks implemented"),
        ("dependencies_installed", "project dependencies installed"),
        ("tests_exist",            "test suite written"),
        ("tests_pass",             "tests passing"),
        ("code_reviewed",          "code reviewed and approved"),
        ("documentation_exists",   "README.md written"),
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
    model:       str           = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    config_file: Optional[Path] = typer.Option(None,    "--config",
                                               help="YAML config with per-capability model overrides. "
                                                    "See 'capabilities:' block in docs."),
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
    resume:      bool = typer.Option(False, "--resume/--no-resume",
                                     help="Resume from an existing --output directory."),
    fix_attempts: int = typer.Option(3, "--fix-attempts",
                                     help="Max BugFixer attempts before giving up on failing tests."),
    from_dir:    Optional[Path] = typer.Option(None, "--from-dir",
                                               help="Load existing .py files as source artifacts (task mode)."),
    hitl_after:  list[str] = typer.Option([], "--hitl-after",
                                          help="Gate on human review after a capability (e.g. --hitl-after architect)."),
    hitl_max_rejections: int = typer.Option(5, "--hitl-max-rejections",
                                            help="Max HITL rejections before the engine gives up."),
    max_cost: Optional[float] = typer.Option(None, "--max-cost",
                                             help="Hard USD budget limit. Engine stops if exceeded."),
    max_tasks: int = typer.Option(12, "--max-tasks",
                                  help="Max tasks TaskPlanner may generate (default 12)."),
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

    # ---- validate from_dir --------------------------------------------------
    if from_dir is not None and not from_dir.is_dir():
        console.print(f"[red]--from-dir: not a directory: {from_dir}[/]")
        raise typer.Exit(code=1)

    # ---- build ---------------------------------------------------------------
    llm              = build_llm(model)
    capability_models = _load_capability_models(config_file)
    goal       = _build_goal(goal_description, tuple(tech), condition, full)
    store      = FilesystemStore(output) if output is not None else MemoryStore()
    log        = EventLog()
    registry   = _build_registry(llm, capability_models=capability_models, max_tasks=max_tasks)
    validators = _build_validators()

    # Task mode: seed store from existing codebase
    if from_dir is not None:
        n = _load_existing_codebase(store, from_dir, goal_description)
        console.print(f"[dim]Loaded {n} source file(s) from {from_dir}[/]")

    # HITL: wire reviewers (interactive console prompts)
    hitl_total_limits: dict[str, int] = {}
    if hitl_after:
        hitl_total_limits = _wire_hitl(
            registry, llm, list(hitl_after), validators, log, hitl_max_rejections,
        )

    operator   = Operator(
        registry, validators, log,
        max_iterations=max_iter,
        retry_limits={"test_runner": 1, "bug_fixer": fix_attempts, "code_reviewer": 2},
        total_limits={"code_regenerator": 2, "review_fixer": 3, **hitl_total_limits},
        max_cost_usd=max_cost,
    )

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

    _run_cost = {"total": 0.0}

    def _on_complete(event) -> None:
        ok     = event.result is None or event.result.succeeded
        status = "[green]ok[/]" if ok else "[red]fail[/]"
        t      = event.result.execution_time if event.result else 0.0
        c      = event.result.cost_usd if event.result else 0.0
        _run_cost["total"] += c
        cost_str = f" [dim]${c:.4f} ∑${_run_cost['total']:.4f}[/]" if c else ""
        console.print(f"  {status} [dim]{event.capability_name}[/] ({t:.1f}s){cost_str}")

    def _on_satisfied(event) -> None:
        console.print(f"  [green]condition:[/] {event.condition_id}")

    def _on_progress(event) -> None:
        console.print("·", end="", highlight=False)

    log.subscribe("capability_dispatched", _on_dispatch)
    log.subscribe("capability_completed",  _on_complete)
    log.subscribe("condition_satisfied",   _on_satisfied)
    log.subscribe("capability_progress",   _on_progress)

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
