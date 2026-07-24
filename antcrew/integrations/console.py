"""
ConsoleChannel — BaseChannel implementation for the terminal (Rich UI).

Displays artifacts in the terminal and prompts for approval interactively.
When the user types anything other than the single-key shortcuts, the input
is returned as conversational feedback (decision="feedback") so the agent
can refine its output before the reviewer approves.
"""
from __future__ import annotations

import json
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table

from antcrew.core.artifacts import (
    PRD,
    CodeArtifact,
    CodeReview,
    ContentPiece,
    ResearchDocument,
    TestArtifact,
    Ticket,
)
from antcrew.core.channel import BaseChannel

_console = Console()

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _display_artifact(artifact, agent_name: str) -> None:
    _console.rule(f"[bold blue]{agent_name}[/] — output")

    if artifact is None:
        _console.print("[dim]No artifact produced.[/dim]")
        return

    if isinstance(artifact, PRD):
        _console.print(Panel(
            f"[bold]{artifact.title}[/]\n\n{artifact.summary}",
            title="PRD",
        ))
        if artifact.functional_requirements:
            _console.print(Panel(
                "\n".join(f"• {r}" for r in artifact.functional_requirements),
                title="Functional requirements",
            ))

    elif isinstance(artifact, list) and artifact and isinstance(artifact[0], Ticket):
        table = Table(show_header=True, header_style="bold cyan", expand=True)
        table.add_column("ID", style="dim", width=14)
        table.add_column("Title")
        table.add_column("Priority", justify="center", width=10)
        _colors = {"low": "dim", "medium": "yellow", "high": "red", "critical": "bold red"}
        for t in artifact:
            color = _colors.get(t.priority.value, "white")
            table.add_row(t.id, t.title, f"[{color}]{t.priority.value}[/]")
        _console.print(table)

    elif isinstance(artifact, list) and artifact and isinstance(artifact[0], (CodeArtifact, TestArtifact)):
        for a in artifact:
            lang = getattr(a, "language", None) or "text"
            _console.print(Panel(
                Syntax(a.content, lang, theme="monokai", line_numbers=True),
                title=f"[bold]{a.file_path}[/]",
                subtitle=f"[dim]{a.description}[/]",
            ))

    elif isinstance(artifact, CodeReview):
        color = {"approve": "green", "request_changes": "yellow", "reject": "red"}[artifact.verdict]
        _console.print(Panel(
            artifact.summary,
            title=f"Code Review: [{color}]{artifact.verdict.upper()}[/{color}]",
            border_style=color,
        ))
        for f in artifact.findings:
            _console.print(
                f"  [{f.severity}] [bold]{f.file_path}[/]: {f.message}"
            )

    elif isinstance(artifact, ResearchDocument):
        _console.print(Panel(
            "\n".join(f"• {kf}" for kf in artifact.key_findings),
            title=f"[bold]{artifact.title}[/] — Key Findings",
            border_style="cyan",
        ))

    elif isinstance(artifact, ContentPiece):
        preview = (artifact.body or "")[:800]
        if len(artifact.body or "") > 800:
            preview += " …"
        _console.print(Panel(
            preview,
            title=f"[bold]{artifact.title}[/]",
            subtitle=f"[dim]{artifact.target_audience} · {artifact.tone}[/]",
            border_style="magenta",
        ))

    else:
        _console.print_json(json.dumps(artifact, default=str))


def _prompt_decision(agent_name: str, options: list[str]) -> str:
    """
    Prompt for a HITL decision.

    Single-key shortcuts for standard options (a/r/e).
    Any other input is returned verbatim as feedback text.
    """
    _console.print()
    _console.print(
        "  [dim]a[/dim] approve  [dim]r[/dim] reject  [dim]e[/dim] edit  "
        "[dim]— or type feedback to request changes[/dim]"
    )
    raw = Prompt.ask(f"[bold yellow]{agent_name}[/]", default="")
    stripped = raw.strip().lower()

    if stripped in ("a", "approve"):
        return "approve"
    if stripped in ("r", "reject"):
        return "reject"
    if stripped in ("e", "edit"):
        return "edit"
    # Anything else → return as-is so the caller can treat it as feedback
    return raw.strip() if raw.strip() else "approve"


# ---------------------------------------------------------------------------
# BaseChannel implementation
# ---------------------------------------------------------------------------

class ConsoleChannel(BaseChannel):
    """
    Terminal HITL channel — displays artifacts with Rich and prompts for a decision.

    Supports conversational mode: if the user types anything other than the
    single-key shortcuts, the input is forwarded to the agent as feedback.

    Usage:
        agent = BackendDevAgent(
            llm=AnthropicModel(),
            channel=ConsoleChannel(),
            approval_required=True,
        )
        team = DevTeam(agents={"backend_dev": agent})
        state = team.run_interactive("Build auth")
    """

    async def notify(self, message: str, **kwargs) -> None:
        _console.print(f"  [dim]● {message}[/dim]")

    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        response_options: Optional[list[str]] = None,
    ) -> dict:
        options = response_options or ["approve", "edit", "reject"]
        _display_artifact(artifact, agent_name)
        decision = _prompt_decision(agent_name, options)

        if decision == "edit":
            from antcrew.console import edit_artifact_in_editor
            edited = edit_artifact_in_editor(artifact)
            return {"decision": "edit", "edited": edited, "feedback": None}

        if decision in ("approve", "reject"):
            return {"decision": decision, "edited": None, "feedback": None}

        # Free-text → conversational feedback
        return {"decision": "feedback", "feedback": decision, "edited": None}
