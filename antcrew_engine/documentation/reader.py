"""DocumentationReader: structured access to workspace documents.

Provides three operations for agents and tools:
  - list_docs()    → catalogue of available documents with metadata
  - search()       → semantic search over indexed content
  - read()         → full text of a specific document by ID

Usage::

    from antcrew_engine.documentation import DocumentationManager, DocumentationReader

    mgr = DocumentationManager()
    mgr.storage = my_storage
    mgr.index_from_storage()

    reader = DocumentationReader(mgr)
    docs = reader.list_docs(doc_type="srs")
    results = reader.search("COBOL WORKING-STORAGE fields for claims")
    full = reader.read("srs/claims-processing.md")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .manager import DocumentationManager


@dataclass
class DocSummary:
    doc_id: str
    doc_type: Optional[str] = None
    source_file: Optional[str] = None
    size_bytes: Optional[int] = None
    uploaded_at: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "source_file": self.source_file,
            "size_bytes": self.size_bytes,
            "uploaded_at": self.uploaded_at,
        }


@dataclass
class SearchResult:
    doc_id: str
    doc_type: Optional[str]
    content: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "content": self.content,
            "score": self.score,
        }


class DocumentationReader:
    """Read-only facade over a DocumentationManager for agent tool use."""

    def __init__(self, manager: "DocumentationManager") -> None:
        self._mgr = manager

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_docs(self, doc_type: Optional[str] = None) -> list[DocSummary]:
        """Return a catalogue of all indexed documents.

        Args:
            doc_type: when given, filters by exact doc_type match.
        """
        summaries: list[DocSummary] = []
        try:
            doc_ids = self._mgr.storage.list_documents()
        except Exception:
            return summaries

        for doc_id in doc_ids:
            meta: dict = {}
            if hasattr(self._mgr.storage, "load_metadata"):
                try:
                    meta = self._mgr.storage.load_metadata(doc_id) or {}
                except Exception:
                    pass

            # Also check in-memory index populated by index_from_storage()
            in_mem = self._mgr._documents.get(doc_id, {})
            resolved_type = meta.get("doc_type") or in_mem.get("doc_type")

            if doc_type and resolved_type != doc_type:
                continue

            summaries.append(DocSummary(
                doc_id=doc_id,
                doc_type=resolved_type,
                source_file=meta.get("source_file"),
                size_bytes=meta.get("size_bytes"),
                uploaded_at=meta.get("uploaded_at"),
            ))
        return summaries

    def list_docs_as_text(self, doc_type: Optional[str] = None) -> str:
        """Return a markdown table of documents for LLM context."""
        docs = self.list_docs(doc_type)
        if not docs:
            return "No documents available."
        lines = ["| Doc ID | Type | File |", "|--------|------|------|"]
        for d in docs:
            lines.append(f"| {d.doc_id} | {d.doc_type or '—'} | {d.source_file or '—'} |")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        doc_type: Optional[str] = None,
    ) -> list[SearchResult]:
        """Semantic search over indexed documents.

        Returns up to *top_k* results sorted by relevance.
        """
        try:
            raw = self._mgr.search(query, doc_type=doc_type, top_k=top_k)
        except Exception:
            return []

        results: list[SearchResult] = []
        for r in raw:
            doc_id = r.get("id") or r.get("doc_id", "")
            results.append(SearchResult(
                doc_id=doc_id,
                doc_type=r.get("doc_type") or r.get("metadata", {}).get("doc_type"),
                content=r.get("content", ""),
                score=float(r.get("score", 0.0)),
                metadata=r.get("metadata", {}),
            ))
        return results

    def search_as_text(self, query: str, top_k: int = 5, doc_type: Optional[str] = None) -> str:
        """Return search results as formatted text for LLM context."""
        results = self.search(query, top_k=top_k, doc_type=doc_type)
        if not results:
            return f"No documents found for query: {query!r}"
        parts = [f"## Search results for: {query!r}\n"]
        for i, r in enumerate(results, 1):
            parts.append(f"### {i}. {r.doc_id} ({r.doc_type or 'unknown type'})")
            parts.append(r.content[:800])
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(self, doc_id: str) -> str:
        """Return the full text content of a document by ID.

        Raises FileNotFoundError when the document is not found.
        """
        try:
            content_bytes = self._mgr.storage.load(doc_id)
        except Exception as exc:
            raise FileNotFoundError(f"Document not found: {doc_id}") from exc

        # Try utf-8; fall back to latin-1 for COBOL/mainframe files
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return content_bytes.decode("latin-1", errors="replace")

    def read_with_header(self, doc_id: str) -> str:
        """Return full content with a metadata header for LLM context."""
        meta: dict = {}
        if hasattr(self._mgr.storage, "load_metadata"):
            try:
                meta = self._mgr.storage.load_metadata(doc_id) or {}
            except Exception:
                pass
        content = self.read(doc_id)
        header_parts = [f"# {meta.get('source_file', doc_id)}"]
        if meta.get("doc_type"):
            header_parts.append(f"**Type:** {meta['doc_type']}")
        if meta.get("uploaded_at"):
            header_parts.append(f"**Uploaded:** {meta['uploaded_at']}")
        header = "\n".join(header_parts)
        return f"{header}\n\n---\n\n{content}"
