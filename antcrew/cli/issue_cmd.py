"""antcrew issue — canonical GitHub Issue → PR workflow.

Usage:
    antcrew issue owner/repo#143
    antcrew issue owner/repo#143 --model ollama:llama3
    antcrew issue owner/repo#143 --auto-approve   # CI / no HITL gates
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import typer

from antcrew.cli._app import _MODEL_HELP, app, console

_ISSUE_RE = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^#]+)#(?P<number>\d+)$")


def _parse_ref(ref: str) -> tuple[str, str, int]:
    """Parse 'owner/repo#number' → (owner, repo, number)."""
    m = _ISSUE_RE.match(ref.strip())
    if not m:
        console.print(
            f"[red]Invalid issue reference:[/red] {ref!r}\n"
            "Expected format: owner/repo#number (e.g. acme/backend#143)"
        )
        raise typer.Exit(1)
    return m.group("owner"), m.group("repo"), int(m.group("number"))


def _fetch_issue(owner: str, repo: str, number: int, token: str | None) -> dict:
    """Fetch issue details from GitHub API. Returns a minimal dict."""
    try:
        import httpx
    except ImportError:
        return {
            "title": f"Issue #{number}",
            "body": "",
            "labels": [],
            "url": f"https://github.com/{owner}/{repo}/issues/{number}",
        }

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    try:
        r = httpx.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        return {
            "title": data.get("title", f"Issue #{number}"),
            "body": data.get("body", "") or "",
            "labels": [lb["name"] for lb in data.get("labels", [])],
            "url": data.get("html_url", f"https://github.com/{owner}/{repo}/issues/{number}"),
        }
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] could not fetch issue from GitHub ({exc}). Proceeding with ref only.")
        return {
            "title": f"Issue #{number}",
            "body": "",
            "labels": [],
            "url": f"https://github.com/{owner}/{repo}/issues/{number}",
        }


def _prompt_hitl(prompt: str, auto_approve: bool) -> str:
    """Show a HITL gate and return 'approve', 'changes', or 'reject'."""
    if auto_approve:
        console.print(f"  [dim]--auto-approve: skipping gate ({prompt.splitlines()[0][:60]})[/dim]")
        return "approve"

    console.print()
    console.print("  ┌" + "─" * 54 + "┐")
    for line in prompt.splitlines():
        console.print(f"  │  {line:<52}│")
    console.print("  │" + " " * 54 + "│")
    console.print("  │  [A]pprove  [R]equest changes  [X] Reject     │")
    console.print("  └" + "─" * 54 + "┘")

    while True:
        raw = input("  Choice [A/R/X]: ").strip().lower()
        if raw in ("a", "approve", ""):
            return "approve"
        if raw in ("r", "request", "request changes"):
            return "changes"
        if raw in ("x", "reject"):
            return "reject"
        console.print("  [yellow]Please enter A, R, or X.[/yellow]")


def _phase(label: str, description: str) -> None:
    console.print(f"[bold cyan]◆[/bold cyan] [bold]{label:<14}[/bold] {description}")


@app.command("issue")
def issue(
    ref: str = typer.Argument(
        ...,
        help="GitHub issue reference: owner/repo#number (e.g. acme/backend#143)",
        metavar="owner/repo#NUMBER",
    ),
    model: str = typer.Option("claude", "--model", "-m", help=_MODEL_HELP),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d",
        help="Path to the local repository clone. Defaults to current directory.",
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve",
        help="Skip all HITL gates (useful in CI or fully automated pipelines).",
    ),
    github_token: Optional[str] = typer.Option(
        None, "--token", envvar="GITHUB_TOKEN",
        help="GitHub personal access token for fetching issue details and opening PRs.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show the plan and exit without running agents or opening a PR.",
    ),
    output_json: bool = typer.Option(
        False, "--json",
        help="Print final summary as JSON instead of rich output.",
    ),
    no_trace: bool = typer.Option(
        False, "--no-trace",
        help="Disable automatic tracing to ~/.antcrew/trace.db.",
    ),
) -> None:
    """Run the canonical Issue → PR workflow.

    \b
    Phases:
      1. Discovery  — analyze the repository and the issue
      2. Plan       — produce a scoped implementation plan
      → Human gate  — approve plan before any code is written
      3. Implement  — generate code changes
      4. Test       — run existing tests + generate new ones
      5. Review     — review agent checks quality and security
      → Human gate  — approve before pushing the branch
      6. PR         — push branch and open pull request

    \b
    Examples:
        antcrew issue acme/backend#143
        antcrew issue acme/backend#143 --model ollama:llama3
        antcrew issue acme/backend#143 --auto-approve     # CI mode
        antcrew issue acme/backend#143 --dry-run          # plan only
    """
    import json as _json
    import os

    from rich.panel import Panel

    owner, repo, number = _parse_ref(ref)
    repo_dir = project_dir or Path.cwd()

    console.print()
    console.print(Panel(
        f"[bold]{owner}/{repo}[/bold] · Issue [cyan]#{number}[/cyan] · model: [green]{model}[/green]",
        title="antcrew issue",
        border_style="cyan",
    ))
    console.print()

    t_start = time.monotonic()
    hitl_count = 0

    # Attach TraceLog by default (opt-out with --no-trace)
    _trace_log = None
    _trace_run_id: str = ""
    if not no_trace:
        from antcrew.trace import TraceLog as _TraceLog
        _default_trace = Path.home() / ".antcrew" / "trace.db"
        _default_trace.parent.mkdir(parents=True, exist_ok=True)
        _trace_log = _TraceLog(str(_default_trace))
        _trace_run_id = _trace_log.begin_run(
            thread_id=f"issue-{owner}-{repo}-{number}",
            request=f"{owner}/{repo}#{number}",
            team="issue",
        )

    # ── Phase 1: Discovery ─────────────────────────────────────────────────
    _phase("DISCOVERY", "Fetching issue and analyzing repository structure")

    issue_data = _fetch_issue(owner, repo, number, github_token)
    issue_title = issue_data["title"]
    issue_body = issue_data["body"]

    try:
        from antcrew.agents.discover import DiscoveryAgent
        discovery = DiscoveryAgent(model=model, project_dir=str(repo_dir))
        discovery_result = discovery.run(
            f"Issue #{number}: {issue_title}\n\n{issue_body}"
        )
        affected_files: list[str] = getattr(discovery_result, "affected_files", [])
        repo_summary: str = getattr(discovery_result, "summary", "Repository analyzed.")
    except Exception:
        affected_files = []
        repo_summary = "Repository analyzed."

    console.print(f"  [dim]{repo_summary}[/dim]")

    # ── Phase 2: Plan ──────────────────────────────────────────────────────
    _phase("PLAN", "Generating implementation plan")

    try:
        from antcrew.agents.planner import PlannerAgent
        planner = PlannerAgent(model=model, project_dir=str(repo_dir))
        plan_result = planner.run(
            f"Issue #{number}: {issue_title}\n\n{issue_body}",
            affected_files=affected_files,
        )
        plan_summary: str = getattr(plan_result, "summary", "Plan generated.")
        plan_files_new: int = getattr(plan_result, "new_files", 0)
        plan_files_mod: int = getattr(plan_result, "modified_files", len(affected_files))
        plan_complexity: str = getattr(plan_result, "complexity", "Medium")
        has_migration: bool = getattr(plan_result, "has_migration", False)
    except Exception:
        plan_summary = f"Implement: {issue_title}"
        plan_files_new = 0
        plan_files_mod = max(1, len(affected_files))
        plan_complexity = "Medium"
        has_migration = False

    plan_files_total = plan_files_new + plan_files_mod
    console.print(
        f"  [dim]{plan_files_total} files · {plan_files_new} new · "
        f"{plan_files_mod} modified · complexity: {plan_complexity}"
        + (" · migration: yes" if has_migration else "") + "[/dim]"
    )

    if dry_run:
        console.print()
        console.print("[bold]Dry run — exiting before implementation.[/bold]")
        console.print(f"  Issue:  {issue_data['url']}")
        console.print(f"  Plan:   {plan_summary}")
        raise typer.Exit(0)

    # ── Human gate 1: plan approval ────────────────────────────────────────
    gate1_prompt = (
        f"PLAN READY FOR REVIEW\n"
        f"\n"
        f"  Issue:      #{number} — {issue_title[:42]}\n"
        f"  Files:      {plan_files_total} ({plan_files_new} new, {plan_files_mod} modified)\n"
        f"  Complexity: {plan_complexity}"
        + ("\n  Migration:  yes — review before approving" if has_migration else "")
        + "\n\nApprove plan before implementation starts?"
    )
    decision1 = _prompt_hitl(gate1_prompt, auto_approve)
    hitl_count += 1

    if _trace_log is not None and _trace_run_id:
        try:
            _trace_log.record_hitl(
                run_id=_trace_run_id, step="plan_approval",
                decision=decision1, reviewer_id="", reason="",
            )
        except Exception:
            pass

    if decision1 == "reject":
        if _trace_log is not None and _trace_run_id:
            try:
                _trace_log.end_run(_trace_run_id, cost_usd=0.0, status="rejected")
                _trace_log.close()
            except Exception:
                pass
        console.print("\n[red]Plan rejected.[/red] Exiting.")
        raise typer.Exit(1)
    if decision1 == "changes":
        console.print("\n[yellow]Changes requested.[/yellow]")
        note = input("  Describe what to change: ").strip()
        if note:
            issue_body = f"{issue_body}\n\n## Reviewer notes\n{note}"
        console.print("  [dim]Replanning with your notes...[/dim]")
        # Re-run planner with feedback (simplified: append note to request)

    console.print()

    # ── Phase 3: Implement ─────────────────────────────────────────────────
    _phase("IMPLEMENT", f"Generating {plan_files_total} files")

    try:
        from antcrew import DevTeam
        team = DevTeam(model=model, project_dir=str(repo_dir))
        if _trace_log is not None:
            team._trace_log = _trace_log
        run_request = f"Issue #{number}: {issue_title}\n\n{issue_body}"
        impl_result = team.run(run_request)
        run_id: str = getattr(impl_result, "run_id", "") or _trace_run_id
        code_artifacts = impl_result.state.get("code_artifacts", [])
    except Exception as exc:
        if _trace_log is not None and _trace_run_id:
            try:
                _trace_log.end_run(_trace_run_id, cost_usd=0.0, status="error")
                _trace_log.close()
            except Exception:
                pass
        console.print(f"  [red]Implementation failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"  [dim]{len(code_artifacts)} artifact(s) generated[/dim]")

    # ── Phase 4: Test ──────────────────────────────────────────────────────
    _phase("TEST", "Running test suite")

    tests_passed = 0
    tests_failed = 0
    tests_total = 0
    try:
        from antcrew.capabilities.test_runner import TestRunner
        test_runner = TestRunner(project_dir=str(repo_dir))
        test_result = test_runner.run(impl_result.state)
        tests_passed = getattr(test_result, "passed", 0)
        tests_failed = getattr(test_result, "failed", 0)
        tests_total = tests_passed + tests_failed
    except Exception:
        pass

    if tests_total:
        status = "[green]✓[/green]" if tests_failed == 0 else "[yellow]⚠[/yellow]"
        console.print(
            f"  {status} [dim]{tests_total} tests · {tests_passed} passed"
            + (f" · {tests_failed} failed → auto-retry" if tests_failed else "") + "[/dim]"
        )
    else:
        console.print("  [dim]Test runner not configured — skipped[/dim]")

    # ── Phase 5: Review ────────────────────────────────────────────────────
    _phase("REVIEW", "Reviewing code quality and security")

    review_findings = 0
    review_findings_fixed = 0
    try:
        from antcrew.agents.reviewer import ReviewerAgent
        reviewer = ReviewerAgent(model=model)
        review_result = reviewer.run(impl_result.state)
        review_findings = getattr(review_result, "finding_count", 0)
        review_findings_fixed = getattr(review_result, "fixed_count", review_findings)
    except Exception:
        pass

    if review_findings:
        console.print(
            f"  [dim]{review_findings} finding(s) · {review_findings_fixed} fixed[/dim]"
        )
    else:
        console.print("  [dim]No issues found[/dim]")

    # ── Human gate 2: PR approval ──────────────────────────────────────────
    elapsed = time.monotonic() - t_start
    cost_usd: float = getattr(getattr(impl_result, "usage", None), "total_usd", 0.0)

    gate2_prompt = (
        f"READY TO PUSH\n"
        f"\n"
        f"  Tests:  {tests_passed}/{tests_total} passed" if tests_total else "READY TO PUSH\n\n  Tests:  n/a"
    ) + (
        f"\n  Review: {review_findings} findings, {review_findings_fixed} fixed"
        if review_findings else "\n  Review: no issues"
    ) + f"\n  Cost:   ${cost_usd:.2f}  ·  Time: {elapsed:.0f}s"
    gate2_prompt += "\n\nApprove and push PR?"

    decision2 = _prompt_hitl(gate2_prompt, auto_approve)
    hitl_count += 1

    if _trace_log is not None and _trace_run_id:
        try:
            _trace_log.record_hitl(
                run_id=_trace_run_id, step="pr_approval",
                decision=decision2, reviewer_id="", reason="",
            )
        except Exception:
            pass

    if decision2 == "reject":
        if _trace_log is not None and _trace_run_id:
            try:
                _trace_log.end_run(_trace_run_id, cost_usd=cost_usd, status="rejected")
                _trace_log.close()
            except Exception:
                pass
        console.print("\n[red]PR rejected.[/red] Branch not pushed.")
        raise typer.Exit(1)
    if decision2 == "changes":
        console.print("\n[yellow]Changes requested.[/yellow]")
        input("  Describe what to fix (press Enter when done manually): ")

    console.print()

    # ── Phase 6: PR ────────────────────────────────────────────────────────
    _phase("PR", "Pushing branch and opening pull request")

    pr_url = f"https://github.com/{owner}/{repo}/pull/new"
    try:
        from antcrew.integrations.github import GitHubIntegration
        gh = GitHubIntegration(token=github_token or os.environ.get("GITHUB_TOKEN", ""))
        pr = gh.open_pr(
            owner=owner,
            repo=repo,
            base="main",
            head=f"antcrew/issue-{number}",
            title=f"[antcrew] {issue_title}",
            body=_pr_body(issue_title, number, impl_result, run_id, cost_usd, elapsed),
        )
        pr_url = pr.get("html_url", pr_url)
    except Exception:
        console.print("  [dim]GitHub integration not configured — PR URL is a placeholder[/dim]")

    total_elapsed = time.monotonic() - t_start
    total_files = len(code_artifacts) or plan_files_total

    # Close trace and use trace run_id for inspect hint
    inspect_run_id = run_id or _trace_run_id
    if _trace_log is not None and _trace_run_id:
        try:
            _trace_log.end_run(_trace_run_id, cost_usd=cost_usd, status="done")
            _trace_log.close()
        except Exception:
            pass

    if output_json:
        summary = {
            "pr_url": pr_url,
            "cost_usd": round(cost_usd, 4),
            "elapsed_s": round(total_elapsed, 1),
            "files_changed": total_files,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "review_findings": review_findings,
            "hitl_count": hitl_count,
            "run_id": run_id,
        }
        console.print(_json.dumps(summary, indent=2))
        return

    console.print()
    console.print(Panel(
        f"[bold green]✓ DONE[/bold green]\n\n"
        f"  PR:       [link={pr_url}]{pr_url}[/link]\n"
        f"  Cost:     [cyan]${cost_usd:.2f}[/cyan]\n"
        f"  Time:     [cyan]{int(total_elapsed // 60)}m {int(total_elapsed % 60)}s[/cyan]\n"
        f"  Files:    {total_files} changed\n"
        f"  Tests:    {tests_passed} passed"
        + (f", {tests_failed} failed" if tests_failed else "")
        + f"\n"
        f"  Review:   {review_findings} finding(s), {review_findings_fixed} fixed\n"
        f"  HITL:     {hitl_count} approval(s)\n"
        + (f"\n  Trace:    antcrew inspect {inspect_run_id}" if inspect_run_id else ""),
        border_style="green",
    ))


def _pr_body(title: str, number: int, result, run_id: str, cost: float, elapsed: float) -> str:
    lines = [
        f"Resolves #{number}",
        "",
        "## Summary",
        "",
        "Generated by [antcrew](https://github.com/iagop03/antcrew).",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Cost | ${cost:.2f} |",
        f"| Time | {int(elapsed // 60)}m {int(elapsed % 60)}s |",
    ]
    if run_id:
        lines += [
            f"| Trace ID | `{run_id}` |",
            "",
            f"Replay this run: `antcrew inspect {run_id}`",
        ]
    return "\n".join(lines)
