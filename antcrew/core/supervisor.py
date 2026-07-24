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

Usage — parallel agents (concurrent execution with auto-merged state):
    from antcrew.core.supervisor import Supervisor, parallel

    supervisor = Supervisor(flow=[
        ("pm",            "coding"),
        ("coding",        "qa"),
    ])
    app = supervisor.build({
        "pm":     PMAgent(llm),
        "coding": parallel(BackendDevAgent(llm), FrontendDevAgent(llm)),
        "qa":     QAAgent(llm),
    })

    List outputs are concatenated; dict outputs are merged; scalar outputs
    use last-write.  Agents run concurrently via ThreadPoolExecutor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState

if TYPE_CHECKING:
    from antcrew.core.gates import BaseGate


# ---------------------------------------------------------------------------
# ParallelGroup
# ---------------------------------------------------------------------------

def _merge(base: dict, update: dict) -> dict:
    """Merge two partial state dicts returned by agents running in parallel."""
    result = dict(base)
    for key, value in update.items():
        if value is None:
            result.setdefault(key, None)
        elif key not in result or result[key] is None:
            result[key] = value
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


class ParallelGroup:
    """Runs multiple agents concurrently and merges their state updates.

    List fields are concatenated; dict fields are union-merged; scalar fields
    use last-write semantics.  Use the :func:`parallel` helper to construct.

    Example::

        from antcrew.core.supervisor import parallel
        supervisor = Supervisor(flow=[("pm", "coding"), ("coding", "qa")])
        app = supervisor.build({
            "pm":     pm_agent,
            "coding": parallel(backend_dev_agent, frontend_dev_agent),
            "qa":     qa_agent,
        })
    """

    def __init__(self, *agents: BaseAgent, name: str = "parallel_group") -> None:
        if not agents:
            raise ValueError("ParallelGroup requires at least one agent.")
        self.name = name
        self._agents: list[BaseAgent] = list(agents)

    def run(self, state: TeamState) -> dict:
        merged: dict = {}
        with ThreadPoolExecutor(max_workers=len(self._agents)) as pool:
            futures = {pool.submit(agent.run, state): agent for agent in self._agents}
            for future in as_completed(futures):
                partial = future.result()
                merged = _merge(merged, partial)
        return merged

    # Mirror enough of BaseAgent so Supervisor can treat this transparently.
    @property
    def approval_required(self) -> bool:
        return any(getattr(a, "approval_required", False) for a in self._agents)


def parallel(*agents: BaseAgent, name: str = "parallel_group") -> ParallelGroup:
    """Convenience constructor — wraps agents into a :class:`ParallelGroup`.

    The *name* is used as the LangGraph node name (must be unique in the flow).
    """
    return ParallelGroup(*agents, name=name)

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


def _make_gated_run(run_fn, gate, agent_name: str):
    """Wrap a node's run function so a gate is checked after each call."""
    from antcrew.core.gates import GateError as _GateError

    def _gated(state: TeamState) -> dict:
        result = run_fn(state)
        # Build the merged state the next node would see
        merged = {**dict(state), **result}
        gate_result = gate.check(merged)
        if not gate_result.passed:
            raise _GateError(gate.name, gate_result.message, gate_result.field)
        return result

    return _gated


class Supervisor:
    """
    Orchestration engine — converts a declarative flow list into a LangGraph
    StateGraph. Agnostic to which BaseAgent / BaseLLM / BaseChannel
    implementations you use.

    Conditional edges look up `when_key` in ``state["metadata"]``:
        agents["qa"].run() should set state["metadata"]["has_critical_bugs"] = True
        before the edge is evaluated.

    Verification gates::

        from antcrew.core.gates import NonEmptyGate, PythonSyntaxGate

        supervisor = Supervisor(
            flow=[("pm", "backend_dev"), ("backend_dev", "qa")],
            gates={
                "pm":          NonEmptyGate("prd"),
                "backend_dev": PythonSyntaxGate("code_artifacts"),
            },
        )
    """

    def __init__(
        self,
        flow: list[tuple],
        *,
        gates: "Optional[dict[str, BaseGate]]" = None,
    ) -> None:
        self._flow: list[_FlowEdge] = [_parse_edge(e) for e in flow]
        self._gates: dict = gates or {}

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

        from antcrew.core.events import _make_evented_run
        for name in all_nodes:
            if name in agents:
                run_fn = agents[name].run
                if name in self._gates:
                    run_fn = _make_gated_run(run_fn, self._gates[name], name)
                run_fn = _make_evented_run(run_fn, name)
                graph.add_node(name, run_fn)

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
