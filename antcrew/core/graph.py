"""
Graph construction utilities.

`build_pipeline` creates a linear agent pipeline (default for DevTeam and other
sequential team templates). Future versions will add `build_supervised_graph`
for dynamic LLM-driven routing between agents.
"""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState


def build_pipeline(
    agents: list[BaseAgent],
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    interrupt_before: list[str] | None = None,
):
    """
    Build a sequential LangGraph pipeline from an ordered list of agents.

    Connects agents left-to-right:
        START → agents[0] → agents[1] → … → agents[-1] → END

    Args:
        agents:           Ordered list of agents. Each must have a unique ``name``.
        checkpointer:     Persistence backend (default: in-memory MemorySaver).
        interrupt_before: Node names where the graph pauses for human review.

    Returns:
        A compiled LangGraph application ready to call with ``.invoke()``.
    """
    if not agents:
        raise ValueError("build_pipeline requires at least one agent.")

    graph = StateGraph(TeamState)

    for agent in agents:
        graph.add_node(agent.name, agent.run)

    names = [a.name for a in agents]
    graph.add_edge(START, names[0])
    for i in range(len(names) - 1):
        graph.add_edge(names[i], names[i + 1])
    graph.add_edge(names[-1], END)

    return graph.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_before=interrupt_before or [],
    )
