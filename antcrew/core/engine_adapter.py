"""EngineTeamAdapter — wraps EngineLoop behind the team.run() interface.

Created when ``team: engine`` (or ``execution: engine``) is specified in a
YAML config.  Uses a MemoryStore and the standard capability registry so the
engine loop can be driven from ``antcrew run --config team.yaml`` without
needing the full engine CLI.

Example YAML::

    team: engine
    model: claude
    max_cost_usd: 2.0
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from antcrew_engine.models.base import BaseLLM


class EngineTeamAdapter:
    """Thin adapter: exposes team.run() over an EngineLoop.

    The adapter uses:
    - ``MemoryStore``   — in-memory artifact store (lost after run)
    - Standard capability registry (Architect → TaskPlanner → CodeGenerator
      → TestGenerator → TestRunner → BugFixer → CodeReviewer → DocGenerator)
    - Standard validators (AllTasksCompleted, TestsPass, CodeReviewed, …)
    - Default goal: requirements → architecture → tasks → implementation →
      deps → tests pass → code reviewed → docs
    """

    def __init__(
        self,
        llm: "BaseLLM",
        *,
        max_cost_usd: Optional[float] = None,
        output_dir: Optional[str] = None,
        max_iterations: int = 50,
    ) -> None:
        self._llm = llm
        self._max_cost_usd = max_cost_usd
        self._output_dir = output_dir
        self._max_iterations = max_iterations

    # ------------------------------------------------------------------
    # Team interface
    # ------------------------------------------------------------------

    def run(self, request: str, *, thread_id: str = "default") -> dict:
        """Run the engine loop for *request* and return a result dict."""
        from antcrew.cli.engine_cmd import (
            _build_goal,
            _build_registry,
            _build_validators,
        )
        from antcrew.engine import (
            EngineLoop,
            EventLog,
            MemoryStore,
        )

        store = MemoryStore()
        event_log = EventLog()
        registry = _build_registry(self._llm)
        validators = _build_validators()
        goal = _build_goal(request, (), [], full=True)

        loop = EngineLoop(
            registry,
            validators,
            event_log,
            max_iterations=self._max_iterations,
            max_cost_usd=self._max_cost_usd,
        )

        try:
            final_state = loop.run(store, goal)
            satisfied = [str(c) for c in final_state.satisfied]
            artifacts = {
                art_id: art.content
                for art_id, art in store._artifacts.items()  # type: ignore[attr-defined]
            } if hasattr(store, "_artifacts") else {}
            return {
                "status": "done",
                "request": request,
                "thread_id": thread_id,
                "satisfied_conditions": satisfied,
                "artifacts": artifacts,
            }
        except Exception as exc:
            return {
                "status": "error",
                "request": request,
                "thread_id": thread_id,
                "error": str(exc),
            }
