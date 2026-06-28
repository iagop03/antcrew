"""Shared display helpers used across CLI commands."""
from __future__ import annotations

import typer
from rich.panel import Panel
from rich.syntax import Syntax

from antcrew.cli._app import console, _TEAM_CHOICES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _build_team(
    team: str,
    model_str: str,
    integrations: list,
    llm=None,
    *,
    project_dir: "str | None" = None,
    project_dirs: "dict | None" = None,
):
    from antcrew.config import build_llm

    llm = llm or build_llm(model_str)

    if team == "dev":
        from antcrew.teams.dev_team import DevTeam
        return DevTeam(model=llm, integrations=integrations)
    if team == "fullstack":
        from antcrew.teams.fullstack_team import FullStackTeam
        return FullStackTeam(
            model=llm, integrations=integrations,
            project_dir=project_dir, project_dirs=project_dirs,
        )
    if team == "research":
        from antcrew.teams.research_team import ResearchTeam
        return ResearchTeam(model=llm)
    if team == "content":
        from antcrew.teams.content_team import ContentTeam
        return ContentTeam(model=llm)

    raise typer.BadParameter(f"Unknown team '{team}'. Choose from: {_TEAM_CHOICES}")


def _print_state(state: dict, team: str) -> None:
    if team in ("dev", "fullstack"):
        if state.get("prd"):
            console.print(Panel(
                f"[bold]{state['prd'].title}[/]\n{state['prd'].summary}",
                title="PRD", border_style="blue",
            ))
        if state.get("tickets"):
            tickets = state["tickets"]
            console.print(Panel(
                "\n".join(f"  [{t.priority.value}] {t.id}: {t.title}" for t in tickets),
                title=f"Tickets ({len(tickets)})", border_style="yellow",
            ))
        if state.get("code_artifacts"):
            for a in state["code_artifacts"]:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  ({a.ticket_id})", border_style="green",
                ))
        if state.get("test_artifacts"):
            console.print(f"[dim]Test files: {len(state['test_artifacts'])}[/]")
        if state.get("test_results") is not None:
            _print_test_results(state["test_results"])
        if state.get("devops_artifacts"):
            for a in state["devops_artifacts"]:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  [dim](devops)[/dim]", border_style="cyan",
                ))
        if state.get("doc_artifacts"):
            for a in state["doc_artifacts"]:
                console.print(Panel(
                    Syntax(a.content, "markdown", theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  [dim]({a.doc_type})[/dim]", border_style="blue",
                ))
        if state.get("review"):
            r = state["review"]
            colour = {"approve": "green", "request_changes": "yellow", "reject": "red"}[r.verdict]
            console.print(Panel(
                r.summary,
                title=f"Code Review: [{colour}]{r.verdict.upper()}[/{colour}]",
                border_style=colour,
            ))

    elif team == "research":
        if state.get("research_document"):
            doc = state["research_document"]
            console.print(Panel(
                "\n".join(f"• {f}" for f in doc.key_findings),
                title=f"[bold]{doc.title}[/] — Key Findings", border_style="cyan",
            ))
        if state.get("content_piece") and state["content_piece"].body:
            piece = state["content_piece"]
            console.print(Panel(
                piece.body[:2000] + (" …" if len(piece.body) > 2000 else ""),
                title=piece.title, border_style="blue",
            ))

    elif team == "content":
        if state.get("content_piece"):
            piece = state["content_piece"]
            console.print(Panel(
                piece.body[:3000] + (" …" if len(piece.body) > 3000 else ""),
                title=f"{piece.title}  [dim]({piece.word_count or '?'} words)[/dim]",
                border_style="magenta",
            ))

    elif team == "custom":
        _SKIP = {"request", "messages", "errors", "metadata", "current_agent"}
        for key, value in state.items():
            if key in _SKIP or value is None:
                continue
            text = str(value)
            console.print(Panel(
                text[:3000] + (" …" if len(text) > 3000 else ""),
                title=f"[bold]{key}[/]",
                border_style="cyan",
            ))

    if state.get("errors"):
        for err in state["errors"]:
            console.print(f"[red]Error:[/] {err}")


# ---------------------------------------------------------------------------
# Test results display
# ---------------------------------------------------------------------------

def _print_test_results(tr) -> None:
    """Print a RunResult panel — works with both live objects and raw dicts."""
    if tr is None:
        return
    # tr may be a RunResult dataclass or a plain dict (from load_state)
    if hasattr(tr, "success"):
        success  = tr.success
        summary  = tr.summary()
        output   = tr.output or ""
    else:
        passed   = int(tr.get("passed", 0))
        failed   = int(tr.get("failed", 0))
        errors   = int(tr.get("errors", 0))
        success  = bool(tr.get("success", failed == 0 and errors == 0))
        ms       = float(tr.get("duration_ms", 0))
        parts    = []
        if passed:
            parts.append(f"{passed} passed")
        if failed:
            parts.append(f"{failed} failed")
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        summary = (", ".join(parts) or "no tests ran") + f" in {ms:.0f}ms"
        output  = str(tr.get("output", ""))

    colour  = "green" if success else "red"
    icon    = "✓" if success else "✗"
    body    = summary
    if not success and output:
        tail = output[-1200:].strip()
        body = f"{summary}\n\n{tail}"
    console.print(Panel(
        body,
        title=f"[{colour}]Tests {icon}[/{colour}]",
        border_style=colour,
    ))


# ---------------------------------------------------------------------------
# Usage display
# ---------------------------------------------------------------------------

def _print_usage(llm) -> None:
    """Print a token/cost summary table if the LLM recorded any usage."""
    if llm is None:
        return
    summary = llm.get_usage_summary()
    if not summary["by_agent"]:
        return

    from rich.table import Table

    table = Table(title="Token usage", show_header=True, header_style="bold dim")
    table.add_column("Agent",          style="cyan",  no_wrap=True)
    table.add_column("Model",          style="dim",   no_wrap=True)
    table.add_column("In tokens",      justify="right")
    table.add_column("Out tokens",     justify="right")
    table.add_column("Cost (USD)",     justify="right")

    for entry in summary["by_agent"]:
        cost = entry["cost_usd"]
        cost_str = f"${cost:.4f}" if cost else "—"
        table.add_row(
            entry.get("agent") or "—",
            entry.get("model") or "—",
            str(entry["input_tokens"]),
            str(entry["output_tokens"]),
            cost_str,
        )

    total_cost = summary["total_cost_usd"]
    table.add_section()
    table.add_row(
        "[bold]Total[/]", "",
        f"[bold]{summary['total_input_tokens']}[/]",
        f"[bold]{summary['total_output_tokens']}[/]",
        f"[bold]{'$'+f'{total_cost:.4f}' if total_cost else '—'}[/]",
    )

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------


def _print_state_raw(raw: dict, team: str) -> None:
    """Like _print_state but works on plain dicts from load_state."""
    from antcrew.core.artifacts import (
        CodeArtifact, ContentPiece, DevOpsArtifact,
        PRD, ResearchDocument, Ticket,
    )

    def _maybe(cls, data):
        if data is None:
            return None
        try:
            return cls.model_validate(data)
        except Exception:
            return None

    if team in ("dev", "fullstack"):
        prd = _maybe(PRD, raw.get("prd"))
        if prd:
            console.print(Panel(
                f"[bold]{prd.title}[/]\n{prd.summary}",
                title="PRD", border_style="blue",
            ))

        tickets_raw = raw.get("tickets") or []
        tickets = [t for t in (_maybe(Ticket, t) for t in tickets_raw) if t]
        if tickets:
            console.print(Panel(
                "\n".join(f"  [{t.priority.value}] {t.id}: {t.title}" for t in tickets),
                title=f"Tickets ({len(tickets)})", border_style="yellow",
            ))

        for a_raw in (raw.get("code_artifacts") or []):
            a = _maybe(CodeArtifact, a_raw)
            if a:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  ({a.ticket_id})", border_style="green",
                ))

        for a_raw in (raw.get("devops_artifacts") or []):
            a = _maybe(DevOpsArtifact, a_raw)
            if a:
                lang = a.language or "text"
                console.print(Panel(
                    Syntax(a.content, lang, theme="monokai", line_numbers=True),
                    title=f"{a.file_path}  [dim](devops)[/dim]", border_style="cyan",
                ))

    elif team == "research":
        doc = _maybe(ResearchDocument, raw.get("research_document"))
        if doc:
            console.print(Panel(
                "\n".join(f"• {f}" for f in doc.key_findings),
                title=f"[bold]{doc.title}[/] — Key Findings", border_style="cyan",
            ))
        piece = _maybe(ContentPiece, raw.get("content_piece"))
        if piece and piece.body:
            console.print(Panel(
                piece.body[:2000] + (" …" if len(piece.body) > 2000 else ""),
                title=piece.title, border_style="blue",
            ))

    elif team == "content":
        piece = _maybe(ContentPiece, raw.get("content_piece"))
        if piece:
            console.print(Panel(
                piece.body[:3000] + (" …" if len(piece.body) > 3000 else ""),
                title=f"{piece.title}  [dim]({piece.word_count or '?'} words)[/dim]",
                border_style="magenta",
            ))

    if raw.get("test_results") is not None:
        _print_test_results(raw["test_results"])

    if raw.get("errors"):
        for err in raw["errors"]:
            console.print(f"[red]Error:[/] {err}")

    # ── metadata footer ───────────────────────────────────────────────────────
    request = raw.get("request", "")
    agent   = raw.get("current_agent", "")
    if request or agent:
        console.print(
            f"\n[dim]Request: {request}  |  Last agent: {agent}[/dim]"
        )


# ---------------------------------------------------------------------------
# antcrew project run / show / history
# ---------------------------------------------------------------------------

