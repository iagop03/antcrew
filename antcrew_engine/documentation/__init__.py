"""antcrew documentation module — flexible, configuration-driven doc management.

Quickstart::

    from antcrew_engine.documentation import DocumentationManager

    mgr = DocumentationManager(schema_path="schema.yaml")
    mgr.bulk_upload("./documentation")

    results = mgr.search("authentication requirements", top_k=5)
    context = mgr.get_context_for_agent("BackendDev", "JWT login flow")
"""
from .graph import DocumentationGraph
from .index import DocumentationIndex, SearchResult
from .manager import DocumentationManager
from .schema import DocumentationSchemaRegistry, DocumentTypeConfig, PathRule, QueryHint

__all__ = [
    "DocumentationManager",
    "DocumentationSchemaRegistry",
    "DocumentTypeConfig",
    "QueryHint",
    "PathRule",
    "DocumentationIndex",
    "SearchResult",
    "DocumentationGraph",
]
