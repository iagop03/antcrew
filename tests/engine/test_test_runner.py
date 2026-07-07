"""Tests for TestRunner and TestGenerator capabilities."""
from __future__ import annotations

import pytest

from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind, ConditionId, MemoryStore,
)
from antcrew.capabilities.test_generator import TestGenerator, _to_test_path
from antcrew.capabilities.test_runner import TestRunner
from antcrew.capabilities.validators import TestsExistValidator, TestsPassValidator
from antcrew.models.simulated import SimulatedLLM


@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def goal():
    from antcrew.engine import Condition, ConditionId, DesiredProjectState, Goal
    return Goal(
        description="Build a todo API",
        desired_state=DesiredProjectState(frozenset([
            Condition(ConditionId("tests_pass"), "all tests pass"),
        ])),
    )


def _source_store():
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
        content="def add(a, b):\n    return a + b\n",
        metadata={"file_path": "src/models.py"},
    ))
    return store


# ---------------------------------------------------------------------------
# TestGenerator
# ---------------------------------------------------------------------------

class TestTestGeneratorUnit:
    def test_creates_one_test_per_source(self, llm, goal):
        result = TestGenerator(llm=llm).execute(_source_store(), goal)
        assert result.succeeded
        assert len(result.delta.created) == 1
        assert result.delta.created[0].kind == ArtifactKind.TEST

    def test_test_artifact_id_prefixed(self, llm, goal):
        result = TestGenerator(llm=llm).execute(_source_store(), goal)
        assert str(result.delta.created[0].id).startswith("test/")

    def test_error_when_no_sources(self, llm, goal):
        result = TestGenerator(llm=llm).execute(MemoryStore(), goal)
        assert not result.succeeded

    def test_multiple_sources_multiple_tests(self, llm, goal):
        store = MemoryStore()
        for name in ("models", "routes", "utils"):
            store.write(Artifact(
                id=ArtifactId(f"src/{name}.py"), kind=ArtifactKind.SOURCE,
                content=f"# {name}", metadata={"file_path": f"src/{name}.py"},
            ))
        result = TestGenerator(llm=llm).execute(store, goal)
        assert len(result.delta.created) == 3


class TestToTestPath:
    def test_src_file_becomes_test_file(self):
        assert _to_test_path("src/models.py") == "tests/test_models.py"

    def test_nested_path(self):
        # Sub-directory structure is preserved to avoid name collisions
        # (src/api/routes.py and src/auth/routes.py must produce different test paths)
        result = _to_test_path("src/api/routes.py")
        assert result.replace("\\", "/") == "tests/api/test_routes.py"

    def test_root_file(self):
        assert _to_test_path("main.py") == "tests/test_main.py"


# ---------------------------------------------------------------------------
# TestRunner — end-to-end with real subprocess
# ---------------------------------------------------------------------------

PASSING_TEST = "def test_always_passes():\n    assert 1 + 1 == 2\n"
FAILING_TEST = "def test_always_fails():\n    assert False, 'intentional failure'\n"


def _runner_store(test_content: str) -> MemoryStore:
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("test/tests/test_example.py"), kind=ArtifactKind.TEST,
        content=test_content,
        metadata={"file_path": "tests/test_example.py"},
    ))
    return store


class TestTestRunnerUnit:
    def test_returns_report_artifact(self, goal):
        result = TestRunner().execute(_runner_store(PASSING_TEST), goal)
        assert result.succeeded
        assert result.delta.created[0].id == ArtifactId("test_report")
        assert result.delta.created[0].kind == ArtifactKind.REPORT

    def test_passing_tests_satisfied(self, goal):
        result = TestRunner().execute(_runner_store(PASSING_TEST), goal)
        report = result.delta.created[0].content
        assert report["passed"] is True
        assert report["returncode"] == 0

    def test_failing_tests_not_satisfied(self, goal):
        result = TestRunner().execute(_runner_store(FAILING_TEST), goal)
        report = result.delta.created[0].content
        assert report["passed"] is False
        assert report["returncode"] != 0

    def test_report_includes_output(self, goal):
        result = TestRunner().execute(_runner_store(PASSING_TEST), goal)
        report = result.delta.created[0].content
        assert isinstance(report["output"], str)

    def test_error_when_no_test_artifacts(self, goal):
        result = TestRunner().execute(MemoryStore(), goal)
        assert not result.succeeded


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class TestTestsExistValidator:
    def test_false_with_empty_store(self):
        assert not TestsExistValidator().validate(MemoryStore()).satisfied

    def test_true_when_test_artifact_present(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("t"), kind=ArtifactKind.TEST, content="x"))
        assert TestsExistValidator().validate(store).satisfied


class TestTestsPassValidator:
    def test_false_with_no_report(self):
        assert not TestsPassValidator().validate(MemoryStore()).satisfied

    def test_true_when_passed(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
                             content={"passed": True, "returncode": 0, "output": ""}))
        assert TestsPassValidator().validate(store).satisfied

    def test_false_when_failed(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
                             content={"passed": False, "returncode": 1, "output": "FAILED"}))
        assert not TestsPassValidator().validate(store).satisfied
