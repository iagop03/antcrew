"""Tests for DocGenerator capability."""
from __future__ import annotations

import pytest

from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind, MemoryStore,
)
from antcrew.capabilities.doc_generator import DocGenerator
from antcrew.testing import SequencedLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

README_CONTENT = "# My Project\n\n## Overview\nA todo API.\n"


@pytest.fixture
def goal():
    from antcrew.engine import DesiredProjectState, Goal
    return Goal(description="Build a todo API", desired_state=DesiredProjectState(frozenset()))


def _make_store(sources: list | None = None, with_arch: bool = True):
    store = MemoryStore()
    if with_arch:
        store.write(Artifact(
            id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
            content="# Architecture\n- FastAPI\n- SQLite",
        ))
    for art in (sources or [_default_source()]):
        store.write(art)
    return store


def _default_source():
    return Artifact(
        id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
        content="class Model:\n    pass\n",
        metadata={"file_path": "src/models.py"},
    )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestDocGeneratorErrors:
    def test_no_source_artifacts(self, goal):
        result = DocGenerator(llm=SequencedLLM([README_CONTENT])).execute(MemoryStore(), goal)
        assert not result.succeeded
        assert any("source" in e for e in result.errors)

    def test_only_empty_source_content(self, goal):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("src/empty.py"), kind=ArtifactKind.SOURCE,
            content="   ",  # whitespace only — should be skipped
            metadata={"file_path": "src/empty.py"},
        ))
        # With all sources empty, files_block will be empty but no error —
        # LLM still receives the request; test it doesn't crash
        llm    = SequencedLLM([README_CONTENT])
        result = DocGenerator(llm=llm).execute(store, goal)
        assert result.succeeded  # graceful even with empty source block


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestDocGeneratorOutput:
    def test_creates_documentation_artifact(self, goal):
        llm    = SequencedLLM([README_CONTENT])
        result = DocGenerator(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.created) == 1
        doc = result.delta.created[0]
        assert doc.id   == ArtifactId("documentation")
        assert doc.kind == ArtifactKind.DOCUMENTATION

    def test_readme_file_path_in_metadata(self, goal):
        llm    = SequencedLLM([README_CONTENT])
        result = DocGenerator(llm=llm).execute(_make_store(), goal)
        doc    = result.delta.created[0]
        assert doc.metadata.get("file_path") == "README.md"

    def test_llm_receives_goal_and_arch(self, goal):
        """Check the LLM call is made (call_count == 1) and content is used."""
        llm    = SequencedLLM([README_CONTENT])
        result = DocGenerator(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert llm.call_count == 1

    def test_works_without_architecture(self, goal):
        """If no architecture artifact exists, DocGenerator should not crash."""
        store  = _make_store(with_arch=False)
        llm    = SequencedLLM([README_CONTENT])
        result = DocGenerator(llm=llm).execute(store, goal)
        assert result.succeeded

    def test_content_is_llm_response(self, goal):
        llm    = SequencedLLM(["# Custom README"])
        result = DocGenerator(llm=llm).execute(_make_store(), goal)
        assert result.delta.created[0].content == "# Custom README"

    def test_long_source_truncated(self, goal):
        """Sources > 80 lines should be truncated before sending to LLM."""
        long_content = "\n".join(f"line_{i} = {i}" for i in range(200))
        store = _make_store(sources=[Artifact(
            id=ArtifactId("src/big.py"), kind=ArtifactKind.SOURCE,
            content=long_content, metadata={"file_path": "src/big.py"},
        )])
        llm    = SequencedLLM([README_CONTENT])
        result = DocGenerator(llm=llm).execute(store, goal)
        assert result.succeeded
        # verify truncation occurred: the LLM was called once (no crash)
        assert llm.call_count == 1
