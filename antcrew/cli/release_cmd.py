"""antcrew release — manage production releases (groups of CRs)."""
from __future__ import annotations

import json as _json
import uuid
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import app, console

release_app = typer.Typer(name="release", help="Manage production releases (groups of change-requests).")
app.add_typer(release_app)


def _load_store(store_path: Path) -> dict:
    if store_path.exists():
        return _json.loads(store_path.read_text(encoding="utf-8"))
    return {"releases": {}}


def _save_store(store_path: Path, data: dict) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")


_DEFAULT_STORE = Path("~/.antcrew/releases.json").expanduser()


@release_app.command("create")
def release_create(
    name: str = typer.Argument(..., help="Release name (e.g. 'Sprint-42-Release')"),
    refs: Optional[list[str]] = typer.Option(
        None, "--ref", "-r",
        help="CR references to include (repeat: --ref CR-1234 --ref CR-1235).",
    ),
    from_jira_state: Optional[str] = typer.Option(
        None, "--from-jira-state",
        help="Pull all CRs in this Jira state (requires tracker: config). "
             "Example: --from-jira-state uat",
    ),
    target_date: Optional[str] = typer.Option(
        None, "--target-date", help="Target deployment date (ISO-8601, e.g. 2026-12-01)."
    ),
    store: Path = typer.Option(_DEFAULT_STORE, "--store", help="JSON store file for releases."),
    output_json: bool = typer.Option(False, "--json", help="Print result as JSON."),
) -> None:
    """Create a new release and add change-requests to it.

    \b
    Example — from explicit refs:
        antcrew release create Sprint-42 --ref CR-1234 --ref CR-1235

    \b
    Example — pull from Jira UAT state:
        antcrew release create Sprint-42 --from-jira-state uat
    """
    from antcrew.models.release import Release

    release_id = f"REL-{str(uuid.uuid4())[:8].upper()}"
    release = Release(
        id=release_id,
        name=name,
        target_date=target_date or "",
    )

    change_refs: list[str] = list(refs or [])

    if from_jira_state:
        try:
            from antcrew.adapters.tracker import IssueFilter, get_tracker_adapter
            import yaml as _yaml
            import os as _os
            cfg_path = _os.environ.get("ANTCREW_CONFIG", "agentteam.yaml")
            cfg_data: dict = {}
            if Path(cfg_path).exists():
                cfg_data = _yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8")) or {}
            tracker_cfg = cfg_data.get("tracker")
            if not tracker_cfg:
                console.print("[red]No tracker: config found.[/] Set up tracker: in agentteam.yaml.")
                raise typer.Exit(1)
            adapter = get_tracker_adapter(tracker_cfg)
            issues = adapter.fetch_issues(IssueFilter(state=from_jira_state))
            for issue in issues:
                if issue.change_ref and issue.change_ref not in change_refs:
                    change_refs.append(issue.change_ref)
            console.print(f"[dim]Pulled {len(issues)} issue(s) from Jira state {from_jira_state!r}[/]")
        except ImportError:
            console.print("[yellow]Warning:[/] PyYAML not installed, skipping --from-jira-state.")

    if not change_refs:
        console.print("[red]No change-refs to add.[/] Use --ref CR-1234 or --from-jira-state.")
        raise typer.Exit(1)

    for ref in change_refs:
        release.add_item(ref)

    # Persist to JSON store
    data = _load_store(store)
    data["releases"][release_id] = {
        "id": release_id,
        "name": name,
        "state": release.state,
        "target_date": release.target_date,
        "items": [{"change_ref": i.change_ref, "origin": i.origin, "run_ids": i.run_ids}
                  for i in release.items],
        "created_at": release.created_at,
    }
    _save_store(store, data)

    if output_json:
        import sys
        sys.stdout.write(_json.dumps(data["releases"][release_id], indent=2) + "\n")
        return

    console.print(f"\n[bold green]Release created:[/] [cyan]{release_id}[/] — {name}")
    console.print(f"  State:       [yellow]{release.state}[/]")
    if target_date:
        console.print(f"  Target date: {target_date}")
    console.print(f"  Change-refs: {', '.join(release.change_refs)}")
    console.print(f"\n[dim]Stored in: {store}[/dim]")


@release_app.command("list")
def release_list(
    store: Path = typer.Option(_DEFAULT_STORE, "--store", help="JSON store file for releases."),
    output_json: bool = typer.Option(False, "--json", help="Print as JSON."),
) -> None:
    """List all releases."""
    from rich.table import Table

    data = _load_store(store)
    releases = list(data.get("releases", {}).values())

    if output_json:
        import sys
        sys.stdout.write(_json.dumps(releases, indent=2) + "\n")
        return

    if not releases:
        console.print("[dim]No releases yet. Use 'antcrew release create'.[/dim]")
        return

    tbl = Table(title="Releases", show_header=True, header_style="bold dim")
    tbl.add_column("ID",      style="dim",    no_wrap=True)
    tbl.add_column("Name",    style="cyan")
    tbl.add_column("State",   no_wrap=True)
    tbl.add_column("CRs",     justify="right")
    tbl.add_column("Target",  style="dim")

    for rel in releases:
        state = rel.get("state", "draft")
        state_str = {
            "draft":            "[yellow]draft[/]",
            "pending_approval": "[blue]pending_approval[/]",
            "approved":         "[green]approved[/]",
            "rejected":         "[red]rejected[/]",
            "deployed":         "[bold green]deployed[/]",
        }.get(state, state)
        tbl.add_row(
            rel["id"],
            rel["name"],
            state_str,
            str(len(rel.get("items", []))),
            rel.get("target_date") or "—",
        )
    console.print(tbl)


@release_app.command("approve")
def release_approve(
    release_id: str = typer.Argument(..., help="Release ID to approve/reject"),
    decision: str = typer.Option(..., "--decision", "-d", help="approved | rejected"),
    approver_id: str = typer.Option("", "--approver-id", help="Reviewer email or user ID"),
    approver_role: str = typer.Option("", "--role", help="Reviewer role (e.g. qa_lead)"),
    reason: str = typer.Option("", "--reason", help="Optional approval reason / notes"),
    trace_db: Optional[Path] = typer.Option(
        None, "--trace", help="TraceLog DB to record the approval in (tamper-evident chain)."
    ),
    store: Path = typer.Option(_DEFAULT_STORE, "--store", help="JSON store file for releases."),
) -> None:
    """Record an approval or rejection for a release."""
    if decision not in ("approved", "rejected"):
        console.print(f"[red]Invalid decision:[/] {decision!r}. Use 'approved' or 'rejected'.")
        raise typer.Exit(1)

    data = _load_store(store)
    rel = data.get("releases", {}).get(release_id)
    if not rel:
        console.print(f"[red]Release not found:[/] {release_id}")
        raise typer.Exit(1)

    # Update state machine
    if decision == "approved":
        if rel.get("state") in ("draft", "pending_approval", "approved"):
            rel["state"] = "approved"
    else:
        rel["state"] = "rejected"

    # Append approval record
    rel.setdefault("approvals", []).append({
        "approver_id": approver_id,
        "approver_role": approver_role,
        "decision": decision,
        "reason": reason,
        "decided_at": __import__("datetime").datetime.utcnow().isoformat(),
    })
    _save_store(store, data)

    # Record in TraceLog hash chain if requested
    if trace_db:
        from antcrew.trace import TraceLog as _TraceLog
        tlog = _TraceLog(trace_db)
        row_id = tlog.record_release_approval(
            release_id=release_id,
            approver_id=approver_id,
            approver_role=approver_role,
            decision=decision,
            reason=reason,
        )
        tlog.close()
        console.print(f"[dim]Recorded in TraceLog (chain row id={row_id})[/dim]")

    icon = "[green]✓[/]" if decision == "approved" else "[red]✗[/]"
    console.print(f"{icon} Release [cyan]{release_id}[/] — {decision} by {approver_id or approver_role or 'unknown'}")


@release_app.command("package")
def release_package(
    release_id: str = typer.Argument(..., help="Release ID to package"),
    output_dir: Path = typer.Option(Path("."), "--output-dir", "-O", help="Output directory"),
    approvers_config: Optional[Path] = typer.Option(
        None, "--approvers", help="Approvers YAML config file"
    ),
    trace_db: Optional[Path] = typer.Option(
        None, "--trace", help="TraceLog DB for test evidence"
    ),
    store: Path = typer.Option(_DEFAULT_STORE, "--store", help="JSON store file for releases."),
) -> None:
    """Build a change package (Excel + summary + email drafts) for a release."""
    data = _load_store(store)
    rel_data = data.get("releases", {}).get(release_id)
    if not rel_data:
        console.print(f"[red]Release not found:[/] {release_id}")
        raise typer.Exit(1)

    from antcrew.models.release import Release, ReleaseItem as _RI
    release = Release(
        id=rel_data["id"],
        name=rel_data["name"],
        state=rel_data.get("state", "draft"),
        target_date=rel_data.get("target_date"),
    )
    for item_data in rel_data.get("items", []):
        release.items.append(_RI(
            release_id=release.id,
            change_ref=item_data["change_ref"],
            run_ids=item_data.get("run_ids", []),
            origin=item_data.get("origin", "antcrew"),
        ))

    approvers_cfg = None
    if approvers_config and approvers_config.exists():
        from antcrew.packager.approvers import ApproversConfig
        approvers_cfg = ApproversConfig.from_yaml(approvers_config)

    tlog = None
    if trace_db and trace_db.exists():
        from antcrew.trace import TraceLog as _TraceLog
        tlog = _TraceLog(trace_db)

    from antcrew.packager import ChangePackager
    packager = ChangePackager(
        release,
        approvers_config=approvers_cfg,
        trace_log=tlog,
    )
    result = packager.build(output_dir=output_dir)

    if tlog:
        tlog.close()

    console.print(f"\n[bold green]Package built:[/] {output_dir}")
    if result.excel_path:
        console.print(f"  Excel:   [cyan]{result.excel_path}[/]")
    console.print(f"  Summary: [cyan]{output_dir}/{release_id}_summary.md[/]")
    if result.email_drafts:
        console.print(f"  Emails:  [cyan]{output_dir}/{release_id}_email_drafts.json[/] ({len(result.email_drafts)} draft(s))")


@release_app.command("show")
def release_show(
    release_id: str = typer.Argument(..., help="Release ID to show"),
    store: Path = typer.Option(_DEFAULT_STORE, "--store", help="JSON store file for releases."),
) -> None:
    """Show details for one release."""
    data = _load_store(store)
    rel = data.get("releases", {}).get(release_id)
    if not rel:
        console.print(f"[red]Release not found:[/] {release_id}")
        raise typer.Exit(1)
    console.print_json(_json.dumps(rel, indent=2))
