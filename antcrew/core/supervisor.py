"""
Supervisor — declarative LangGraph orchestration engine.

Converts a `flow` list into a compiled StateGraph, auto-detecting:
  - Entry nodes  (sources that are never targets  → wired from START)
  - Exit nodes   (targets that are never sources  → wired to END)
  - Conditional edges (tuples with a `when` key   → add_conditional_edges)
  - Interrupt points (agents with approval_required=True → interrupt_before)

Usage — sequential:
    supervisor = Supervisor(flow=[
        ("business_analyst", "pm"),
        ("pm", "backend_dev"),
    ])
    app = supervisor.build(agents)

Usage — conditional (condition key looked up in state["metadata"]):
    supervisor = Supervisor(flow=[
        ("pm", "backend_dev"),
        ("qa", "reviewer",    when="no_critical_bugs"),
        ("qa", "backend_dev", when="has_critical_bugs"),
    ])

Usage — parallel fan-out / fan-in:
    supervisor = Supervisor(flow=[
        ("pm", "backend_dev"),
        ("pm", "frontend_dev"),   # both start after pm
        ("backend_dev", "qa"),
        ("frontend_dev", "qa"),   # qa starts after BOTH finish
    ])
"""
from __future__ import annotations

import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState

# Must be set before the first checkpoint restore (checked at deserialize time)
os.environ.setdefault(
    "LANGGRAPH_ALLOWED_MSGPACK_MODULES",
    "antcrew.core.artifacts,antcrew.core.state",
)

# A flow edge: (src, dst) or (src, dst, when="condition_key")
_FlowEdge = tuple  # normalised to (src, dst, condition | None)


def _parse_edge(raw: tuple) -> _FlowEdge:
    if len(raw) == 2:
        return (raw[0], raw[1], None)
    if len(raw) == 3:
        return (raw[0], raw[1], raw[2])
    raise ValueError(
        f"Flow edge must be (src, dst) or (src, dst, when_key); got {raw!r}"
    )


class Supervisor:
    """
    Orchestration engine — converts a declarative flow list into a LangGraph
    StateGraph. Agnostic to which BaseAgent / BaseLLM / BaseChannel
    implementations you use.

    Conditional edges look up `when_key` in ``state["metadata"]``:
        agents["qa"].run() should set state["metadata"]["has_critical_bugs"] = True
        before the edge is evaluated.
    """

    def __init__(self, flow: list[tuple]) -> None:
        self._flow: list[_FlowEdge] = [_parse_edge(e) for e in flow]

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        agents: dict[str, BaseAgent],
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        interrupt_before: list[str] | None = None,
    ):
        """
        Compile and return a LangGraph application.

        Args:
            agents:           Dict mapping node names to BaseAgent instances.
            checkpointer:     Persistence backend (default: in-memory MemorySaver).
            interrupt_before: Override which nodes trigger a HITL pause.
                              When None, pauses before any agent whose
                              ``approval_required=True``.
        """
        if not self._flow:
            raise ValueError("Supervisor requires at least one flow edge.")

        graph = StateGraph(TeamState)

        # ── Collect node names ────────────────────────────────────────
        all_sources = {e[0] for e in self._flow}
        all_targets = {e[1] for e in self._flow}
        all_nodes   = all_sources | all_targets

        for name in all_nodes:
            if name in agents:
                graph.add_node(name, agents[name].run)

        # ── Entry / exit wiring ───────────────────────────────────────
        entry_nodes = all_sources - all_targets
        for node in entry_nodes:
            graph.add_edge(START, node)

        exit_nodes = all_targets - all_sources
        for node in exit_nodes:
            graph.add_edge(node, END)

        # ── Edges grouped by source ───────────────────────────────────
        by_source: dict[str, list[tuple[str, str | None]]] = {}
        for src, dst, cond in self._flow:
            by_source.setdefault(src, []).append((dst, cond))

        for src, targets in by_source.items():
            unconditional = [(dst, c) for dst, c in targets if c is None]
            conditional   = [(dst, c) for dst, c in targets if c is not None]

            for dst, _ in unconditional:
                graph.add_edge(src, dst)

            if conditional:
                cond_map: dict[str, str] = {cond: dst for dst, cond in conditional}
                possible_destinations = list(cond_map.values()) + [END]

                def _make_router(cm: dict[str, str]):
                    def _router(state: TeamState) -> str:
                        meta: dict = state.get("metadata") or {}
                        for key, target in cm.items():
                            if meta.get(key):
                                return target
                        return END
                    return _router

                graph.add_conditional_edges(
                    src,
                    _make_router(cond_map),
                    possible_destinations,
                )

        # ── Interrupt points ──────────────────────────────────────────
        if interrupt_before is None:
            interrupt_before = [
                name
                for name, agent in agents.items()
                if getattr(agent, "approval_required", False) and name in all_nodes
            ]

        return graph.compile(
            checkpointer=checkpointer or MemorySaver(),
            interrupt_before=interrupt_before,
        )
