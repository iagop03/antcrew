"""Tests for CodeRegenerator capability."""
from __future__ import annotations

import pytest

from antcrew.capabilities.code_regenerator import CodeRegenerator
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


_TRACEBACK = (
    'FAILED src/models.py::test_model - AssertionError\n'
    'File "/tmp/proj/src/models.py", line 10, in test_model'
)


def _make_store(
    *,
    passed: bool = False,
    test_output: str = _TRACEBACK,
    tasks: list | None = None,
    sources: list | None = None,
    tests: list | None = None,
):
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
        content={"passed": passed, "output": test_output},
    ))
    task_list = tasks or [
        {"id": "task_001", "status": "done", "title": "Models", "description": "Create models",
         "depends_on": []},
    ]
    store.write(Artifact(
        id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH,
        content={"tasks": task_list},
    ))
    for art in (sources or [_src("src/models.py", "task_001")]):
        store.write(art)
    for art in (tests or [_test("tests/test_models.py", "task_001")]):
        store.write(art)
    return store


def _src(fp: str, task_id: str) -> Artifact:
    return Artifact(
        id=ArtifactId(f"src/{fp}"), kind=ArtifactKind.SOURCE,
        content="class Model: pass\n",
        metadata={"file_path": fp, "task_id": task_id},
    )


def _test(fp: str, task_id: str) -> Artifact:
    return Artifact(
        id=ArtifactId(f"test/{fp}"), kind=ArtifactKind.TEST,
        content="def test_model(): pass\n",
        metadata={"file_path": fp, "task_id": task_id},
    )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestCodeRegeneratorErrors:
    def test_no_test_report(self, goal):
        result = CodeRegenerator(llm=SequencedLLM(["lessons"])).execute(MemoryStore(), goal)
        assert not result.succeeded
        assert any("test_report" in e for e in result.errors)

    def test_tests_already_pass(self, goal):
        store = _make_store(passed=True)
        result = CodeRegenerator(llm=SequencedLLM(["lessons"])).execute(store, goal)
        assert not result.succeeded
        assert any("already pass" in e for e in result.errors)

    def test_no_task_graph(self, goal):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
            content={"passed": False, "output": "FAILED"},
        ))
        result = CodeRegenerator(llm=SequencedLLM(["lessons"])).execute(store, goal)
        assert not result.succeeded
        assert any("task_graph" in e for e in result.errors)

    def test_no_done_tasks_returns_error(self, goal):
        tasks  = [{"id": "task_001", "status": "pending", "depends_on": []}]
        store  = _make_store(tasks=tasks, test_output="generic failure with no file paths")
        result = CodeRegenerator(llm=SequencedLLM(["lessons"])).execute(store, goal)
        assert not result.succeeded
        assert any("reset" in e or "task" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestCodeRegeneratorReset:
    def test_resets_failing_task_to_pending(self, goal):
        store  = _make_store()
        result = CodeRegenerator(llm=SequencedLLM(["Use parameterised queries."])).execute(store, goal)
        assert result.succeeded
        tg     = result.delta.modified[0]
        assert tg.id == ArtifactId("task_graph")
        task_001 = next(t for t in tg.content["tasks"] if t["id"] == "task_001")
        assert task_001["status"] == "pending"

    def test_appends_lessons_to_description(self, goal):
        store  = _make_store()
        result = CodeRegenerator(llm=SequencedLLM(["Avoid N+1 queries."])).execute(store, goal)
        tg     = result.delta.modified[0]
        desc   = next(t["description"] for t in tg.content["tasks"] if t["id"] == "task_001")
        assert "Avoid N+1 queries." in desc

    def test_deletes_source_artifacts_for_failing_task(self, goal):
        store  = _make_store()
        result = CodeRegenerator(llm=SequencedLLM(["Lesson."])).execute(store, goal)
        assert ArtifactId("src/src/models.py") in result.delta.deleted

    def test_deletes_test_artifacts_for_failing_task(self, goal):
        store  = _make_store()
        result = CodeRegenerator(llm=SequencedLLM(["Lesson."])).execute(store, goal)
        assert ArtifactId("test/tests/test_models.py") in result.delta.deleted

    def test_deletes_test_report(self, goal):
        store  = _make_store()
        result = CodeRegenerator(llm=SequencedLLM(["Lesson."])).execute(store, goal)
        assert ArtifactId("test_report") in result.delta.deleted

    def test_fallback_to_last_done_task_when_no_traceback(self, goal):
        """When output has no file paths, reset the last done task."""
        store  = _make_store(test_output="generic error with no file references")
        result = CodeRegenerator(llm=SequencedLLM(["Lesson."])).execute(store, goal)
        assert result.succeeded
        tg     = result.delta.modified[0]
        assert any(t["status"] == "pending" for t in tg.content["tasks"])

    def test_only_deletes_implicated_task_artifacts(self, goal):
        """A source for a different task should NOT be deleted."""
        store = _make_store(
            sources=[
                _src("src/models.py", "task_001"),
                _src("src/routes.py", "task_002"),
            ],
        )
        result  = CodeRegenerator(llm=SequencedLLM(["Lesson."])).execute(store, goal)
        deleted = set(result.delta.deleted)
        assert ArtifactId("src/src/models.py") in deleted
        assert ArtifactId("src/src/routes.py") not in deleted
