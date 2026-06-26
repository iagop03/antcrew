"""Flow-graph renderers for ``antcrew graph``.

Two output formats are supported:

- ``mermaid``  — Mermaid.js flowchart (paste into any Mermaid renderer or
                 GitHub markdown)
- ``ascii``    — plain-text box-and-arrow diagram suitable for terminals
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def render_mermaid(flow: list[tuple]) -> str:
    """Render a Supervisor flow as a Mermaid LR flowchart string.

    Conditional edges are shown as labelled arrows.  START and END nodes are
    rendered as rounded rectangles.

    Example output::

        graph LR
            __start__([ START ]) --> business_analyst
            business_analyst --> pm
            pm --> backend_dev
            backend_dev --> qa
            qa -->|no_critical_bugs| reviewer
            qa -->|has_critical_bugs| backend_dev
            reviewer --> __end__([ END ])
    """
    if not flow:
        return "graph LR\n    __start__([ START ]) --> __end__([ END ])"

    edges = _normalise(flow)
    all_srcs = {e[0] for e in edges}
    all_dsts = {e[1] for e in edges}
    all_nodes = all_srcs | all_dsts

    entry_nodes = all_srcs - all_dsts
    exit_nodes = all_dsts - all_srcs

    lines = ["graph LR"]

    def _label(name: str) -> str:
        if name == "__start__":
            return f"{name}([ START ])"
        if name == "__end__":
            return f"{name}([ END ])"
        return name

    for node in sorted(entry_nodes):
        lines.append(f"    {_label('__start__')} --> {_label(node)}")

    for src, dst, cond in edges:
        arrow = f" -->|{cond}|" if cond else " -->"
        lines.append(f"    {_label(src)}{arrow} {_label(dst)}")

    for node in sorted(exit_nodes):
        lines.append(f"    {_label(node)} --> {_label('__end__')}")

    return "\n".join(lines)


def render_ascii(flow: list[tuple]) -> str:
    """Render a Supervisor flow as a plain-text ASCII diagram.

    Linear chains are shown on one line; branching flows are rendered as a
    topologically-sorted node list with labelled arrows.

    Example (linear)::

        [START] ──▶ business_analyst ──▶ pm ──▶ backend_dev ──▶ [END]

    Example (branching)::

        [START]
          └──▶ business_analyst
                 └──▶ pm
                        ├──▶ backend_dev ──▶ qa
                        └──▶ frontend_dev ──▶ qa
                                               └──▶ reviewer ──▶ [END]
    """
    if not flow:
        return "[START] ──▶ [END]"

    edges = _normalise(flow)
    all_srcs = {e[0] for e in edges}
    all_dsts = {e[1] for e in edges}

    entry_nodes = sorted(all_srcs - all_dsts)
    exit_nodes = sorted(all_dsts - all_srcs)

    # Build adjacency list
    adj: dict[str, list[tuple[str, str | None]]] = {}
    for src, dst, cond in edges:
        adj.setdefault(src, []).append((dst, cond))

    # Detect linear chain (each node has exactly one in and one out)
    in_degree: dict[str, int] = {}
    for _, dst, _ in edges:
        in_degree[dst] = in_degree.get(dst, 0) + 1

    is_linear = (
        len(entry_nodes) == 1
        and len(exit_nodes) == 1
        and all(len(v) == 1 for v in adj.values())
        and all(in_degree.get(n, 0) <= 1 for n in (all_srcs | all_dsts))
    )

    if is_linear:
        return _render_linear(entry_nodes[0], exit_nodes[0], adj)

    return _render_dag(entry_nodes, exit_nodes, adj)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _normalise(flow: list[tuple]) -> list[tuple[str, str, str | None]]:
    result = []
    for edge in flow:
        src = str(edge[0])
        dst = str(edge[1])
        cond = str(edge[2]) if len(edge) >= 3 and edge[2] is not None else None
        result.append((src, dst, cond))
    return result


def _render_linear(start: str, end: str, adj: dict) -> str:
    chain = [start]
    current = start
    while current in adj:
        nxt, _ = adj[current][0]
        chain.append(nxt)
        current = nxt

    arrow = " ──▶ "
    nodes = ["[START]"] + chain + ["[END]"]
    return arrow.join(nodes)


def _render_dag(
    entry_nodes: list[str],
    exit_nodes: list[str],
    adj: dict[str, list[tuple[str, str | None]]],
) -> str:
    """Topological-sort render for branching / conditional graphs.

    Produces a compact edge list ordered by topology, with START/END markers.
    """
    all_nodes: set[str] = set()
    for src, targets in adj.items():
        all_nodes.add(src)
        for dst, _ in targets:
            all_nodes.add(dst)

    in_deg: dict[str, int] = {n: 0 for n in all_nodes}
    for _, targets in adj.items():
        for dst, _ in targets:
            in_deg[dst] = in_deg.get(dst, 0) + 1

    queue = sorted(n for n in all_nodes if in_deg[n] == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for dst, _ in adj.get(node, []):
            in_deg[dst] -= 1
            if in_deg[dst] == 0:
                queue.append(dst)
                queue.sort()

    # Add any nodes not reached by topo sort (cycles / sinks)
    for node in sorted(all_nodes):
        if node not in order:
            order.append(node)

    max_src = max((len(n) for n in all_nodes), default=0)
    lines: list[str] = []
    for node in entry_nodes:
        lines.append(f"  [START] ──▶ {node}")

    for node in order:
        targets = adj.get(node, [])
        if not targets:
            continue
        for dst, cond in targets:
            src_padded = node.ljust(max_src)
            label = f"  [{cond}]" if cond else ""
            lines.append(f"  {src_padded} ──▶ {dst}{label}")

    for node in exit_nodes:
        lines.append(f"  {node.ljust(max_src)} ──▶ [END]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in team flow registry
# ---------------------------------------------------------------------------

def _get_builtin_flow(team_name: str) -> list[tuple] | None:
    """Return the default flow for a named built-in team, or None."""
    name = team_name.lower().replace("-", "_").replace(" ", "_")
    try:
        if name in ("dev", "dev_team"):
            from antcrew.teams.dev_team import _DEFAULT_FLOW
            return list(_DEFAULT_FLOW)
        if name in ("fullstack", "fullstack_team", "full_stack", "full_stack_team"):
            from antcrew.teams.fullstack_team import _DEFAULT_FLOW
            return list(_DEFAULT_FLOW)
        if name in ("research", "research_team"):
            from antcrew.teams.research_team import _DEFAULT_FLOW
            return list(_DEFAULT_FLOW)
        if name in ("content", "content_team"):
            from antcrew.teams.content_team import _DEFAULT_FLOW
            return list(_DEFAULT_FLOW)
    except ImportError:
        return None
    return None
