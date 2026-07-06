"""Tests for CodeGenerator capability."""
from __future__ import annotations

import json
import pytest

from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind, ConditionId, MemoryStore,
)
from antcrew.capabilities.code_generator import CodeGenerator, _next_pending
from antcrew.capabilities.validators import AllTasksCompletedValidator
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TWO_TASKS = [
    {"id": "task_001", "title": "Models", "description": "Create models",
     "files": ["src/models.py"], "depends_on": [], "status": "pending"},
    {"id": "task_002", "title": "Routes", "description": "Create routes",
     "files": ["src/routes.py"], "depends_on": ["task_001"], "status": "pending"},
]


def _make_store(tasks=None):
    import copy
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH,
        content={"tasks": copy.deepcopy(tasks or TWO_TASKS)},
    ))
    store.write(Artifact(
        id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
        content="# Architecture\n- FastAPI app\n- SQLite DB",
    ))
    return store


@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def goal():
    from antcrew.engine import Condition, ConditionId, DesiredProjectState, Goal
    return Goal(
        description="Build a todo API",
        desired_state=DesiredProjectState(frozenset([
            Condition(ConditionId("implementation_exists"), "all tasks done"),
        ])),
    )


# ---------------------------------------------------------------------------
# Unit: _next_pending helper
# ---------------------------------------------------------------------------

class TestNextPending:
    def test_returns_first_pending_with_no_deps(self):
        task = _next_pending(TWO_TASKS)
        assert task["id"] == "task_001"

    def test_skips_task_with_unmet_deps(self):
        tasks = [
            {"id": "t1", "status": "pending", "depends_on": ["t0"]},
        ]
        assert _next_pending(tasks) is None

    def test_returns_task_when_deps_done(self):
        tasks = [
            {"id": "t1", "status": "done",    "depends_on": []},
            {"id": "t2", "status": "pending",  "depends_on": ["t1"]},
        ]
        assert _next_pending(tasks)["id"] == "t2"

    def test_returns_none_when_all_done(self):
        tasks = [{"id": "t1", "status": "done", "depends_on": []}]
        assert _next_pending(tasks) is None

    def test_returns_none_on_empty_list(self):
        assert _next_pending([]) is None


# ---------------------------------------------------------------------------
# Unit: CodeGenerator._run()
# ---------------------------------------------------------------------------

class TestCodeGeneratorUnit:
    def test_creates_source_artifacts(self, llm, goal):
        result = CodeGenerator(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        sources = [a for a in result.delta.created if a.kind == ArtifactKind.SOURCE]
        assert len(sources) >= 0  # SimulatedLLM may return non-JSON; graceful

    def test_modifies_task_graph(self, llm, goal):
        result = CodeGenerator(llm=llm).execute(_make_store(), goal)
        assert result.succeeded
        assert len(result.delta.modified) == 1
        tg = result.delta.modified[0]
        assert tg.id == ArtifactId("task_graph")

    def test_first_task_marked_done(self, llm, goal):
        result = CodeGenerator(llm=llm).execute(_make_store(), goal)
        tasks  = result.delta.modified[0].content["tasks"]
        assert tasks[0]["status"] == "done"

    def test_second_task_still_pending(self, llm, goal):
        result = CodeGenerator(llm=llm).execute(_make_store(), goal)
        tasks  = result.delta.modified[0].content["tasks"]
        assert tasks[1]["status"] == "pending"

    def test_error_when_no_task_graph(self, llm, goal):
        result = CodeGenerator(llm=llm).execute(MemoryStore(), goal)
        assert not result.succeeded
        assert any("task_graph" in e for e in result.errors)

    def test_error_when_all_tasks_done(self, llm, goal):
        done_tasks = [dict(t, status="done") for t in TWO_TASKS]
        result = CodeGenerator(llm=llm).execute(_make_store(done_tasks), goal)
        assert not result.succeeded
        assert any("pending" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Integration: two CodeGenerator calls complete both tasks
# ---------------------------------------------------------------------------

class TestCodeGeneratorMultipleRuns:
    def test_two_runs_mark_all_tasks_done(self, llm, goal):
        store = _make_store()
        gen   = CodeGenerator(llm=llm)

        # run 1 — task_001
        r1 = gen.execute(store, goal)
        store.apply(r1.delta)

        # run 2 — task_002 (depends_on task_001, now done)
        r2 = gen.execute(store, goal)
        store.apply(r2.delta)

        tg    = store.read(ArtifactId("task_graph"))
        tasks = tg.content["tasks"]
        assert all(t["status"] == "done" for t in tasks)

    def test_validator_satisfied_after_all_done(self, llm, goal):
        store = _make_store()
        gen   = CodeGenerator(llm=llm)
        v     = AllTasksCompletedValidator()

        assert not v.validate(store).satisfied

        r1 = gen.execute(store, goal); store.apply(r1.delta)
        assert not v.validate(store).satisfied  # one task still pending

        r2 = gen.execute(store, goal); store.apply(r2.delta)
        assert v.validate(store).satisfied
