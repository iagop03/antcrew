"""
Rich-based terminal display and interactive approval helpers.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Literal

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table

from antcrew.core.artifacts import PRD, CodeArtifact, Ticket

console = Console()

ApprovalResult = Literal["approve", "edit", "reject"]

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def print_prd(prd: PRD) -> None:
    console.rule("[bold blue]PRD — Product Requirements Document[/]")

    console.print(Panel(f"[bold]{prd.title}[/]\n\n{prd.summary}", title="Overview"))

    def _list_panel(title: str, items: list[str], color: str = "green") -> Panel:
        content = "\n".join(f"[{color}]•[/] {item}" for item in items) or "[dim]—[/]"
        return Panel(content, title=title, expand=False)

    console.print(
        Columns(
            [
                _list_panel("Goals", prd.goals, "green"),
                _list_panel("Out of scope", prd.out_of_scope, "red"),
            ]
        )
    )
    console.print(_list_panel("Functional requirements", prd.functional_requirements))
    console.print(
        _list_panel("Non-functional requirements", prd.non_functional_requirements, "yellow")
    )
    if prd.open_questions:
        console.print(_list_panel("Open questions", prd.open_questions, "magenta"))


def print_tickets(tickets: list[Ticket]) -> None:
    console.rule("[bold blue]Tickets[/]")

    table = Table(show_header=True, header_style="bold cyan", expand=True)
    table.add_column("ID", style="dim", width=14)
    table.add_column("Title")
    table.add_column("Priority", justify="center", width=10)
    table.add_column("Acceptance criteria")

    _priority_color = {
        "low": "dim",
        "medium": "yellow",
        "high": "red",
        "critical": "bold red",
    }

    for t in tickets:
        color = _priority_color.get(t.priority.value, "white")
        criteria = "\n".join(f"• {c}" for c in t.acceptance_criteria) or "—"
        table.add_row(
            t.id,
            f"[bold]{t.title}[/]\n[dim]{t.description}[/]",
            f"[{color}]{t.priority.value}[/]",
            criteria,
        )

    console.print(table)


def print_code_artifacts(artifacts: list[CodeArtifact]) -> None:
    console.rule("[bold blue]Code artifacts[/]")

    for artifact in artifacts:
        lang = artifact.language or "text"
        console.print(
            Panel(
                Syntax(artifact.content, lang, theme="monokai", line_numbers=True),
                title=f"[bold]{artifact.file_path}[/] [dim]({artifact.ticket_id})[/]",
                subtitle=f"[dim]{artifact.description}[/]",
            )
        )


# ---------------------------------------------------------------------------
# Interactive approval
# ---------------------------------------------------------------------------


def ask_approval(label: str) -> ApprovalResult:
    """Prompt approve / edit / reject and return the choice."""
    console.print()
    choice = Prompt.ask(
        f"[bold yellow]Review {label}[/] — what do you want to do?",
        choices=["a", "e", "r"],
        default="a",
        show_choices=True,
        show_default=True,
    )
    return {"a": "approve", "e": "edit", "r": "reject"}[choice]


def edit_artifact_in_editor(obj: PRD | list[Ticket]) -> str | None:
    """
    Open the artifact JSON in the system editor.
    Returns the edited JSON string, or None if unchanged / cancelled.
    """
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or _default_editor()

    if isinstance(obj, list):
        raw = json.dumps([t.model_dump() for t in obj], indent=2, default=str)
    else:
        raw = obj.model_dump_json(indent=2)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        subprocess.run([editor, tmp_path], check=True)  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args
        with open(tmp_path, encoding="utf-8") as f:
            edited = f.read()
        return edited if edited.strip() != raw.strip() else None
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        console.print(f"[red]Editor failed: {exc}. Keeping original.[/]")
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _default_editor() -> str:
    import platform
    return "notepad" if platform.system() == "Windows" else "nano"
