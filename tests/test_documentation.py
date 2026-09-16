"""Tests for antcrew_engine.documentation module.

Covers: schema registry, parsers, storage, index, graph, manager, and
BaseExecutor integration. All tests are self-contained — no external services
required. ChromaDB / networkx are optional; tests adapt when absent.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from antcrew_engine.documentation import (
    DocumentationGraph,
    DocumentationIndex,
    DocumentationManager,
    DocumentationSchemaRegistry,
    DocumentTypeConfig,
)
from antcrew_engine.documentation.parsers import (
    JiraTicketParser,
    MarkdownParser,
    TextParser,
)
from antcrew_engine.documentation.storage.local import LocalFileStorage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_SCHEMA: dict = {
    "documentation_schema": {
        "org_name": "TestOrg",
        "document_types": [
            {
                "id": "spec",
                "name": "Specification",
                "category": "functional",
                "purpose": "Requirements",
                "parser": "markdown",
                "related_to": ["design"],
            },
            {
                "id": "design",
                "name": "Design",
                "category": "technical",
                "purpose": "Architecture",
                "parser": "markdown",
                "related_to": [],
            },
        ],
        "agent_hints": {
            "BackendDev": [
                "For requirements: search Specification (spec)",
                "For design: search Design (design)",
            ],
        },
    }
}


@pytest.fixture()
def schema_registry() -> DocumentationSchemaRegistry:
    reg = DocumentationSchemaRegistry()
    reg.load_from_dict(MINIMAL_SCHEMA)
    return reg


@pytest.fixture()
def tmp_docs(tmp_path: Path) -> Path:
    """Create a small directory of markdown documents."""
    (tmp_path / "spec").mkdir()
    (tmp_path / "design").mkdir()
    (tmp_path / "spec" / "spec.project.md").write_text(
        "# Authentication spec\n\nUsers must log in with JWT tokens.", encoding="utf-8"
    )
    (tmp_path / "design" / "design.project.md").write_text(
        "# Architecture\n\nUse a stateless REST API with HS256 JWT.", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def doc_manager(tmp_path: Path) -> DocumentationManager:
    mgr = DocumentationManager(
        schema_path=None,
        storage_type="local",
        storage_config={"path": str(tmp_path / "storage")},
        cache_dir=str(tmp_path / "index"),
    )
    mgr.load_schema_from_dict(MINIMAL_SCHEMA)
    return mgr


# ---------------------------------------------------------------------------
# 1. DocumentationSchemaRegistry
# ---------------------------------------------------------------------------

class TestDocumentationSchemaRegistry:
    def test_load_from_dict(self, schema_registry: DocumentationSchemaRegistry) -> None:
        assert schema_registry.org_name == "TestOrg"
        assert len(schema_registry.all_types()) == 2

    def test_get_document_type(self, schema_registry: DocumentationSchemaRegistry) -> None:
        dt = schema_registry.get_document_type("spec")
        assert dt is not None
        assert dt.name == "Specification"
        assert dt.category == "functional"

    def test_unknown_type_returns_none(self, schema_registry: DocumentationSchemaRegistry) -> None:
        assert schema_registry.get_document_type("nonexistent") is None

    def test_get_parser_for_type(self, schema_registry: DocumentationSchemaRegistry) -> None:
        assert schema_registry.get_parser_for_type("spec") == "markdown"
        assert schema_registry.get_parser_for_type("unknown") == "markdown"  # default

    def test_get_related_types(self, schema_registry: DocumentationSchemaRegistry) -> None:
        related = schema_registry.get_related_types("spec")
        assert "design" in related

    def test_get_agent_hints(self, schema_registry: DocumentationSchemaRegistry) -> None:
        hints = schema_registry.get_agent_hints("BackendDev")
        assert len(hints) == 2
        assert any("spec" in h for h in hints)

    def test_agent_hints_unknown_agent(self, schema_registry: DocumentationSchemaRegistry) -> None:
        assert schema_registry.get_agent_hints("NoSuchAgent") == []

    def test_validate_schema_valid(self, schema_registry: DocumentationSchemaRegistry) -> None:
        assert schema_registry.validate_schema() is True

    def test_validate_schema_empty(self) -> None:
        reg = DocumentationSchemaRegistry()
        assert reg.validate_schema() is False

    def test_register_document_type(self, schema_registry: DocumentationSchemaRegistry) -> None:
        schema_registry.register_document_type({
            "id": "test_doc",
            "name": "Test Document",
            "category": "technical",
            "parser": "text",
        })
        dt = schema_registry.get_document_type("test_doc")
        assert dt is not None
        assert dt.name == "Test Document"

    def test_load_from_yaml_file(self, tmp_path: Path) -> None:
        schema_file = tmp_path / "schema.yaml"
        schema_file.write_text(
            "documentation_schema:\n"
            "  org_name: YAMLOrg\n"
            "  document_types:\n"
            "    - id: req\n"
            "      name: Requirements\n"
            "      category: functional\n"
            "      parser: markdown\n",
            encoding="utf-8",
        )
        try:
            reg = DocumentationSchemaRegistry(schema_path=str(schema_file))
            assert reg.org_name == "YAMLOrg"
            assert reg.get_document_type("req") is not None
        except ImportError:
            pytest.skip("pyyaml not installed")

    def test_get_category_for_type(self, schema_registry: DocumentationSchemaRegistry) -> None:
        assert schema_registry.get_category_for_type("spec") == "functional"
        assert schema_registry.get_category_for_type("design") == "technical"
        assert schema_registry.get_category_for_type("missing") == ""


# ---------------------------------------------------------------------------
# 2. Parsers
# ---------------------------------------------------------------------------

class TestMarkdownParser:
    def test_parse_basic(self, tmp_path: Path) -> None:
        p = tmp_path / "doc.md"
        p.write_text("# My Title\n\nSome content here.\n\n## Section\n\nMore text.")
        result = MarkdownParser().parse(str(p))
        assert "My Title" in result.content
        assert result.metadata["title"] == "My Title"
        assert result.metadata["word_count"] > 0
        assert len(result.sections) >= 2

    def test_parse_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.md"
        p.write_text("")
        result = MarkdownParser().parse(str(p))
        assert result.content == ""

    def test_sections_extracted(self, tmp_path: Path) -> None:
        p = tmp_path / "multi.md"
        p.write_text("# H1\n\nIntro.\n\n## H2\n\nBody.\n\n### H3\n\nDeep.")
        result = MarkdownParser().parse(str(p))
        titles = [s["title"] for s in result.sections]
        assert "H2" in titles


class TestTextParser:
    def test_parse_basic(self, tmp_path: Path) -> None:
        p = tmp_path / "notes.txt"
        p.write_text("Hello world.\nThis is a test.")
        result = TextParser().parse(str(p))
        assert "Hello world" in result.content
        assert result.metadata["word_count"] == 6

    def test_single_section(self, tmp_path: Path) -> None:
        p = tmp_path / "a.txt"
        p.write_text("content")
        result = TextParser().parse(str(p))
        assert len(result.sections) == 1
        assert result.sections[0]["title"] == "Content"


class TestJiraTicketParser:
    def test_parse_json(self, tmp_path: Path) -> None:
        ticket = {"key": "PROJ-1", "fields": {"summary": "Fix login bug", "description": "Steps to reproduce"}}
        p = tmp_path / "ticket.json"
        p.write_text(json.dumps(ticket))
        result = JiraTicketParser().parse(str(p))
        assert "PROJ-1" in result.content
        assert "Fix login bug" in result.content
        assert result.metadata["key"] == "PROJ-1"

    def test_parse_text_fallback(self, tmp_path: Path) -> None:
        p = tmp_path / "plain.json"
        p.write_text("This is not JSON {{{")
        result = JiraTicketParser().parse(str(p))
        assert result.content == "This is not JSON {{{"


# ---------------------------------------------------------------------------
# 3. LocalFileStorage
# ---------------------------------------------------------------------------

class TestLocalFileStorage:
    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(root=str(tmp_path / "store"))
        storage.save("doc/readme.md", b"Hello storage", {"doc_type": "spec"})
        loaded = storage.load("doc/readme.md")
        assert loaded == b"Hello storage"

    def test_list_documents(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(root=str(tmp_path / "store"))
        storage.save("alpha.md", b"A", {})
        storage.save("beta.md", b"B", {})
        docs = storage.list_documents()
        assert len(docs) == 2

    def test_delete(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(root=str(tmp_path / "store"))
        storage.save("x.md", b"X", {})
        storage.delete("x.md")
        assert "x.md" not in storage.list_documents()

    def test_list_with_prefix(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(root=str(tmp_path / "store"))
        storage.save("project/spec.md", b"spec", {})
        storage.save("other/design.md", b"design", {})
        docs = storage.list_documents(prefix="project")
        assert any("project" in d for d in docs)

    def test_load_metadata(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(root=str(tmp_path / "store"))
        storage.save("doc.md", b"content", {"doc_type": "spec", "version": "1.0"})
        meta = storage.load_metadata("doc.md")
        assert meta.get("doc_type") == "spec"


# ---------------------------------------------------------------------------
# 4. DocumentationIndex
# ---------------------------------------------------------------------------

class TestDocumentationIndex:
    def test_add_and_search(self, tmp_path: Path) -> None:
        idx = DocumentationIndex(cache_dir=str(tmp_path / "idx"), chunk_size=200)
        idx.add_document(
            "doc1",
            "JWT authentication token login security",
            {"doc_type": "spec", "category": "functional"},
        )
        idx.add_document(
            "doc2",
            "Database schema tables foreign keys indexes",
            {"doc_type": "design", "category": "technical"},
        )
        results = idx.search("JWT login", top_k=5)
        assert len(results) >= 1
        assert results[0]["id"] == "doc1"

    def test_search_with_filter(self, tmp_path: Path) -> None:
        idx = DocumentationIndex(cache_dir=str(tmp_path / "idx2"))
        idx.add_document("s1", "spec content about auth", {"doc_type": "spec", "category": "functional"})
        idx.add_document("d1", "design content about database", {"doc_type": "design", "category": "technical"})
        results = idx.search("content", top_k=5, filter_metadata={"doc_type": "spec"})
        assert all(r["metadata"]["doc_type"] == "spec" for r in results)

    def test_delete_document(self, tmp_path: Path) -> None:
        idx = DocumentationIndex(cache_dir=str(tmp_path / "idx3"))
        idx.add_document("del1", "deletable content", {"doc_type": "spec"})
        idx.delete_document("del1")
        results = idx.search("deletable", top_k=5)
        assert not any(r["id"] == "del1" for r in results)

    def test_get_index_size(self, tmp_path: Path) -> None:
        idx = DocumentationIndex(cache_dir=str(tmp_path / "idx4"))
        size = idx.get_index_size()
        assert isinstance(size, dict)
        assert "backend" in size

    def test_chunk_splitting(self) -> None:
        idx = DocumentationIndex(chunk_size=20)
        chunks = idx._chunk("word " * 50)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 60  # generous bound for word-boundary rounding

    def test_search_empty_index(self, tmp_path: Path) -> None:
        idx = DocumentationIndex(cache_dir=str(tmp_path / "empty_idx"))
        results = idx.search("anything", top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# 5. DocumentationGraph
# ---------------------------------------------------------------------------

class TestDocumentationGraph:
    def test_add_node_and_edge(self) -> None:
        g = DocumentationGraph()
        g.add_node("doc1", {"doc_type": "spec"})
        g.add_node("doc2", {"doc_type": "design"})
        g.add_edge("doc1", "doc2", "related_to")
        neighbors = g.get_neighbors("doc1")
        assert "doc2" in neighbors

    def test_get_neighbors_filtered_by_relation(self) -> None:
        g = DocumentationGraph()
        g.add_edge("a", "b", "related_to")
        g.add_edge("a", "c", "depends_on")
        related = g.get_neighbors("a", "related_to")
        assert "b" in related
        assert "c" not in related

    def test_get_graph_for_doc(self) -> None:
        g = DocumentationGraph()
        g.add_edge("root", "child1", "related_to")
        g.add_edge("child1", "grandchild", "related_to")
        subgraph = g.get_graph_for_doc("root", depth=2)
        assert "root" in subgraph["nodes"]
        assert "child1" in subgraph["nodes"]
        assert "grandchild" in subgraph["nodes"]

    def test_detect_cycles_no_cycle(self) -> None:
        g = DocumentationGraph()
        g.add_edge("a", "b", "related_to")
        g.add_edge("b", "c", "related_to")
        cycles = g.detect_cycles()
        assert isinstance(cycles, list)

    def test_unknown_node_returns_empty(self) -> None:
        g = DocumentationGraph()
        assert g.get_neighbors("nonexistent") == []


# ---------------------------------------------------------------------------
# 6. DocumentationManager
# ---------------------------------------------------------------------------

class TestDocumentationManager:
    def test_load_schema_from_dict(self, doc_manager: DocumentationManager) -> None:
        assert doc_manager.schema.validate_schema() is True
        assert doc_manager.schema.get_document_type("spec") is not None

    def test_upload_document(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        spec_file = str(tmp_docs / "spec" / "spec.project.md")
        doc_id = doc_manager.upload_document(spec_file, "spec", project="project")
        assert doc_id.endswith(".md") or "spec" in doc_id
        assert doc_id in doc_manager._documents

    def test_bulk_upload(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        uploaded = doc_manager.bulk_upload(str(tmp_docs))
        assert len(uploaded) >= 2

    def test_search_after_upload(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        results = doc_manager.search("JWT authentication", top_k=5)
        assert len(results) >= 1

    def test_search_by_type(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        results = doc_manager.search_by_type("authentication", "spec", top_k=5)
        assert isinstance(results, list)

    def test_search_by_category(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        results = doc_manager.search_by_category("architecture", "technical", top_k=5)
        assert isinstance(results, list)

    def test_get_statistics(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        stats = doc_manager.get_statistics()
        assert stats["total_documents"] >= 2
        assert "by_type" in stats
        assert "by_category" in stats

    def test_validate_against_schema(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        report = doc_manager.validate_against_schema()
        assert "present_types" in report
        assert "missing_types" in report

    def test_get_related_documents(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        for doc_id in list(doc_manager._documents.keys())[:1]:
            related = doc_manager.get_related_documents(doc_id)
            assert isinstance(related, list)

    def test_get_context_for_agent(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        ctx = doc_manager.get_context_for_agent("BackendDev", "JWT authentication")
        assert isinstance(ctx, dict)

    def test_format_context(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        doc_manager.bulk_upload(str(tmp_docs))
        ctx = doc_manager.get_context_for_agent("BackendDev", "JWT")
        formatted = doc_manager.format_context(ctx, max_chars=500)
        assert isinstance(formatted, str)

    def test_doc_id_namespacing(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        spec_file = str(tmp_docs / "spec" / "spec.project.md")
        doc_id = doc_manager.upload_document(spec_file, "spec", project="myproject")
        assert "myproject" in doc_id or "spec" in doc_id

    def test_bulk_upload_skips_bad_files(self, doc_manager: DocumentationManager, tmp_path: Path) -> None:
        (tmp_path / "good.md").write_text("# Good doc\n\nContent here.")
        (tmp_path / "bad.xyz").write_bytes(b"\x00\x01\x02\x03")
        doc_manager.load_schema_from_dict(MINIMAL_SCHEMA)
        uploaded = doc_manager.bulk_upload(str(tmp_path))
        assert len(uploaded) >= 1  # at least good.md


# ---------------------------------------------------------------------------
# 7. BaseExecutor integration
# ---------------------------------------------------------------------------

class TestBaseExecutorDocumentation:
    def test_set_documentation(self) -> None:
        from antcrew_engine.capabilities.base import BaseExecutor

        class DummyExecutor(BaseExecutor):
            descriptor = None  # type: ignore[assignment]
            def _run(self, store, goal):
                return None

        ex = DummyExecutor()
        assert ex._documentation is None
        ex.set_documentation("mock_doc_mgr")
        assert ex._documentation == "mock_doc_mgr"

    def test_doc_context_returns_empty_without_manager(self) -> None:
        from antcrew_engine.capabilities.base import BaseExecutor

        class DummyExecutor(BaseExecutor):
            descriptor = None  # type: ignore[assignment]

        ex = DummyExecutor()
        assert ex._doc_context("any query") == ""

    def test_doc_context_returns_string_with_manager(
        self, doc_manager: DocumentationManager, tmp_docs: Path
    ) -> None:
        from antcrew_engine.capabilities.base import BaseExecutor

        class DummyExecutor(BaseExecutor):
            descriptor = None  # type: ignore[assignment]

        doc_manager.bulk_upload(str(tmp_docs))
        ex = DummyExecutor()
        ex.set_documentation(doc_manager)
        ctx = ex._doc_context("JWT authentication")
        assert isinstance(ctx, str)

    def test_doc_context_silent_on_exception(self) -> None:
        from antcrew_engine.capabilities.base import BaseExecutor

        class BadManager:
            def get_context_for_agent(self, *a, **kw):
                raise RuntimeError("boom")
            def search(self, *a, **kw):
                raise RuntimeError("boom")

        class DummyExecutor(BaseExecutor):
            descriptor = None  # type: ignore[assignment]

        ex = DummyExecutor()
        ex.set_documentation(BadManager())
        # Must not raise — returns ""
        assert ex._doc_context("query") == ""


# ---------------------------------------------------------------------------
# 8. BaseAgent integration
# ---------------------------------------------------------------------------

class TestBaseAgentDocumentation:
    def _make_agent(self):
        from unittest.mock import MagicMock
        from antcrew.core.agent import BaseAgent

        class DummyAgent(BaseAgent):
            name = "DummyAgent"
            def run(self, state):
                return {}

        llm = MagicMock()
        llm.system.return_value = "ok"
        llm.current_agent = None
        agent = DummyAgent(llm=llm)
        return agent, llm

    def test_set_documentation(self) -> None:
        agent, _ = self._make_agent()
        assert agent.documentation is None
        agent.set_documentation("mock_mgr")
        assert agent.documentation == "mock_mgr"

    def test_doc_context_empty_without_manager(self) -> None:
        agent, _ = self._make_agent()
        assert agent._doc_context("any query") == ""

    def test_doc_context_with_manager(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        agent, _ = self._make_agent()
        doc_manager.bulk_upload(str(tmp_docs))
        agent.set_documentation(doc_manager)
        ctx = agent._doc_context("JWT authentication")
        assert isinstance(ctx, str)

    def test_inject_documentation_prepends_to_user(self, doc_manager: DocumentationManager, tmp_docs: Path) -> None:
        agent, _ = self._make_agent()
        doc_manager.bulk_upload(str(tmp_docs))
        agent.set_documentation(doc_manager)
        result = agent._inject_documentation("Task: build login")
        # When docs found, result starts with documentation context
        assert isinstance(result, str)
        assert "Task: build login" in result

    def test_inject_documentation_noop_without_manager(self) -> None:
        agent, _ = self._make_agent()
        msg = "Task: build login"
        assert agent._inject_documentation(msg) == msg

    def test_doc_context_silent_on_exception(self) -> None:
        from antcrew.core.agent import BaseAgent

        class BadManager:
            def get_context_for_agent(self, *a, **kw):
                raise RuntimeError("boom")
            def search(self, *a, **kw):
                raise RuntimeError("boom")

        class DummyAgent(BaseAgent):
            name = "DummyAgent"
            def run(self, state):
                return {}

        from unittest.mock import MagicMock
        agent = DummyAgent(llm=MagicMock())
        agent.set_documentation(BadManager())
        assert agent._doc_context("query") == ""
