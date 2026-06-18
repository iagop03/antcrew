"""
Format AntCrew artifacts as Telegram HTML messages.
Telegram limits: 4096 chars per message, HTML parse mode.
"""
from __future__ import annotations

from antcrew.core.artifacts import PRD, Ticket, CodeArtifact

_PRIORITY_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🚨"}

_MAX = 4000  # leave margin below Telegram's 4096 limit


def format_prd(prd: PRD) -> str:
    parts = [
        f"<b>📋 PRD: {prd.title}</b>",
        "",
        prd.summary,
        "",
    ]

    def _section(title: str, items: list[str], bullet: str = "•") -> list[str]:
        if not items:
            return []
        return [f"<b>{title}:</b>", *[f"  {bullet} {i}" for i in items], ""]

    parts += _section("Goals", prd.goals)
    parts += _section("Out of scope", prd.out_of_scope)
    parts += _section("Functional requirements", prd.functional_requirements)
    parts += _section("Non-functional requirements", prd.non_functional_requirements)
    parts += _section("Open questions", prd.open_questions, "❓")

    return "\n".join(parts)[:_MAX]


def format_tickets(tickets: list[Ticket]) -> str:
    parts = [f"<b>🎫 Tickets ({len(tickets)})</b>", ""]
    for t in tickets:
        emoji = _PRIORITY_EMOJI.get(t.priority.value, "⚪")
        parts.append(f"{emoji} <b>{t.id}:</b> {t.title}")
        parts.append(f"<i>{t.description}</i>")
        for c in t.acceptance_criteria:
            parts.append(f"   ✓ {c}")
        parts.append("")
    return "\n".join(parts)[:_MAX]


def format_code_artifact(artifact: CodeArtifact) -> list[str]:
    """
    Returns one or more message strings split at Telegram's 4096-char limit.
    Uses <pre><code> blocks for syntax highlighting in supported clients.
    """
    header = f"<b>💻 {artifact.file_path}</b>\n<i>{artifact.description}</i>"
    lang = artifact.language or ""
    code = artifact.content
    chunks: list[str] = []

    while True:
        tag_open = f'<pre><code class="language-{lang}">'
        tag_close = "</code></pre>"
        available = _MAX - len(header) - len(tag_open) - len(tag_close) - 2
        snippet, code = code[:available], code[available:]
        chunks.append(f"{header}\n\n{tag_open}{snippet}{tag_close}")
        if not code:
            break
        header = f"<b>💻 {artifact.file_path}</b> <i>(continued)</i>"

    return chunks
