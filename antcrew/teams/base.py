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
from typing import TYPE_CHECKING

from antcrew.core.artifacts import (
    PRD, Ticket, CodeArtifact, TestArtifact,
    CodeReview, DevOpsArtifact, DocumentationArtifact,
    ResearchDocument, ContentPiece,
)

if TYPE_CHECKING:
    from antcrew.core.agent import BaseAgent

log = logging.getLogger(__name__)


def _sync_run(coro):
    """Run an async coroutine synchronously, safe in any context.

    Works in plain scripts (no event loop), Jupyter notebooks, and FastAPI
    handlers (all of which have a running event loop that asyncio.run() would
    refuse to nest into).
    """
    try:
        asyncio.get_running_loop()
        # Already inside a running event loop — delegate to a fresh thread so
        # we don't block the loop and can still use asyncio.run() there.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop — just use asyncio.run() directly.
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
    # ResearchTeam uses "writer" as alias for copywriter
    "devops":           ("devops_artifacts",  DevOpsArtifact),
    "doc_writer":       ("doc_artifacts",     DocumentationArtifact),
    "writer":           ("content_piece",     ContentPiece),
    "idea":             ("content_piece",     ContentPiece),
    "copywriter":       ("content_piece",     ContentPiece),
    "editor":           ("content_piece",     ContentPiece),
}


class InteractiveMixin:
    """
    Mixin that adds run_interactive() and run_async() to any team class.

    Requires the host class to define:
        self._agents:    dict[str, BaseAgent]
        self._supervisor: Supervisor
        self._initial_state(request: str) -> TeamState
    """

    _agents: dict[str, "BaseAgent"]

    async def run_async(self, request: str, *, thread_id: str = "default") -> dict:
        """Non-blocking version of run() — safe to await from FastAPI or any async context.

        Runs the synchronous LangGraph pipeline in a thread-pool executor so it
        does not block the event loop.  All kwargs accepted by run() are forwarded.

        Example (FastAPI):
            @app.post("/run")
            async def create_run(body: RunRequest):
                state = await team.run_async(body.request)
                return state
        """
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

        _rc = _RC()

        approval_nodes = [
            name for name, agent in self._agents.items()
            if getattr(agent, "approval_required", False)
        ]
        all_node_names = list(self._agents.keys())
        interrupt_nodes = approval_nodes or all_node_names[1:]

        app = self._supervisor.build(self._agents, interrupt_before=interrupt_nodes)
        config = {"configurable": {"thread_id": thread_id}}

        _rc.print("\n[bold green]AntCrew[/] — starting pipeline.\n")
        app.invoke(self._initial_state(request), config=config)  # type: ignore[attr-defined]

        while True:  # outer: one iteration per pipeline step
            snapshot = app.get_state(config)
            if not snapshot.next:
                _rc.print("\n[bold green]Done![/]\n")
                break

            prev_agent_name = snapshot.values.get("current_agent", "")
            agent = self._agents.get(prev_agent_name)
            channel = getattr(agent, "channel", None) if agent else None

            state_key, _ = AGENT_ARTIFACT.get(prev_agent_name, ("", None))
            artifact = snapshot.values.get(state_key) if state_key else None

            options = getattr(agent, "response_options", None) or ["approve", "edit", "reject"]
            pipeline_stopped = False

            while True:  # inner: conversational refinement
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

                if decision == "edit" and result.get("edited"):
                    self._apply_edit(app, config, prev_agent_name, result["edited"])
                    snapshot = app.get_state(config)
                    artifact = snapshot.values.get(state_key) if state_key else None
                    break  # treat manual edit as implicit approve

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

            next_node = snapshot.next[0]
            _rc.print(f"\n[bold]{next_node}[/] running…")
            app.invoke(None, config=config)

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
