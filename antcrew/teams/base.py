"""
Shared HITL mixin for all team classes.

Any team that sets self._agents and self._supervisor (and provides
_initial_state / run) can inherit InteractiveMixin to get
run_interactive() with the full conversational loop for free.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import logging
import re
import time
from typing import TYPE_CHECKING

from antcrew.core.artifacts import (
    PRD, Ticket, CodeArtifact, TestArtifact,
    CodeReview, DevOpsArtifact, DocumentationArtifact,
    ResearchDocument, ContentPiece,
)

if TYPE_CHECKING:
    from antcrew.core.agent import BaseAgent

log = logging.getLogger(__name__)

_FILE_PATH_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _sync_run(coro):
    """Run an async coroutine synchronously, safe in any context."""
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


# Maps every agent name to (state_key, Pydantic class used for manual edits)
AGENT_ARTIFACT: dict[str, tuple[str, type | None]] = {
    "business_analyst": ("prd",               PRD),
    "pm":               ("tickets",           Ticket),
    "backend_dev":      ("code_artifacts",    CodeArtifact),
    "frontend_dev":     ("code_artifacts",    CodeArtifact),
    "qa":               ("test_artifacts",    TestArtifact),
    "reviewer":         ("review",            CodeReview),
    "researcher":       ("research_document", ResearchDocument),
    "devops":           ("devops_artifacts",  DevOpsArtifact),
    "doc_writer":       ("doc_artifacts",     DocumentationArtifact),
    "writer":           ("content_piece",     ContentPiece),
    "idea":             ("content_piece",     ContentPiece),
    "copywriter":       ("content_piece",     ContentPiece),
    "editor":           ("content_piece",     ContentPiece),
}

# Friendly labels shown in the progress panel instead of raw agent names.
_AGENT_LABEL = {
    "business_analyst": "Business Analyst",
    "pm":               "Product Manager",
    "sprint_planner":   "Sprint Planner",
    "backend_dev":      "Backend Dev",
    "frontend_dev":     "Frontend Dev",
    "qa":               "QA",
    "reviewer":         "Reviewer",
    "devops":           "DevOps",
    "doc_writer":       "Doc Writer",
}


class _ProgressPanel:
    """Tracks agent execution and renders a Rich Panel for Live display."""

    def __init__(self) -> None:
        self._completed: list[tuple[str, float, str]] = []  # (name, secs, hint)
        self._cur = ""
        self._cur_start = 0.0
        self._cur_hint = ""
        self._total_start = time.monotonic()
        self._buf = ""

    # ------------------------------------------------------------------
    # Called from on_token
    # ------------------------------------------------------------------

    def update(self, token: str, agent_name: str) -> None:
        if agent_name and agent_name != self._cur:
            self._flush()
            self._cur = agent_name
            self._cur_start = time.monotonic()
            self._cur_hint = ""
            self._buf = ""

        self._buf = (self._buf + token)[-800:]
        m = _FILE_PATH_RE.search(self._buf)
        if m:
            self._cur_hint = m.group(1)

    def flush_current(self) -> None:
        """Call after app.invoke() returns to finalise the last agent."""
        self._flush()

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self):
        from rich.panel import Panel
        from rich.table import Table

        t = Table.grid(padding=(0, 2))
        t.add_column(width=2)    # icon
        t.add_column(width=22)   # agent label
        t.add_column(width=7)    # time
        t.add_column()           # hint

        for name, secs, hint in self._completed:
            label = _AGENT_LABEL.get(name, name)
            t.add_row(
                "[green]✓[/green]",
                f"[dim]{label}[/dim]",
                f"[dim]{_fmt_time(secs)}[/dim]",
                f"[dim]{hint}[/dim]" if hint else "",
            )

        if self._cur:
            elapsed = time.monotonic() - self._cur_start
            label = _AGENT_LABEL.get(self._cur, self._cur)
            t.add_row(
                "[bold yellow]⠸[/bold yellow]",
                f"[bold cyan]{label}[/bold cyan]",
                f"[yellow]{_fmt_time(elapsed)}[/yellow]",
                f"[dim]↳ {self._cur_hint}[/dim]" if self._cur_hint else "[dim]working…[/dim]",
            )

        total = time.monotonic() - self._total_start
        avg = self._avg_per_agent()
        subtitle = f"[dim]elapsed {_fmt_time(total)}"
        if avg and self._cur:
            subtitle += f"  ·  avg {_fmt_time(avg)}/agent"
        subtitle += "[/dim]"

        return Panel(t, title="[bold green]AntCrew[/bold green]", subtitle=subtitle, border_style="green", padding=(0, 1))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        if self._cur:
            secs = time.monotonic() - self._cur_start
            hint = self._cur_hint
            self._completed.append((self._cur, secs, hint))
            self._cur = ""
            self._cur_hint = ""
            self._buf = ""

    def _avg_per_agent(self) -> float:
        if not self._completed:
            return 0.0
        return sum(s for _, s, _ in self._completed) / len(self._completed)


class InteractiveMixin:
    """
    Mixin that adds run_interactive() and run_async() to any team class.

    Requires the host class to define:
        self._agents:    dict[str, BaseAgent]
        self._supervisor: Supervisor
        self._initial_state(request: str) -> TeamState
    """

    _agents: dict[str, "BaseAgent"]

    def _build_agent_map(self) -> dict:
        """Return the agent dict to pass to Supervisor.build().

        Override in subclasses (e.g., DevTeam) to inject per-agent state
        transformations such as role-scoped KB context via _KBProxy.
        Default: return self._agents unchanged.
        """
        return self._agents

    def _unique_llms(self) -> "list":
        """Return one instance per distinct LLM object used across all agents.

        Recurses into ParallelGroup instances so their inner agents' LLMs are
        included in cost aggregation and trace propagation.
        """
        from antcrew.core.supervisor import ParallelGroup

        seen: dict[int, object] = {}

        def _collect(agent) -> None:
            if isinstance(agent, ParallelGroup):
                for inner in agent._agents:
                    _collect(inner)
            else:
                llm = getattr(agent, "llm", None)
                if llm is not None and id(llm) not in seen:
                    seen[id(llm)] = llm

        for agent in self._agents.values():
            _collect(agent)
        return list(seen.values())

    async def run_async(self, request: str, *, thread_id: str = "default") -> dict:
        """Non-blocking version of run() — safe to await from FastAPI or any async context."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.run, request, thread_id=thread_id),
        )

    def run_interactive(self, request: str, *, thread_id: str = "default"):
        """
        Execute the pipeline with HITL pause after every agent.

        Decision flow per agent:
        - approve   → proceed to next node.
        - reject    → stop the pipeline.
        - edit      → apply manual JSON edit, then proceed.
        - feedback  → if agent.conversational, call agent.refine(feedback),
                      update state, and show the revised artifact again.
        """
        from antcrew.integrations.console import _display_artifact, _prompt_decision
        from antcrew.console import edit_artifact_in_editor
        from rich.console import Console as _RC
        from rich.live import Live

        _rc = _RC()

        approval_nodes = [
            name for name, agent in self._agents.items()
            if getattr(agent, "approval_required", False)
        ]
        all_node_names = list(self._agents.keys())
        interrupt_nodes = approval_nodes or all_node_names[1:]

        app = self._supervisor.build(self._build_agent_map(), interrupt_before=interrupt_nodes)
        config = {"configurable": {"thread_id": thread_id}}

        _llms = self._unique_llms()
        progress = _ProgressPanel()
        _live_ref: list = [None]

        def _make_on_token(llm_ref):
            def _on_token(token: str) -> None:
                agent_name = getattr(llm_ref, "current_agent", "")
                progress.update(token, agent_name)
                if _live_ref[0] is not None:
                    _live_ref[0].update(progress.render())
            return _on_token

        def _invoke(state_or_none):
            for _llm in _llms:
                _llm.on_token = _make_on_token(_llm)
            with Live(progress.render(), refresh_per_second=4, console=_rc) as live:
                _live_ref[0] = live
                app.invoke(state_or_none, config=config)
                _live_ref[0] = None
            for _llm in _llms:
                _llm.on_token = None
            progress.flush_current()

        _rc.print(f"\n[bold green]AntCrew[/bold green] — [dim]{request[:80]}[/dim]\n")
        _invoke(self._initial_state(request))

        while True:
            snapshot = app.get_state(config)
            if not snapshot.next:
                _rc.print("\n[bold green]Done![/bold green]\n")
                break

            prev_agent_name = snapshot.values.get("current_agent", "")
            agent = self._agents.get(prev_agent_name)
            channel = getattr(agent, "channel", None) if agent else None

            state_key, _ = AGENT_ARTIFACT.get(prev_agent_name, ("", None))
            artifact = snapshot.values.get(state_key) if state_key else None

            options = getattr(agent, "response_options", None) or ["approve", "edit", "reject"]
            pipeline_stopped = False

            while True:
                if channel is not None:
                    result = _sync_run(
                        channel.send_for_review(artifact, prev_agent_name, thread_id, options)
                    )
                else:
                    _display_artifact(artifact, prev_agent_name)
                    decision_str = _prompt_decision(prev_agent_name, options)
                    if decision_str == "edit":
                        edited = edit_artifact_in_editor(artifact)
                        result = {"decision": "edit", "edited": edited, "feedback": None}
                    elif decision_str in ("approve", "reject"):
                        result = {"decision": decision_str, "edited": None, "feedback": None}
                    else:
                        result = {"decision": "feedback", "feedback": decision_str, "edited": None}

                decision = result["decision"]

                if decision == "reject":
                    _rc.print(f"[red]Pipeline stopped after {prev_agent_name}.[/]")
                    pipeline_stopped = True
                    break

                if decision == "approve":
                    break

                if decision == "fix":
                    # Route back to backend_dev with fix_requested flag in metadata.
                    current_meta = snapshot.values.get("metadata") or {}
                    fix_count = current_meta.get("fix_attempts", 0) + 1
                    app.update_state(config, {
                        "metadata": {
                            **current_meta,
                            "reviewer_fix_requested": True,
                            "fix_attempts": fix_count,
                        }
                    })
                    _rc.print(
                        f"\n[dim]↩ Routing back to backend_dev for targeted fixes "
                        f"(attempt {fix_count}).[/dim]\n"
                    )
                    break

                if decision == "edit" and result.get("edited"):
                    self._apply_edit(app, config, prev_agent_name, result["edited"])
                    snapshot = app.get_state(config)
                    artifact = snapshot.values.get(state_key) if state_key else None
                    break

                if decision == "feedback":
                    feedback = result.get("feedback") or ""
                    if getattr(agent, "conversational", False) and feedback:
                        try:
                            refined = agent.refine(snapshot.values, artifact, feedback)
                            if refined:
                                app.update_state(config, refined)
                                snapshot = app.get_state(config)
                                artifact = snapshot.values.get(state_key) if state_key else None
                                _rc.print(
                                    f"\n[dim]↻ {prev_agent_name} revised — "
                                    "review the updated output.[/dim]\n"
                                )
                        except Exception as exc:
                            _rc.print(f"[yellow]⚠ Refinement failed: {exc}[/yellow]")
                    else:
                        _rc.print(
                            f"[dim]{prev_agent_name} is not in conversational mode — "
                            "feedback noted but no change applied.[/dim]"
                        )

            if pipeline_stopped:
                break

            _invoke(None)

        return app.get_state(config).values

    def _apply_edit(self, app, config: dict, agent_name: str, edited_json: str) -> None:
        state_key, cls = AGENT_ARTIFACT.get(agent_name, ("", None))
        if not state_key:
            return
        try:
            data = json.loads(edited_json)
        except json.JSONDecodeError:
            log.warning("Invalid JSON in edit for %s — skipping", agent_name)
            return
        if isinstance(data, list) and cls:
            app.update_state(config, {state_key: [cls.model_validate(item) for item in data]})
        elif isinstance(data, dict) and cls:
            app.update_state(config, {state_key: cls.model_validate(data)})
        else:
            app.update_state(config, {state_key: data})
