"""antcrew cobol-incident — end-to-end COBOL incident analysis with HITL governance.

Pipeline:
  1. Build COBOLIndex from --cobol-dir
  2. IncidentTriageExecutor  — LLM maps incident → candidate programs
  3. [HITL checkpoint 1]     — human reviews triage before full analysis
  4. ImpactAnalysisExecutor  — transitive callers, DB2 tables, risk level
  5. COBOLAnalyzerExecutor   — structural deep-dive on candidate programs
  6. [HITL checkpoint 2]     — human approves analysis before evidence export
  7. Export evidence package (JSON)

All LLM calls are recorded in TraceLog. HITL decisions are hash-bound and
auditable via ``antcrew verify``.

Examples::

    antcrew cobol-incident \\
      "Error en fecha efectiva durante rehabilitacion de polizas vencidas" \\
      --cobol-dir ./tests/fixtures/cobol/insurance

    antcrew cobol-incident "DB2 SQLCODE -803 en MOVPOL" \\
      --cobol-dir /srv/cobol/src \\
      --output-dir ./evidence \\
      --model claude
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from antcrew.cli._app import app

console = Console()

_RISK_COLOR = {"high": "red", "medium": "yellow", "low": "green"}


@app.command(name="cobol-incident")
def cobol_incident_cmd(
    incident: str = typer.Argument(
        ...,
        help="Natural-language description of the incident to analyze.",
    ),
    cobol_dir: Path = typer.Option(
        ...,
        "--cobol-dir", "-d",
        help="Directory containing COBOL source files (.cbl, .cob, .cobol).",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir", "-o",
        help="Directory to write the evidence package. Defaults to ./cobol_evidence/.",
    ),
    model: str = typer.Option(
        "claude",
        "--model", "-m",
        help="LLM model to use (e.g. claude, claude-sonnet-5, simulated).",
    ),
    auto_approve: bool = typer.Option(
        False,
        "--auto-approve",
        help="Skip HITL prompts (useful for CI demos). Records system:auto_approve in TraceLog.",
    ),
    trace_db: Optional[Path] = typer.Option(
        None,
        "--trace",
        help="TraceLog SQLite path. Defaults to ~/.antcrew/trace.db.",
    ),
    push_to: Optional[str] = typer.Option(
        None,
        "--push-to",
        help="Platform URL to upload the run (e.g. http://localhost:8080). Uploads to /runs/upload.",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="Platform API key for --push-to (or set ANTCREW_API_KEY env var).",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace", "-w",
        help="Workspace name or ID to associate the run with on the platform (requires --push-to).",
    ),
) -> None:
    """Analyze a COBOL production incident with LLM triage, impact analysis, and HITL governance.

    The full pipeline produces an evidence package that can be verified with
    ``antcrew verify``.
    """
    # ── 1. Validate inputs ─────────────────────────────────────────────────────
    if not cobol_dir.is_dir():
        console.print(f"[red]cobol-dir not found: {cobol_dir}[/red]")
        raise typer.Exit(1)

    out_dir = output_dir or Path("cobol_evidence")
    out_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    # ── 2. Build COBOLIndex ────────────────────────────────────────────────────
    console.rule("[bold cyan]AntCrew COBOL Incident Analysis[/bold cyan]")
    console.print(f"[bold]Incident:[/bold] {incident}")
    console.print(f"[bold]COBOL dir:[/bold] {cobol_dir}")
    console.print()

    from antcrew.augment.cobol.index import COBOLIndex

    idx_db = out_dir / "cobol_index.db"
    idx = COBOLIndex(str(idx_db))
    n = idx.build_from_directory(cobol_dir)

    if n == 0:
        console.print(f"[red]No COBOL files found in {cobol_dir}[/red]")
        raise typer.Exit(1)

    stats = idx.stats()
    _print_index_stats(stats, n)

    # ── 3. TraceLog setup ──────────────────────────────────────────────────────
    from antcrew.trace import TraceLog

    tlog_path = str(trace_db) if trace_db else "~/.antcrew/trace.db"
    tlog = TraceLog(tlog_path)
    run_id = tlog.begin_run(
        thread_id=f"cobol-incident-{Path(cobol_dir).name}",
        request=incident,
        team="cobol-incident",
    )
    console.print(f"[dim]TraceLog run_id: {run_id}[/dim]")

    # ── 4. LLM setup ──────────────────────────────────────────────────────────
    try:
        from antcrew.config import build_llm
        llm = build_llm(model)
    except Exception as exc:
        console.print(f"[red]Could not load model '{model}': {exc}[/red]")
        raise typer.Exit(1)

    # ── 5. HITL setup ─────────────────────────────────────────────────────────
    from antcrew.core.hitl import FlexibleHITL, HITLAction, HITLDecision

    def _terminal_callback(checkpoint: str, state: dict) -> HITLDecision:
        if checkpoint == "triage_review":
            _print_triage_panel(state)
        elif checkpoint == "analysis_review":
            _print_analysis_panel(state)
        console.print()
        answer = console.input(
            f"[bold yellow]¿Aprobar checkpoint '{checkpoint}'? [y/n]: [/bold yellow]"
        ).strip().lower()
        if answer in ("y", "yes", "s", "si", "sí"):
            return HITLDecision(action=HITLAction.APPROVE, reviewer_id="terminal")
        return HITLDecision(
            action=HITLAction.REJECT,
            reason="rejected by terminal reviewer",
            reviewer_id="terminal",
        )

    hitl = FlexibleHITL(
        callback=_terminal_callback if not auto_approve else None,
        auto_approve=auto_approve,
        trace_log=tlog,
        run_id=run_id,
    )

    # ── 6. Artifact store ─────────────────────────────────────────────────────
    from antcrew_engine.engine.store import MemoryStore
    from antcrew_engine.engine.goal import (
        Constraints, DesiredProjectState, Goal,
    )

    store = MemoryStore()
    goal = Goal(
        description=incident,
        desired_state=DesiredProjectState(conditions=frozenset()),
        constraints=Constraints(),
    )

    # ── 7. Triage ─────────────────────────────────────────────────────────────
    console.rule("[cyan]Step 1: Incident Triage[/cyan]")

    from antcrew_engine.capabilities.incident_triage import IncidentTriageExecutor

    triage_exec = IncidentTriageExecutor(llm=llm, cobol_index=idx)
    triage_result = triage_exec.execute(store, goal)

    if triage_result.errors:
        console.print(f"[red]Triage failed: {triage_result.errors}[/red]")
        raise typer.Exit(1)

    store.apply(triage_result.delta)
    from antcrew_engine.engine import ArtifactId
    triage_art = store.read(ArtifactId("triage_analysis"))
    triage = json.loads(triage_art.content) if triage_art else {}

    tlog.record_call(
        run_id=run_id,
        agent_name="incident_triage",
        duration_ms=triage_result.execution_time * 1000 if triage_result.execution_time else 0,
        prompt_snippet=incident[:200],
        response_snippet=json.dumps(triage.get("candidate_programs", []))[:200],
    )

    console.print(
        f"[green]Candidate programs:[/green] "
        f"{', '.join(triage.get('candidate_programs', []))}"
    )
    console.print(f"[green]Risk area:[/green] {triage.get('risk_area', 'unknown')}")

    # ── 8. HITL checkpoint 1 ──────────────────────────────────────────────────
    hitl_state: dict = {"triage": triage, "incident": incident}
    if not hitl.gate("triage_review", hitl_state):
        console.print("[yellow]Triage rejected. Analysis aborted.[/yellow]")
        tlog.end_run(run_id, status="rejected")
        raise typer.Exit(0)

    # ── 9. Impact analysis ────────────────────────────────────────────────────
    console.rule("[cyan]Step 2: Impact Analysis[/cyan]")

    from antcrew_engine.capabilities.impact_analysis import ImpactAnalysisExecutor

    impact_exec = ImpactAnalysisExecutor(cobol_index=idx)
    impact_result = impact_exec.execute(store, goal)

    if impact_result.errors:
        console.print(f"[red]Impact analysis failed: {impact_result.errors}[/red]")
        raise typer.Exit(1)

    store.apply(impact_result.delta)
    impact_art = store.read(ArtifactId("impact_analysis"))
    impact = json.loads(impact_art.content) if impact_art else {}

    risk = impact.get("risk_level", "unknown")
    risk_color = _RISK_COLOR.get(risk, "white")
    console.print(f"Risk level: [{risk_color}]{risk.upper()}[/{risk_color}] — {impact.get('risk_reason', '')}")

    if impact.get("affected_callers"):
        # filter out internal doc_ids (e.g. COBOL_SOURCE__X) that leak from DocumentationManager
        callers = [c for c in impact["affected_callers"] if "__" not in c]
        if callers:
            console.print(f"Transitive callers: {', '.join(callers)}")
    if impact.get("db2_tables_touched"):
        tables = [f"{t['table']}({t['op']})" for t in impact["db2_tables_touched"]]
        console.print(f"DB2 tables: {', '.join(tables)}")

    # ── 10. Deep COBOL analysis ───────────────────────────────────────────────
    console.rule("[cyan]Step 3: Structural COBOL Analysis[/cyan]")

    from antcrew_engine.capabilities.cobol_analyzer import COBOLAnalyzerExecutor
    from antcrew_engine.documentation import DocumentationManager

    doc_mgr = DocumentationManager()
    for prog_name in triage.get("candidate_programs", []):
        cbl_files = list(cobol_dir.rglob(f"{prog_name}.*"))
        for f in cbl_files:
            if f.suffix.lower() in (".cbl", ".cob", ".cobol", ".cpy"):
                doc_type = "copybook" if f.suffix.lower() == ".cpy" else "cobol_source"
                doc_mgr.upload_document(str(f), doc_type)

    analyzer = COBOLAnalyzerExecutor(llm=llm)
    analyzer.set_documentation(doc_mgr)
    analysis_result = analyzer.execute(store, goal)

    if analysis_result.errors:
        console.print(f"[yellow]COBOL analysis warnings: {analysis_result.errors}[/yellow]")

    store.apply(analysis_result.delta)
    analysis_art = store.read(ArtifactId("cobol_analysis"))
    analysis = json.loads(analysis_art.content) if analysis_art else {}

    tlog.record_call(
        run_id=run_id,
        agent_name="cobol_analyzer",
        duration_ms=analysis_result.execution_time * 1000 if analysis_result.execution_time else 0,
        prompt_snippet=f"analyzing {len(triage.get('candidate_programs', []))} programs",
        response_snippet=analysis.get("summary", "")[:200],
    )

    if analysis.get("summary"):
        console.print(Panel(analysis["summary"], title="Analysis Summary", border_style="cyan"))

    # ── 11. HITL checkpoint 2 ─────────────────────────────────────────────────
    hitl_state["impact"] = impact
    hitl_state["analysis"] = analysis
    if not hitl.gate("analysis_review", hitl_state):
        console.print("[yellow]Analysis rejected. Evidence not exported.[/yellow]")
        tlog.end_run(run_id, status="rejected")
        raise typer.Exit(0)

    # ── 12. Export evidence package ───────────────────────────────────────────
    console.rule("[cyan]Step 4: Evidence Export[/cyan]")

    evidence = {
        "run_id":    run_id,
        "incident":  incident,
        "cobol_dir": str(cobol_dir),
        "index_stats": stats,
        "triage_analysis":  triage,
        "impact_analysis":  impact,
        "cobol_analysis":   analysis,
        "hitl_decisions":   tlog.get_hitl_decisions(run_id),
        "trace_calls":      tlog.get_calls(run_id),
    }

    evidence_path = out_dir / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    tlog.end_run(run_id, status="done")
    duration_s = time.time() - start_time

    console.print(f"[green]Evidence package written:[/green] {evidence_path}")
    console.print(
        f"[dim]Verify with:[/dim] antcrew verify {run_id}"
        + (f" --trace {trace_db}" if trace_db else "")
    )

    # ── 13. Push to platform ──────────────────────────────────────────────────
    if push_to:
        _push_to_platform(
            push_to=push_to.rstrip("/"),
            api_key=api_key or os.environ.get("ANTCREW_API_KEY", ""),
            incident=incident,
            run_id=run_id,
            risk=risk,
            triage=triage,
            impact=impact,
            analysis=analysis,
            hitl_decisions=evidence["hitl_decisions"],
            duration_s=duration_s,
            workspace=workspace,
        )

    console.print()
    console.print(
        f"[bold green]Done.[/bold green] "
        f"Risk: [{_RISK_COLOR.get(risk, 'white')}]{risk.upper()}[/{_RISK_COLOR.get(risk, 'white')}] | "
        f"Programs: {', '.join(triage.get('candidate_programs', []))} | "
        f"HITL decisions: {len(evidence['hitl_decisions'])}"
    )


# ── Platform push ─────────────────────────────────────────────────────────────

def _push_to_platform(
    *,
    push_to: str,
    api_key: str,
    incident: str,
    run_id: str,
    risk: str,
    triage: dict,
    impact: dict,
    analysis: dict,
    hitl_decisions: list,
    duration_s: float,
    workspace: Optional[str] = None,
) -> None:
    """POST the evidence to the platform's /runs/upload endpoint."""
    import urllib.request

    url = f"{push_to}/runs/upload"
    payload = {
        "team": "cobol-incident",
        "request": incident,
        "thread_id": run_id,
        "cost_usd": 0.0,
        "duration_s": round(duration_s, 2),
        "state": {
            "risk_level": risk,
            "candidate_programs": triage.get("candidate_programs", []),
            "risk_area": triage.get("risk_area", ""),
            "affected_callers": impact.get("affected_callers", []),
            "db2_tables_touched": impact.get("db2_tables_touched", []),
            "analysis_summary": analysis.get("summary", ""),
            "root_cause_lines": analysis.get("root_cause_lines", []),
            "hitl_decisions": hitl_decisions,
        },
    }
    if workspace:
        payload["workspace"] = workspace
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            **({"X-Api-Key": api_key} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            run_url = f"{push_to}/run/{result['run_id']}"
            console.print(f"[green]Pushed to platform:[/green] {run_url}")
    except Exception as exc:
        console.print(f"[yellow]Push to platform failed:[/yellow] {exc}")


# ── Rich helpers ──────────────────────────────────────────────────────────────

def _print_index_stats(stats: dict, n: int) -> None:
    t = Table(title=f"COBOL Index ({n} files)", show_header=True, header_style="bold")
    t.add_column("Programs")
    t.add_column("CALL edges")
    t.add_column("COPY edges")
    t.add_column("DB2 tables")
    t.add_row(
        str(stats.get("programs", 0)),
        str(stats.get("call_edges", 0)),
        str(stats.get("copy_edges", 0)),
        str(stats.get("db2_tables", 0)),
    )
    console.print(t)


def _print_triage_panel(state: dict) -> None:
    triage = state.get("triage", {})
    candidates = triage.get("candidate_programs", [])
    reasoning  = triage.get("reasoning", "")
    risk_area  = triage.get("risk_area", "")
    body = (
        f"[bold]Candidate programs:[/bold] {', '.join(candidates)}\n"
        f"[bold]Risk area:[/bold] {risk_area}\n\n"
        f"{reasoning}"
    )
    console.print(Panel(body, title="[cyan]Triage Result[/cyan]", border_style="cyan"))


def _print_analysis_panel(state: dict) -> None:
    impact = state.get("impact", {})
    analysis = state.get("analysis", {})
    risk = impact.get("risk_level", "unknown")
    color = _RISK_COLOR.get(risk, "white")
    tables = [f"{t['table']}({t['op']})" for t in impact.get("db2_tables_touched", [])]
    callers = impact.get("affected_callers", [])
    summary = analysis.get("summary", "(no structural analysis)")
    body = (
        f"[bold]Risk:[/bold] [{color}]{risk.upper()}[/{color}] — {impact.get('risk_reason', '')}\n"
        + (f"[bold]Callers affected:[/bold] {', '.join(callers)}\n" if callers else "")
        + (f"[bold]DB2 tables:[/bold] {', '.join(tables)}\n" if tables else "")
        + f"\n[bold]Structural summary:[/bold]\n{summary}"
    )
    console.print(Panel(body, title="[cyan]Impact + Analysis[/cyan]", border_style="cyan"))
