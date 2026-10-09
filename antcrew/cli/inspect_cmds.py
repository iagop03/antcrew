"""Show, extract, describe, agents, and inspect commands."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import _TEAM_CHOICES, app, console
from antcrew.cli._shared import _print_state_raw

_DEFAULT_TRACE_DB = Path.home() / ".antcrew" / "trace.db"


@app.command(name="inspect")
def inspect_cmd(
    run_id: str = typer.Argument(..., help="Run ID to inspect (prefix match supported)"),
    trace: Path = typer.Option(
        _DEFAULT_TRACE_DB,
        "--trace", "-t",
        help="TraceLog SQLite file (default: ~/.antcrew/trace.db)",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output raw JSON evidence package"),
) -> None:
    """Show the evidence summary for a governed execution.

    \b
    Examples:
        antcrew inspect ac_20261007_a3f2
        antcrew inspect ac_20261007   --trace ./my.db
        antcrew inspect ac_20261007_a3f2 --json
    """
    from rich.table import Table

    from antcrew.evidence import EvidencePackage
    from antcrew.trace import TraceLog

    trace_path = Path(str(trace).replace("~", str(Path.home())))
    if not trace_path.exists():
        console.print(
            f"[red]TraceLog not found:[/] {trace_path}\n"
            "[dim]Run with [bold]--trace <file.db>[/] or record a run first "
            "using [bold]--trace ~/.antcrew/trace.db[/bold][/dim]"
        )
        raise typer.Exit(1)

    tlog = TraceLog(str(trace_path))

    # Prefix matching: find the first run whose id starts with run_id
    run = tlog.get_run(run_id)
    if run is None:
        all_runs = tlog.list_runs(limit=200)
        candidates = [r for r in all_runs if r["id"].startswith(run_id)]
        if not candidates:
            console.print(f"[red]Run not found:[/] {run_id!r}")
            tlog.close()
            raise typer.Exit(1)
        run = candidates[0]
        run_id = run["id"]

    pkg = EvidencePackage.from_trace(tlog, run_id)
    tlog.close()

    if output_json:
        typer.echo(pkg.to_json())
        return

    # ── Evidence header ──────────────────────────────────────────────────────
    from rich.panel import Panel

    status_color = "green" if pkg.status == "done" else ("red" if pkg.status == "error" else "yellow")
    chain_icon = {"intact": "✓", "empty": "○", "broken": "✗", "unverifiable": "?"}.get(pkg.chain_status, "?")
    chain_color = {"intact": "green", "empty": "dim", "broken": "red", "unverifiable": "yellow"}.get(pkg.chain_status, "dim")

    cost_str     = f"${pkg.cost_usd:.4f}" if pkg.cost_usd else "—"
    duration_str = (
        f"{pkg.duration_seconds:.0f}s"
        if pkg.duration_seconds >= 1
        else f"{pkg.duration_seconds * 1000:.0f}ms"
    ) if pkg.duration_seconds else "—"

    console.print(Panel(
        f"[bold]{pkg.request_preview or '(no request recorded)'}[/bold]\n\n"
        f"  Run ID:   [cyan]{pkg.run_id}[/cyan]\n"
        f"  Team:     [yellow]{pkg.team or '—'}[/yellow]\n"
        f"  Status:   [{status_color}]{pkg.status}[/{status_color}]\n"
        f"  Cost:     [cyan]{cost_str}[/cyan]   Duration: [dim]{duration_str}[/dim]\n"
        f"  HITL:     {pkg.hitl_count} decision(s), {pkg.approved_count} approved\n"
        f"  Chain:    [{chain_color}]{chain_icon} {pkg.chain_status}[/{chain_color}]"
        + (f" — {pkg.chain_message}" if pkg.chain_message and pkg.chain_status != "intact" else ""),
        title="[bold]Evidence Package[/bold]",
        border_style="blue",
    ))

    # ── Agents table ─────────────────────────────────────────────────────────
    if pkg.agents:
        tbl = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
        tbl.add_column("Agent",    style="cyan", no_wrap=True)
        tbl.add_column("Model",    style="dim")
        tbl.add_column("Tokens",   justify="right")
        tbl.add_column("Cost",     justify="right")
        tbl.add_column("Duration", justify="right")
        for a in pkg.agents:
            tok_str  = f"{a.input_tokens}↑ {a.output_tokens}↓"
            cost_str = f"${a.cost_usd:.4f}" if a.cost_usd else "—"
            dur_str  = f"{a.duration_ms/1000:.1f}s" if a.duration_ms >= 1000 else f"{a.duration_ms:.0f}ms"
            tbl.add_row(a.agent_name, a.model_id or "—", tok_str, cost_str, dur_str)
        console.print("\n[bold dim]AGENTS[/bold dim]")
        console.print(tbl)

    # ── HITL decisions table ─────────────────────────────────────────────────
    if pkg.hitl_decisions:
        htbl = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
        htbl.add_column("Step",       style="cyan", no_wrap=True)
        htbl.add_column("Verdict",    no_wrap=True)
        htbl.add_column("Reviewer",   style="dim")
        htbl.add_column("Reason",     max_width=50)
        htbl.add_column("When",       style="dim", no_wrap=True)
        htbl.add_column("Hash",       style="dim", no_wrap=True, max_width=12)
        for d in pkg.hitl_decisions:
            v = d.verdict
            v_color = "green" if "approve" in v else ("red" if "reject" in v or "timeout" in v else "yellow")
            when_str = (d.decided_at or "")[:19].replace("T", " ")
            htbl.add_row(
                d.step,
                f"[{v_color}]{v}[/{v_color}]",
                d.reviewer_id or "—",
                d.reason[:50] if d.reason else "—",
                when_str or "—",
                (d.row_hash[:10] + "…") if d.row_hash else "—",
            )
        console.print("\n[bold dim]HUMAN DECISIONS[/bold dim]")
        console.print(htbl)
    else:
        console.print("\n[dim]No HITL decisions recorded for this run.[/dim]")

    # ── Footer ────────────────────────────────────────────────────────────────
    console.print(
        f"\n[dim]Generated at {pkg.generated_at[:19].replace('T', ' ')}  "
        f"·  engine {pkg.engine_version}  "
        f"·  doc-hash {pkg._document_hash()[:16]}…[/dim]\n"
    )
    console.print(
        "[dim]To replay:      [bold]antcrew trace replay[/bold] --checkpointer ~/.antcrew/threads.db "
        f"--trace {trace_path}\n"
        "To export JSON:  [bold]antcrew inspect[/bold] " + run_id[:12] + "… --json[/dim]"
    )


@app.command(name="runs")
def runs_cmd(
    trace: Path = typer.Option(
        _DEFAULT_TRACE_DB,
        "--trace", "-t",
        help="TraceLog SQLite file (default: ~/.antcrew/trace.db)",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max runs to show"),
    team: Optional[str] = typer.Option(None, "--team", help="Filter by team name"),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status (done, error, running…)"),
) -> None:
    """List recent governed executions.

    \b
    Examples:
        antcrew runs
        antcrew runs --limit 50
        antcrew runs --team dev --status done
    """
    from rich.table import Table

    from antcrew.trace import TraceLog

    trace_path = Path(str(trace).replace("~", str(Path.home())))
    if not trace_path.exists():
        console.print(
            "[dim]No runs recorded yet.[/dim]\n"
            "[dim]Run [bold]antcrew issue[/bold] or [bold]antcrew run[/bold] to create one.[/dim]"
        )
        return

    tlog = TraceLog(str(trace_path))
    runs = tlog.list_runs_filtered(team=team, status=status, limit=limit)
    tlog.close()

    if not runs:
        console.print("[dim]No runs match the current filters.[/dim]")
        return

    tbl = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    tbl.add_column("Run ID",  style="cyan",   no_wrap=True, max_width=16)
    tbl.add_column("Team",    style="yellow", no_wrap=True)
    tbl.add_column("Status",  no_wrap=True)
    tbl.add_column("Cost",    justify="right")
    tbl.add_column("HITL",    justify="right")
    tbl.add_column("Date",    style="dim",    no_wrap=True)
    tbl.add_column("Request", max_width=50)

    tlog2 = TraceLog(str(trace_path))
    for r in runs:
        status_str = r["status"]
        status_color = "green" if status_str == "done" else ("red" if status_str == "error" else "yellow")
        cost_str = f"${r['cost_usd']:.4f}" if r.get("cost_usd") else "—"
        date_str = (r.get("started_at") or "")[:16].replace("T", " ")
        hitl_rows = tlog2.get_hitl_decisions(r["id"])
        hitl_str = str(len(hitl_rows)) if hitl_rows else "—"
        tbl.add_row(
            r["id"][:14] + "…",
            r["team"],
            f"[{status_color}]{status_str}[/{status_color}]",
            cost_str,
            hitl_str,
            date_str,
            r["request"][:50],
        )
    tlog2.close()

    console.print(f"\n[bold]Recent runs[/bold] [dim]({trace_path})[/dim]\n")
    console.print(tbl)
    console.print(
        f"\n[dim]{len(runs)} run(s) shown  ·  "
        "[bold]antcrew inspect <run-id>[/bold] for evidence detail[/dim]\n"
    )


@app.command(name="evidence")
def evidence_cmd(
    run_id: str = typer.Argument(..., help="Run ID (prefix match supported)"),
    trace: Path = typer.Option(
        _DEFAULT_TRACE_DB,
        "--trace", "-t",
        help="TraceLog SQLite file (default: ~/.antcrew/trace.db)",
    ),
    html: Optional[Path] = typer.Option(
        None, "--html",
        help="Export evidence as a self-contained HTML file (e.g. evidence.html)",
    ),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON evidence package"),
    open_browser: bool = typer.Option(False, "--open", "-o", help="Open the HTML report in a browser"),
) -> None:
    """Export an evidence package for a governed execution.

    \b
    Examples:
        antcrew evidence ac_20261007_a3f2 --html evidence.html --open
        antcrew evidence ac_20261007_a3f2 --json > evidence.json
    """
    from antcrew.evidence import EvidencePackage
    from antcrew.trace import TraceLog

    trace_path = Path(str(trace).replace("~", str(Path.home())))
    if not trace_path.exists():
        console.print(f"[red]TraceLog not found:[/] {trace_path}")
        raise typer.Exit(1)

    tlog = TraceLog(str(trace_path))

    run = tlog.get_run(run_id)
    if run is None:
        all_runs = tlog.list_runs(limit=200)
        candidates = [r for r in all_runs if r["id"].startswith(run_id)]
        if not candidates:
            console.print(f"[red]Run not found:[/] {run_id!r}")
            tlog.close()
            raise typer.Exit(1)
        run_id = candidates[0]["id"]

    pkg = EvidencePackage.from_trace(tlog, run_id)
    tlog.close()

    if output_json:
        typer.echo(pkg.to_json())
        return

    # Export HTML
    out_path = html or Path(f"evidence_{run_id[:12]}.html")
    out_path.write_text(pkg.to_html(), encoding="utf-8")
    console.print(f"[green]✓[/] Evidence report → [cyan]{out_path}[/cyan]")
    console.print(
        f"  [dim]run: {run_id[:16]}…  ·  chain: {pkg.chain_status}  ·  "
        f"{pkg.hitl_count} HITL decision(s)[/dim]"
    )

    if open_browser or (html is None and not output_json):
        import webbrowser
        webbrowser.open(out_path.resolve().as_uri())
        console.print("  [dim]Opening in browser…[/dim]")


@app.command(name="verify")
def verify_cmd(
    run_id: str = typer.Argument(..., help="Run ID to verify (prefix match supported)"),
    trace: Path = typer.Option(
        _DEFAULT_TRACE_DB,
        "--trace", "-t",
        help="TraceLog SQLite file (default: ~/.antcrew/trace.db)",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output raw JSON verification result"),
) -> None:
    """Verify the integrity of an execution's event chain.

    Checks that every event in the run (run_started, agent_call, hitl_decision,
    run_ended) has a valid SHA-256 hash and that no event was inserted, deleted,
    or modified after the fact.

    \b
    Examples:
        antcrew verify ac_20261007_a3f2
        antcrew verify ac_20261007 --json
    """
    from rich.panel import Panel

    from antcrew.evidence import EvidencePackage
    from antcrew.trace import TraceLog

    trace_path = Path(str(trace).replace("~", str(Path.home())))
    if not trace_path.exists():
        console.print(f"[red]TraceLog not found:[/] {trace_path}")
        raise typer.Exit(1)

    tlog = TraceLog(str(trace_path))

    run = tlog.get_run(run_id)
    if run is None:
        all_runs = tlog.list_runs(limit=200)
        candidates = [r for r in all_runs if r["id"].startswith(run_id)]
        if not candidates:
            console.print(f"[red]Run not found:[/] {run_id!r}")
            tlog.close()
            raise typer.Exit(1)
        run = candidates[0]
        run_id = run["id"]

    exec_result  = tlog.verify_execution_chain(run_id)
    hitl_result  = tlog.verify_hitl_chain()
    pkg          = EvidencePackage.from_trace(tlog, run_id)
    tlog.close()

    if output_json:
        import json as _j
        typer.echo(_j.dumps({
            "run_id":         run_id,
            "execution_chain": exec_result,
            "hitl_chain":      hitl_result,
            "document_hash":   pkg._document_hash(),
        }, indent=2))
        valid_overall = exec_result.get("valid") is not False and hitl_result.get("valid") is not False
        raise typer.Exit(0 if valid_overall else 1)

    # ── Rich output ───────────────────────────────────────────────────────────
    exec_valid  = exec_result.get("valid")
    hitl_valid  = hitl_result.get("valid")
    overall_ok  = exec_valid is not False and hitl_valid is not False

    def _status(valid) -> str:
        if valid is True:
            return "[green]✓ INTACT[/green]"
        if valid is False:
            return "[red]✗ BROKEN[/red]"
        return "[yellow]○ EMPTY[/yellow]"

    exec_total  = exec_result.get("total", 0)
    exec_ver    = exec_result.get("verified", 0)
    chain_root  = exec_result.get("chain_root", "")

    hitl_total  = hitl_result.get("total", 0)
    hitl_ver    = hitl_result.get("verified", 0)

    doc_hash    = pkg._document_hash()
    status_color = "green" if run.get("status") == "done" else ("red" if run.get("status") == "error" else "yellow")

    lines = [
        f"[bold]{run.get('request', '')[:80] or '(no request recorded)'}[/bold]\n",
        f"  Run ID:      [cyan]{run_id}[/cyan]",
        f"  Status:      [{status_color}]{run.get('status', '?')}[/{status_color}]",
        f"  Team:        [yellow]{run.get('team', '—')}[/yellow]\n",
        f"  [bold]Execution chain[/bold]   {_status(exec_valid)}",
        f"  Events:      {exec_ver}/{exec_total} verified",
    ]
    if exec_result.get("broken_at"):
        lines.append(f"  Broken at:   [red]sequence {exec_result['broken_at']}[/red]")
    if chain_root:
        lines.append(f"  Chain root:  [dim]{chain_root[:32]}…[/dim]")
    lines.append("")
    lines.append(f"  [bold]HITL chain[/bold]       {_status(hitl_valid)}")
    lines.append(f"  Decisions:   {hitl_ver}/{hitl_total} verified")
    if hitl_result.get("broken_at"):
        lines.append(f"  Broken at:   [red]row id {hitl_result['broken_at']}[/red]")
    lines.append("")
    lines.append(f"  Document hash: [dim]{doc_hash[:48]}…[/dim]")

    # Coverage warning: a chain can be intact but incomplete (e.g. run_ended missing).
    # An intact but partial chain must not be presented as a complete verified execution.
    cov = pkg.coverage
    cov_level = cov.get("level", "empty")
    if exec_valid is True and cov_level != "full":
        lines.append("")
        lines.append(
            f"  [yellow]⚠ Coverage: {cov_level}[/yellow] — "
            + ("run_ended missing" if not cov.get("has_run_end") else "")
            + ("run_started missing" if not cov.get("has_run_start") else "")
            + " — chain is intact but execution record is incomplete"
        )
        overall_ok = False  # incomplete chain is not a passing verification

    border = "green" if overall_ok else ("yellow" if cov_level == "partial" else "red")
    title  = "[bold green]Verification PASSED[/bold green]" if overall_ok else (
        "[bold yellow]Verification INCOMPLETE[/bold yellow]" if cov_level == "partial"
        else "[bold red]Verification FAILED[/bold red]"
    )

    console.print(Panel("\n".join(lines), title=title, border_style=border))

    if not overall_ok:
        raise typer.Exit(1)


@app.command(name="verify-package")
def verify_package_cmd(
    package: Path = typer.Argument(..., help="Path to an exported EvidencePackage JSON file"),
    output_json: bool = typer.Option(False, "--json", help="Output raw JSON result"),
) -> None:
    """Verify an exported EvidencePackage JSON independently of the live TraceLog.

    Checks that the document_hash recorded in the package matches the SHA-256
    of the package body.  Use this to verify a package handed to an auditor
    or stored in a third-party system, without needing the original database.

    \b
    Examples:
        antcrew verify-package evidence-a3f4b9c1.json
        antcrew verify-package evidence-a3f4b9c1.json --json
    """
    import hashlib
    import json as _j

    from rich.panel import Panel

    if not package.exists():
        console.print(f"[red]File not found:[/] {package}")
        raise typer.Exit(1)

    try:
        raw = package.read_text(encoding="utf-8")
        doc = _j.loads(raw)
    except Exception as exc:
        console.print(f"[red]Failed to parse package:[/] {exc}")
        raise typer.Exit(1)

    claimed_hash = doc.get("document_hash", "")
    body = {k: v for k, v in doc.items() if k not in ("document_hash", "hmac_sha256")}
    computed_hash = "sha256:" + hashlib.sha256(
        _j.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    valid = claimed_hash == computed_hash
    run_id = doc.get("run_id", "unknown")

    if output_json:
        typer.echo(_j.dumps({
            "valid": valid,
            "run_id": run_id,
            "claimed_hash": claimed_hash,
            "computed_hash": computed_hash,
        }, indent=2))
        raise typer.Exit(0 if valid else 1)

    if valid:
        console.print(Panel(
            f"  Run ID:        [cyan]{run_id}[/cyan]\n"
            f"  Document hash: [dim]{claimed_hash[:48]}…[/dim]\n\n"
            "  Hash verified against package body.",
            title="[bold green]Package VALID[/bold green]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"  Run ID:        [cyan]{run_id}[/cyan]\n"
            f"  Claimed:   [red]{claimed_hash[:48]}…[/red]\n"
            f"  Computed:  [yellow]{computed_hash[:48]}…[/yellow]\n\n"
            "  The package has been modified after export.",
            title="[bold red]Package INVALID[/bold red]",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def show(
    path: Path = typer.Argument(..., help="Path to a JSON state file saved with --save"),
    output_json: bool = typer.Option(
        False, "--json", help="Print raw JSON instead of rich output"
    ),
) -> None:
    """Display a previously saved pipeline state."""
    from antcrew.utils.persistence import load_state

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    raw = load_state(path)

    if output_json:
        typer.echo(json.dumps(raw, indent=2))
        return

    console.print(f"\n[bold green]AntCrew show[/] — [cyan]{path}[/]\n")

    # ── detect team type from available keys ─────────────────────────────────
    if raw.get("prd") or raw.get("tickets") or raw.get("code_artifacts"):
        _print_state_raw(raw, "dev")
    elif raw.get("research_document"):
        _print_state_raw(raw, "research")
    elif raw.get("content_piece"):
        _print_state_raw(raw, "content")
    else:
        _print_state_raw(raw, "dev")

    console.print()


@app.command()
def extract(
    path: Path = typer.Argument(..., help="JSON state file (--save output) or project JSON"),
    output: Path = typer.Option(
        Path("output"), "--output", "-o", help="Directory to write files into"
    ),
    include_tests: bool = typer.Option(True, "--tests/--no-tests", help="Also write test artifacts"),
    include_devops: bool = typer.Option(True, "--devops/--no-devops", help="Also write devops artifacts"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List files that would be written without writing"),
) -> None:
    """Write generated code artifacts from a saved state to disk as real files."""
    from antcrew.utils.persistence import load_state

    if not path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    raw = load_state(path)

    # Support both plain state files and project files (which nest state under "state").
    if "state" in raw and isinstance(raw["state"], dict):
        raw = raw["state"]

    artifacts: list[tuple[str, str]] = []  # (rel_path, content)

    for a in raw.get("code_artifacts") or []:
        if isinstance(a, dict) and a.get("file_path") and a.get("content") is not None:
            artifacts.append((a["file_path"], a["content"]))

    if include_tests:
        for a in raw.get("test_artifacts") or []:
            if isinstance(a, dict) and a.get("file_path") and a.get("content") is not None:
                artifacts.append((a["file_path"], a["content"]))

    if include_devops:
        for a in raw.get("devops_artifacts") or []:
            if isinstance(a, dict) and a.get("file_path") and a.get("content") is not None:
                artifacts.append((a["file_path"], a["content"]))

    if not artifacts:
        console.print("[yellow]No artifacts found in the state file.[/]")
        raise typer.Exit(0)

    console.print(f"\n[bold green]AntCrew extract[/] — {len(artifacts)} file(s) → [cyan]{output}/[/]\n")

    from antcrew.core.paths import safe_artifact_path

    skipped = 0
    for rel, content in artifacts:
        try:
            dest = safe_artifact_path(rel, output)
        except ValueError:
            console.print(f"  [red]SECURITY: skipped[/] {rel!r} — resolves outside output directory")
            skipped += 1
            continue
        if dry_run:
            console.print(f"  [dim](dry-run)[/] {dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            console.print(f"  [green]✓[/] {dest}")

    written = len(artifacts) - skipped
    if not dry_run and written:
        console.print(f"\n[bold green]Done![/] {written} file(s) written to [cyan]{output}/[/]\n")
    if skipped:
        console.print(f"[yellow]{skipped} file(s) skipped (path traversal attempt).[/]")


@app.command()
def describe(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to agentteam.yaml"),
    team: str = typer.Option("dev", "--team", "-t", help=f"Team preset: {_TEAM_CHOICES}"),
    trace: Optional[Path] = typer.Option(None, "--trace", help="TraceLog DB to show historical average cost"),
    context: Optional[Path] = typer.Option(
        None, "--context",
        help="Pre-computed scan JSON (from 'antcrew scan --output'). "
             "Shows what codebase context would be injected at run time.",
    ),
) -> None:
    """Show pipeline agents, data flow (consumes/produces), and coherence check.

    \b
    Examples:
        antcrew describe --team dev
        antcrew describe --config agentteam.yaml
        antcrew describe --team fullstack --context ctx.json
    """
    import json as _json

    from rich.table import Table

    # ── Parse config file (YAML/JSON) without instantiating the LLM ─────────
    cfg: dict = {}
    pipeline_name = team
    model_str = "claude"

    if config:
        if not config.exists():
            console.print(f"[red]Config file not found:[/] {config}")
            raise typer.Exit(1)
        try:
            raw_text = config.read_text(encoding="utf-8")
            if config.suffix.lower() == ".json":
                cfg = _json.loads(raw_text)
            else:
                try:
                    import yaml as _yaml  # type: ignore[import]
                    cfg = _yaml.safe_load(raw_text) or {}
                except ImportError:
                    console.print(
                        "[red]PyYAML required for YAML files.[/]  "
                        "Install: [bold]pip install pyyaml[/]"
                    )
                    raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Failed to read config:[/] {exc}")
            raise typer.Exit(1)

        pipeline_name = config.stem
        team = cfg.get("team", team).lower()
        model_str = cfg.get("model", model_str)

    # ── Agent class registry (no instantiation needed) ────────────────────────
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.agents.codebase_scanner import CodebaseScannerAgent
    from antcrew.agents.copywriter import CopywriterAgent
    from antcrew.agents.devops import DevOpsAgent
    from antcrew.agents.doc_writer import DocWriterAgent
    from antcrew.agents.editor import EditorAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent
    from antcrew.agents.idea import IdeaAgent
    from antcrew.agents.pm import PMAgent
    from antcrew.agents.qa import QAAgent
    from antcrew.agents.researcher import ResearcherAgent
    from antcrew.agents.reviewer import ReviewerAgent
    from antcrew.agents.sprint_planner import SprintPlannerAgent

    _CLASSES: dict[str, type] = {
        "business_analyst": BusinessAnalystAgent,
        "pm":               PMAgent,
        "backend_dev":      BackendDevAgent,
        "frontend_dev":     FrontendDevAgent,
        "qa":               QAAgent,
        "reviewer":         ReviewerAgent,
        "devops":           DevOpsAgent,
        "doc_writer":       DocWriterAgent,
        "researcher":       ResearcherAgent,
        "idea":             IdeaAgent,
        "copywriter":       CopywriterAgent,
        "writer":           CopywriterAgent,   # alias used by research team
        "editor":           EditorAgent,
        "codebase_scanner": CodebaseScannerAgent,
        "sprint_planner":   SprintPlannerAgent,
    }

    _DEFAULT_ORDER: dict[str, list[str]] = {
        "dev":       ["business_analyst", "pm", "backend_dev"],
        "fullstack": [
            "codebase_scanner", "business_analyst", "pm", "sprint_planner",
            "backend_dev", "frontend_dev", "qa", "reviewer", "devops", "doc_writer",
        ],
        "research":  ["researcher", "writer"],
        "content":   ["idea", "copywriter", "editor"],
    }

    # ── Determine agent order ─────────────────────────────────────────────────
    if "flow" in cfg:
        # Unique ordered list: first appearance in each edge wins.
        seen: list[str] = []
        for step in cfg["flow"]:
            for node in list(step)[:2]:  # skip optional condition (3rd element)
                if node not in seen:
                    seen.append(str(node))
        ordered: list[str] = seen
    else:
        base = list(_DEFAULT_ORDER.get(team, ["business_analyst", "pm", "backend_dev"]))
        extra = [k for k in (cfg.get("agents") or {}) if k not in base]
        ordered = base + extra

    # ── Header ────────────────────────────────────────────────────────────────
    console.print(
        f"\n[bold green]Pipeline:[/] [cyan]{pipeline_name}[/]  "
        f"[dim]team={team}  model={model_str}[/dim]\n"
    )

    # ── Table ─────────────────────────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("Agent",    style="cyan",  no_wrap=True, min_width=18)
    table.add_column("Consumes", style="white", min_width=30)
    table.add_column("Produces", style="green")

    for agent_name in ordered:
        cls = _CLASSES.get(agent_name)
        consumed = list(getattr(cls, "consumes", [])) if cls else []
        produced = list(getattr(cls, "produces", [])) if cls else []
        table.add_row(
            agent_name,
            ", ".join(consumed) if consumed else "—",
            ", ".join(produced) if produced else "—",
        )

    console.print(table)
    console.print()

    # ── Coherence check (unknown agent names) ─────────────────────────────────
    unknown = [n for n in ordered if n not in _CLASSES]
    if unknown:
        console.print(
            f"[bold yellow]Coherencia:[/] {len(unknown)} unknown agent(s): "
            + ", ".join(unknown)
            + "\n"
        )
    else:
        console.print("[bold green]Coherencia:[/] OK\n")

    # ── Pre-loaded scan context preview (optional) ────────────────────────────
    if context is not None:
        if not context.exists():
            console.print(f"[red]Context file not found:[/] {context}")
        else:
            import json as _ctx_json

            from rich.table import Table as _RTable
            ctx_data = _ctx_json.loads(context.read_text(encoding="utf-8"))
            components = ctx_data.get("components") or [ctx_data]
            ctx_tbl = _RTable(show_header=False, box=None, padding=(0, 2))
            ctx_tbl.add_column("Field", style="dim", no_wrap=True)
            ctx_tbl.add_column("Value", style="white")
            for comp in components:
                label = comp.get("label", "—")
                tech = ", ".join(comp.get("tech_stack") or []) or "—"
                exists = comp.get("what_exists") or "—"
                missing = comp.get("what_is_missing") or "—"
                ctx_tbl.add_row(f"[{label}] tech_stack",    tech)
                ctx_tbl.add_row(f"[{label}] what_exists",   exists)
                ctx_tbl.add_row(f"[{label}] what_is_missing", missing)
            from rich.panel import Panel as _Panel
            console.print(_Panel(ctx_tbl, title=f"Pre-loaded context ({context.name})",
                                 border_style="magenta"))
            console.print()

    # ── Historical cost from TraceLog (optional) ──────────────────────────────
    trace_path = trace or Path.home() / ".antcrew" / "trace.db"
    if trace_path.exists():
        try:
            from antcrew.trace import TraceLog
            tl = TraceLog(str(trace_path))
            stats = tl.get_stats()
            total = stats.get("total_runs", 0)
            if total:
                avg = stats.get("avg_cost_usd", 0.0)
                total_c = stats.get("total_cost_usd", 0.0)
                console.print(
                    f"[dim]Historical cost ({total} run{'s' if total != 1 else ''}): "
                    f"avg [cyan]${avg:.4f}[/]  total [cyan]${total_c:.4f}[/]  "
                    f"(from {trace_path})[/dim]\n"
                )
        except Exception:
            pass


@app.command(name="agents")
def agents_cmd(
    output_json: bool = typer.Option(False, "--json", help="Output as JSON array."),
) -> None:
    """List all built-in agent types with their role descriptions."""
    from antcrew.agents.registry import AGENT_REGISTRY, get_agent_class

    entries = []
    for name, (_, cls_name) in AGENT_REGISTRY.items():
        role = ""
        try:
            cls = get_agent_class(name)
            role = getattr(cls, "role_description", "") or ""
        except Exception:
            role = ""
        entries.append({"name": name, "class": cls_name, "role": role})

    if output_json:
        typer.echo(json.dumps(entries, indent=2))
        return

    from rich.table import Table
    tbl = Table(show_header=True, header_style="bold", box=None, show_edge=False)
    tbl.add_column("Name", style="cyan", min_width=20)
    tbl.add_column("Class", style="dim", min_width=22)
    tbl.add_column("Role description", style="white")
    for e in entries:
        tbl.add_row(e["name"], e["class"], e["role"])

    console.print("\n[bold]Built-in agent types[/bold]\n")
    console.print(tbl)
    console.print(
        "\n[dim]Custom agents: set [cyan]team: custom[/] with a [cyan]steps:[/] list "
        "and declare [cyan]system_prompt:[/] inline or via [cyan]system_prompt_file:[/].[/dim]\n"
    )

    from antcrew.agents.template_agent import POST_PROCESS_TRANSFORMS
    transforms = sorted(POST_PROCESS_TRANSFORMS)
    console.print(
        f"[dim]post_process transforms available: {', '.join(transforms)}[/dim]\n"
    )


