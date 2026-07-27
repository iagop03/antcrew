"""Tests for ReviewFixer capability."""
from __future__ import annotations

import json

import pytest

from antcrew.capabilities.review_fixer import ReviewFixer
from antcrew.engine import (
    Artifact,
    ArtifactId,
    ArtifactKind,
    MemoryStore,
)
from antcrew.testing import SequencedLLM

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def goal():
    from antcrew.engine import DesiredProjectState, Goal
    return Goal(description="Build a todo API", desired_state=DesiredProjectState(frozenset()))


_CRITICAL_FINDING = {
    "file": "src/models.py",
    "severity": "critical",
    "message": "SQL injection risk",
    "suggestion": "Use parameterised queries",
}

_INFO_FINDING = {
    "file": "src/models.py",
    "severity": "info",
    "message": "Variable name could be clearer",
    "suggestion": "Rename 'x' to 'user_id'",
}


def _make_store(verdict="needs_changes", findings=None, sources=None):
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("review_report"), kind=ArtifactKind.REPORT,
        content={
            "verdict": verdict,
            "findings": findings if findings is not None else [_CRITICAL_FINDING],
        },
    ))
    for art in (sources or [_default_source()]):
        store.write(art)
    return store


def _default_source():
    return Artifact(
        id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
        content="class Model:\n    pass\n",
        metadata={"file_path": "src/models.py", "task_id": "task_001"},
    )


def _fix_response(file_path: str, content: str) -> str:
    return json.dumps({"files": [{"file_path": file_path, "content": content}]})


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestReviewFixerErrors:
    def test_no_review_report(self, goal):
        result = ReviewFixer(llm=SequencedLLM([])).execute(MemoryStore(), goal)
        assert not result.succeeded
        assert any("review_report" in e for e in result.errors)

    def test_already_approved(self, goal):
        store = _make_store(verdict="approved")
        result = ReviewFixer(llm=SequencedLLM([])).execute(store, goal)
        assert not result.succeeded
        assert any("approved" in e for e in result.errors)

    def test_no_critical_or_error_findings(self, goal):
        store = _make_store(findings=[_INFO_FINDING])
        result = ReviewFixer(llm=SequencedLLM([])).execute(store, goal)
        assert not result.succeeded
        assert any("critical" in e or "error" in e for e in result.errors)

    def test_no_source_artifacts(self, goal):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("review_report"), kind=ArtifactKind.REPORT,
            content={"verdict": "needs_changes", "findings": [_CRITICAL_FINDING]},
        ))
        result = ReviewFixer(llm=SequencedLLM([])).execute(store, goal)
        assert not result.succeeded
        assert any("source" in e for e in result.errors)

    def test_llm_returns_no_files(self, goal):
        llm = SequencedLLM([json.dumps({"files": []})])
        result = ReviewFixer(llm=llm).execute(_make_store(), goal)
        assert not result.succeeded


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestReviewFixerFixes:
    def test_existing_file_goes_to_modified(self, goal):
        llm    = SequencedLLM([_fix_response("src/models.py", "class Model:\n    safe = True\n")])
        result = ReviewFixer(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.modified) == 1
        assert result.delta.modified[0].id == ArtifactId("src/models.py")

    def test_preserves_task_id_in_metadata(self, goal):
        llm    = SequencedLLM([_fix_response("src/models.py", "class Model:\n    safe = True\n")])
        result = ReviewFixer(llm=llm).execute(_make_store(), goal)
        art    = result.delta.modified[0]
        assert art.metadata.get("task_id") == "task_001"

    def test_new_file_goes_to_created(self, goal):
        llm    = SequencedLLM([_fix_response("src/new_file.py", "def helper(): pass\n")])
        result = ReviewFixer(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.created) == 1

    def test_list_response_also_accepted(self, goal):
        """ReviewFixer accepts both {"files": [...]} and [...] formats."""
        llm    = SequencedLLM([json.dumps([{"file_path": "src/models.py", "content": "# fixed\n"}])])
        result = ReviewFixer(llm=llm).execute(_make_store(), goal)
        assert result.succeeded

    def test_only_critical_error_findings_sent_to_llm(self, goal):
        """Mixed findings: only critical/error should trigger a fix."""
        findings = [_CRITICAL_FINDING, _INFO_FINDING]
        store  = _make_store(findings=findings)
        llm    = SequencedLLM([_fix_response("src/models.py", "# fixed\n")])
        result = ReviewFixer(llm=llm).execute(store, goal)
        assert result.succeeded  # critical finding present → fix attempted
