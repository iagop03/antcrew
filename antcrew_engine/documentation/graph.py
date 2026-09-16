"""DocumentationGraph: tracks relationships between documents.

Uses NetworkX when available; falls back to a simple adjacency dict.
"""
from __future__ import annotations

from typing import Any


class DocumentationGraph:
    """Directed graph of document relationships."""

    def __init__(self) -> None:
        try:
            import networkx as nx
            self._g: Any = nx.DiGraph()
            self._nx = nx
        except ImportError:
            self._g = None
            self._nx = None
            self._adj: dict[str, list[dict]] = {}  # doc_id → [{to, relation}]
            self._node_meta: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def add_node(self, doc_id: str, metadata: dict) -> None:
        if self._g is not None:
            self._g.add_node(doc_id, **metadata)
        else:
            self._node_meta[doc_id] = metadata
            self._adj.setdefault(doc_id, [])

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def add_edge(self, from_doc: str, to_doc: str, relation: str = "related_to") -> None:
        if self._g is not None:
            for node in (from_doc, to_doc):
                if not self._g.has_node(node):
                    self._g.add_node(node)
            self._g.add_edge(from_doc, to_doc, relation=relation)
        else:
            for node in (from_doc, to_doc):
                self._adj.setdefault(node, [])
                self._node_meta.setdefault(node, {})
            self._adj[from_doc].append({"to": to_doc, "relation": relation})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_neighbors(self, doc_id: str, relation: str | None = None) -> list[str]:
        if self._g is not None:
            return [
                v
                for _, v, data in self._g.out_edges(doc_id, data=True)
                if relation is None or data.get("relation") == relation
            ]
        edges = self._adj.get(doc_id, [])
        return [e["to"] for e in edges if relation is None or e.get("relation") == relation]

    def get_graph_for_doc(self, doc_id: str, depth: int = 2) -> dict:
        """Return a subgraph dict (nodes list + edges list) centred on doc_id."""
        nodes: set[str] = {doc_id}
        edges: list[tuple[str, str, str]] = []
        frontier = {doc_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for n in frontier:
                for neighbor in self.get_neighbors(n):
                    edges.append((n, neighbor, "related_to"))
                    if neighbor not in nodes:
                        nodes.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
        return {"nodes": list(nodes), "edges": edges}

    def detect_cycles(self) -> list[list[str]]:
        """Return all simple cycles in the graph (empty list if networkx not available)."""
        if self._g is not None and self._nx is not None:
            return list(self._nx.simple_cycles(self._g))
        return []
