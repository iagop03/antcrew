"""DocumentationManager: main orchestrator for the documentation system."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .graph import DocumentationGraph
from .index import DocumentationIndex
from .schema import DocumentationSchemaRegistry

if TYPE_CHECKING:
    from .storage.base import BaseStorage

# Maps parser alias → canonical name
_PARSER_ALIASES: dict[str, str] = {
    "word_doc": "docx",
    "word": "docx",
    "md": "markdown",
    "txt": "text",
}

# Maps file extension → default parser
_EXT_TO_PARSER: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".docx": "docx",
    ".doc": "docx",
    ".pdf": "pdf",
    ".json": "jira",
    ".cbl": "cobol",
    ".cob": "cobol",
    ".cpy": "cobol",
    ".copy": "cobol",
}


def _get_parser(parser_name: str):
    canonical = _PARSER_ALIASES.get(parser_name, parser_name)
    if canonical == "markdown":
        from .parsers.markdown import MarkdownParser
        return MarkdownParser()
    if canonical == "docx":
        from .parsers.docx import DocxParser
        return DocxParser()
    if canonical == "pdf":
        from .parsers.pdf import PDFParser
        return PDFParser()
    if canonical == "jira":
        from .parsers.jira import JiraTicketParser
        return JiraTicketParser()
    if canonical == "cobol":
        from .parsers.cobol import CobolParser
        return CobolParser()
    from .parsers.text import TextParser
    return TextParser()


class DocumentationManager:
    """Main orchestrator: schema, storage, parsing, indexing, and graph."""

    def __init__(
        self,
        schema_path: str | None = None,
        storage_type: str = "local",
        storage_config: dict | None = None,
        cache_dir: str = "./.antcrew_docs",
    ) -> None:
        self.schema = DocumentationSchemaRegistry(schema_path)
        self.storage: "BaseStorage" = self._init_storage(storage_type, storage_config or {})
        self.index = DocumentationIndex(cache_dir)
        self.graph = DocumentationGraph()
        self._documents: dict[str, dict] = {}  # doc_id → {doc_type, category, metadata}

    # ------------------------------------------------------------------
    # Storage factory
    # ------------------------------------------------------------------

    def _init_storage(self, storage_type: str, config: dict) -> "BaseStorage":
        if storage_type == "git":
            from .storage.git import GitStorage
            return GitStorage(root=config.get("path", "./documentation"))
        if storage_type == "s3":
            from .storage.s3 import S3Storage
            return S3Storage(
                bucket=config["bucket"],
                prefix=config.get("prefix", ""),
                region=config.get("region", "us-east-1"),
                aws_access_key_id=config.get("aws_access_key_id"),
                aws_secret_access_key=config.get("aws_secret_access_key"),
            )
        from .storage.local import LocalFileStorage
        return LocalFileStorage(root=config.get("path", "./documentation"))

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    def load_schema(self, schema_path: str) -> None:
        self.schema.load_from_file(schema_path)

    def load_schema_from_dict(self, schema_dict: dict) -> None:
        self.schema.load_from_dict(schema_dict)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_document(
        self,
        file_path: str,
        doc_type: str,
        project: str | None = None,
        version: str | None = None,
    ) -> str:
        """Parse, store, and index a single document.

        Returns the assigned doc_id.
        """
        p = Path(file_path)
        parser = _get_parser(self.schema.get_parser_for_type(doc_type))
        parsed = parser.parse(file_path)

        doc_id = self._make_doc_id(doc_type, p.name, project)
        category = self.schema.get_category_for_type(doc_type)
        metadata: dict[str, Any] = {
            **parsed.metadata,
            "doc_type": doc_type,
            "category": category,
            "project": project or "",
            "version": version or "",
            "source_file": str(p),
        }

        self.storage.save(doc_id, parsed.content.encode("utf-8"), metadata)
        self.index.add_document(doc_id, parsed.content, metadata)

        self.graph.add_node(doc_id, metadata)
        for related_type in self.schema.get_related_types(doc_type):
            for existing_id, info in self._documents.items():
                if info.get("doc_type") == related_type:
                    self.graph.add_edge(doc_id, existing_id, "related_to")

        self._documents[doc_id] = {
            "doc_type": doc_type,
            "category": category,
            "metadata": metadata,
        }
        return doc_id

    def bulk_upload(self, directory: str, project: str | None = None) -> list[str]:
        """Upload all documents found under *directory*.

        File naming convention (optional): ``{doc_type}.{project}.{ext}``
        Example: ``srs.claims-processing.md`` → doc_type=srs, project=claims-processing

        Falls back to extension-based type detection when the convention doesn't
        match a registered doc type.
        """
        uploaded: list[str] = []
        for p in sorted(Path(directory).rglob("*")):
            if not p.is_file():
                continue
            doc_type, proj = self._detect_doc_type(p, project)
            try:
                doc_id = self.upload_document(str(p), doc_type, project=proj)
                uploaded.append(doc_id)
            except Exception:
                pass  # skip unreadable / unsupported files silently
        return uploaded

    def load_all_documents(self, directory: str) -> None:
        """Alias for bulk_upload — loads documents into memory without returning IDs."""
        self.bulk_upload(directory)

    def index_from_storage(self) -> list[str]:
        """Index all documents already present in the configured storage backend.

        Works with any backend (local, git, s3). Downloads each document, parses
        it, and adds it to the semantic index and knowledge graph.
        Returns the list of successfully indexed doc_ids.
        """
        import tempfile
        from pathlib import Path as _Path

        indexed: list[str] = []
        try:
            doc_ids = self.storage.list_documents()
        except Exception:
            return indexed

        for doc_id in doc_ids:
            try:
                content_bytes = self.storage.load(doc_id)
                metadata = {}
                if hasattr(self.storage, "load_metadata"):
                    try:
                        metadata = self.storage.load_metadata(doc_id) or {}
                    except Exception:
                        pass

                # Multi-layer doc_type detection for pre-existing files
                path = _Path(doc_id)

                # Layer 1: antcrew metadata sidecar (loaded above via load_metadata)
                doc_type: str | None = metadata.get("doc_type")

                # Layer 2: S3 native user-metadata via HeadObject
                if not doc_type and hasattr(self.storage, "_s3"):
                    try:
                        head = self.storage._s3.head_object(
                            Bucket=self.storage.bucket,
                            Key=self.storage._key(doc_id),
                        )
                        obj_meta = head.get("Metadata", {})
                        doc_type = (
                            obj_meta.get("doc-type")
                            or obj_meta.get("doc_type")
                            or obj_meta.get("category")
                        )
                    except Exception:
                        pass

                # Layer 3: path_rules from schema
                if not doc_type:
                    doc_type = self.schema.classify_by_path(doc_id)

                # Layer 4+5: filename convention + extension fallback
                if not doc_type:
                    doc_type = self._detect_doc_type(path, None)[0]

                category = metadata.get("category") or self.schema.get_category_for_type(doc_type)
                parser_name = self.schema.get_parser_for_type(doc_type)

                # Write to temp file so parsers can read it by path
                suffix = path.suffix or ".txt"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(content_bytes)
                    tmp_path = tmp.name

                try:
                    parser = _get_parser(parser_name)
                    parsed = parser.parse(tmp_path)
                finally:
                    import os as _os
                    _os.unlink(tmp_path)

                full_meta: dict = {
                    **parsed.metadata,
                    **metadata,
                    "doc_type": doc_type,
                    "category": category,
                    "source_file": doc_id,
                }
                self.index.add_document(doc_id, parsed.content, full_meta)
                self.graph.add_node(doc_id, full_meta)
                self._documents[doc_id] = {
                    "doc_type": doc_type,
                    "category": category,
                    "metadata": full_meta,
                }
                indexed.append(doc_id)
            except Exception:
                pass  # skip unreadable/unsupported files silently

        return indexed

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        doc_type: str | None = None,
        category: str | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        # Explicit filter always wins — bypass hint routing
        if doc_type or category:
            filter_meta: dict | None = {"doc_type": doc_type} if doc_type else {"category": category}
            return self.index.search(query, top_k=top_k, filter_metadata=filter_meta)

        # Intent-based routing via query_hints
        hinted_types = self.schema.get_doc_types_for_query(query)
        if not hinted_types:
            return self.index.search(query, top_k=top_k)

        # Search hinted types first, then fill remaining slots with generic results
        seen: set[str] = set()
        results: list[dict] = []
        for dt in hinted_types:
            for r in self.index.search(query, top_k=top_k, filter_metadata={"doc_type": dt}):
                rid = r.get("id") or r.get("doc_id", "")
                if rid not in seen:
                    seen.add(rid)
                    results.append(r)
            if len(results) >= top_k:
                break

        if len(results) < top_k:
            for r in self.index.search(query, top_k=top_k):
                rid = r.get("id") or r.get("doc_id", "")
                if rid not in seen:
                    seen.add(rid)
                    results.append(r)
                if len(results) >= top_k:
                    break

        return results[:top_k]

    def search_by_type(self, query: str, doc_type: str, top_k: int = 5) -> list[dict]:
        return self.search(query, doc_type=doc_type, top_k=top_k)

    def search_by_category(self, query: str, category: str, top_k: int = 5) -> list[dict]:
        return self.search(query, category=category, top_k=top_k)

    # ------------------------------------------------------------------
    # Graph / relations
    # ------------------------------------------------------------------

    def get_related_documents(self, doc_id: str) -> list[dict]:
        related_ids = self.graph.get_neighbors(doc_id)
        return [
            {"id": rid, **self._documents[rid]}
            for rid in related_ids
            if rid in self._documents
        ]

    # ------------------------------------------------------------------
    # Agent context
    # ------------------------------------------------------------------

    def get_context_for_agent(self, agent_name: str, query: str) -> dict[str, list[dict]]:
        """Return documentation context keyed by doc_type for a specific agent.

        Uses agent_hints from the schema to determine which doc types to search.
        Hint format: "For X: search TYPE_NAME (type_id)"
        """
        hints = self.schema.get_agent_hints(agent_name)
        context: dict[str, list[dict]] = {}
        for hint in hints:
            # Extract the doc_type id from parentheses, e.g. "(srs)"
            m = re.search(r"\((\w+)\)", hint)
            if not m:
                continue
            doc_type = m.group(1)
            docs = self.search_by_type(query, doc_type, top_k=3)
            if docs:
                context[doc_type] = docs
        return context

    def format_context(self, context: dict[str, list[dict]], max_chars: int = 4000) -> str:
        """Format a context dict into a string ready to prepend to an LLM prompt."""
        parts: list[str] = []
        chars = 0
        for doc_type, docs in context.items():
            dt = self.schema.get_document_type(doc_type)
            type_name = dt.name if dt else doc_type.upper()
            header = f"### {type_name} ({doc_type.upper()})"
            parts.append(header)
            chars += len(header)
            for doc in docs:
                snippet = doc["content"][:1000]
                parts.append(snippet)
                chars += len(snippet)
                if chars >= max_chars:
                    break
            if chars >= max_chars:
                break
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Validation & stats
    # ------------------------------------------------------------------

    def validate_against_schema(self) -> dict:
        registered = {dt.id for dt in self.schema.all_types()}
        present = {info["doc_type"] for info in self._documents.values()}
        return {
            "present_types": sorted(present),
            "missing_types": sorted(registered - present),
            "errors": [],
        }

    def get_statistics(self) -> dict:
        by_type: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for info in self._documents.values():
            by_type[info["doc_type"]] = by_type.get(info["doc_type"], 0) + 1
            by_category[info["category"]] = by_category.get(info["category"], 0) + 1
        return {
            "total_documents": len(self._documents),
            "by_type": by_type,
            "by_category": by_category,
            "index": self.index.get_index_size(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _make_doc_id(self, doc_type: str, filename: str, project: str | None) -> str:
        safe = re.sub(r"[^\w\-.]", "_", filename)
        prefix = f"{project}/" if project else ""
        return f"{prefix}{doc_type}/{safe}"

    def _detect_doc_type(self, path: Path, default_project: str | None) -> tuple[str, str | None]:
        """Detect doc_type from filename convention or file extension."""
        stem_parts = path.stem.split(".")
        if len(stem_parts) >= 2:
            candidate_type = stem_parts[0]
            candidate_proj = stem_parts[1] if len(stem_parts) > 1 else None
            if self.schema.get_document_type(candidate_type):
                return candidate_type, candidate_proj or default_project

        all_types = self.schema.all_types()
        ext = path.suffix.lower()
        ext_parser = _EXT_TO_PARSER.get(ext, "text")
        for dt in all_types:
            canonical = _PARSER_ALIASES.get(dt.parser, dt.parser)
            if canonical == ext_parser:
                return dt.id, default_project

        return (all_types[0].id if all_types else "generic"), default_project
