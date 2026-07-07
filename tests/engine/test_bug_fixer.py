"""Tests for BugFixer capability."""
from __future__ import annotations

import json
import pytest

from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind, MemoryStore,
)
from antcrew.capabilities.bug_fixer import BugFixer, _extract_failing_files
from antcrew.testing import SequencedLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def goal():
    from antcrew.engine import DesiredProjectState, Goal
    return Goal(description="Build a todo API", desired_state=DesiredProjectState(frozenset()))


def _make_store(*, passed: bool = False, output: str = "", sources: list | None = None):
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
        content={"passed": passed, "output": output},
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


def _fix_llm(*contents):
    return SequencedLLM([json.dumps([{"file_path": c[0], "content": c[1]}]) for c in contents])


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestBugFixerErrors:
    def test_no_test_report(self, goal):
        result = BugFixer(llm=SequencedLLM([])).execute(MemoryStore(), goal)
        assert not result.succeeded
        assert any("test_report" in e for e in result.errors)

    def test_tests_already_pass(self, goal):
        store = _make_store(passed=True)
        result = BugFixer(llm=SequencedLLM([])).execute(store, goal)
        assert not result.succeeded
        assert any("already pass" in e for e in result.errors)

    def test_no_source_artifacts(self, goal):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
            content={"passed": False, "output": "FAILED"},
        ))
        result = BugFixer(llm=SequencedLLM([])).execute(store, goal)
        assert not result.succeeded
        assert any("source" in e for e in result.errors)

    def test_no_fixes_parsed_returns_error(self, goal):
        llm    = SequencedLLM(["not json at all"])
        result = BugFixer(llm=llm).execute(_make_store(), goal)
        assert not result.succeeded


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestBugFixerFixes:
    def test_returns_modified_artifact(self, goal):
        fix_content = "class Model:\n    fixed = True\n"
        llm = SequencedLLM([json.dumps([
            {"file_path": "src/models.py", "content": fix_content},
        ])])
        result = BugFixer(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.modified) == 1

    def test_preserves_metadata_including_task_id(self, goal):
        fix_content = "class Model:\n    fixed = True\n"
        llm = SequencedLLM([json.dumps([
            {"file_path": "src/models.py", "content": fix_content},
        ])])
        result = BugFixer(llm=llm).execute(_make_store(), goal)
        art = result.delta.modified[0]
        assert art.metadata.get("task_id") == "task_001"

    def test_new_file_in_fix_goes_to_modified(self, goal):
        """LLM can fix a file not yet in the store — treated as new source."""
        llm = SequencedLLM([json.dumps([
            {"file_path": "src/new_helper.py", "content": "def helper(): pass\n"},
        ])])
        result = BugFixer(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.modified) == 1
        # not in store → id defaults to src/<fp>
        assert result.delta.modified[0].id == ArtifactId("src/src/new_helper.py")

    def test_empty_fix_entries_ignored(self, goal):
        llm = SequencedLLM([json.dumps([
            {"file_path": "", "content": "x"},
            {"file_path": "src/models.py", "content": ""},
            {"file_path": "src/models.py", "content": "class M: pass"},
        ])])
        result = BugFixer(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.modified) == 1


# ---------------------------------------------------------------------------
# _extract_failing_files helper
# ---------------------------------------------------------------------------

class TestExtractFailingFiles:
    def test_parses_long_file_path(self):
        output = 'File "/tmp/project/src/models.py", line 42, in test_method'
        assert "src/models.py" in next(iter(_extract_failing_files(output)))

    def test_parses_short_tb_style(self):
        output = "src/utils.py:17: AssertionError"
        assert "src/utils.py" in next(iter(_extract_failing_files(output)))

    def test_ignores_bare_filename_without_slash(self):
        output = "models.py:10: Error"
        # No slash → should NOT be captured (would be ambiguous)
        assert not any("models.py" == p for p in _extract_failing_files(output))

    def test_empty_output_returns_empty_set(self):
        assert _extract_failing_files("") == set()
