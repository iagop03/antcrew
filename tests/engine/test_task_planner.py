"""Tests for TaskPlanner capability."""
from __future__ import annotations

import json
import pytest

from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind, ConditionId, MemoryStore,
)
from antcrew.capabilities.task_planner import TaskPlanner, _safe_parse_tasks
from antcrew.capabilities.validators import AllTasksCompletedValidator
from antcrew.models.simulated import SimulatedLLM


ARCH_CONTENT = "# Architecture\n\n## Components\n- API layer\n- Data layer"

VALID_TASKS_JSON = json.dumps([
    {"id": "task_001", "title": "Setup project", "description": "Init pyproject.toml",
     "files": ["pyproject.toml"], "depends_on": []},
    {"id": "task_002", "title": "Create models", "description": "SQLModel entities",
     "files": ["src/models.py"], "depends_on": ["task_001"]},
])


@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def store_with_arch():
    store = MemoryStore()
    store.write(Artifact(id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
                         content=ARCH_CONTENT))
    return store


@pytest.fixture
def goal(tmp_path):
    from antcrew.engine import Condition, ConditionId, Constraints, DesiredProjectState, Goal
    return Goal(
        description="Build a todo API",
        desired_state=DesiredProjectState(frozenset([
            Condition(ConditionId("task_graph_exists"), "task graph created"),
        ])),
        constraints=Constraints(tech_stack=("Python", "FastAPI")),
    )


class TestTaskPlannerUnit:
    def test_creates_task_graph_artifact(self, llm, store_with_arch, goal):
        result = TaskPlanner(llm=llm).execute(store_with_arch, goal)
        assert result.succeeded
        assert len(result.delta.created) == 1
        art = result.delta.created[0]
        assert art.id   == ArtifactId("task_graph")
        assert art.kind == ArtifactKind.TASK_GRAPH

    def test_content_has_tasks_key(self, llm, store_with_arch, goal):
        result  = TaskPlanner(llm=llm).execute(store_with_arch, goal)
        content = result.delta.created[0].content
        assert isinstance(content, dict)
        assert "tasks" in content

    def test_tasks_have_pending_status(self, llm, store_with_arch, goal):
        result = TaskPlanner(llm=llm).execute(store_with_arch, goal)
        tasks  = result.delta.created[0].content["tasks"]
        assert all(t.get("status") == "pending" for t in tasks)

    def test_reads_architecture_from_store(self, goal):
        received = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                received.append(user)
                return super().system(prompt, user, **kw)

        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
                             content="UNIQUE_ARCH_MARKER"))
        TaskPlanner(llm=SpyLLM()).execute(store, goal)
        assert "UNIQUE_ARCH_MARKER" in received[0]

    def test_works_without_architecture_in_store(self, llm, goal):
        result = TaskPlanner(llm=llm).execute(MemoryStore(), goal)
        assert result.succeeded


class TestSafeParseTasks:
    def test_parses_valid_json_array(self):
        tasks = _safe_parse_tasks(VALID_TASKS_JSON)
        assert len(tasks) == 2
        assert tasks[0]["id"] == "task_001"

    def test_parses_json_with_fences(self):
        fenced = f"```json\n{VALID_TASKS_JSON}\n```"
        tasks  = _safe_parse_tasks(fenced)
        assert len(tasks) == 2

    def test_parses_dict_with_tasks_key(self):
        wrapped = json.dumps({"tasks": json.loads(VALID_TASKS_JSON)})
        tasks   = _safe_parse_tasks(wrapped)
        assert len(tasks) == 2

    def test_returns_empty_list_on_invalid_json(self):
        assert _safe_parse_tasks("not json at all") == []

    def test_returns_empty_list_on_empty_string(self):
        assert _safe_parse_tasks("") == []


class TestAllTasksCompletedValidator:
    def test_false_when_no_task_graph(self):
        v = AllTasksCompletedValidator()
        r = v.validate(MemoryStore())
        assert not r.satisfied

    def test_false_when_tasks_pending(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH,
                             content={"tasks": [{"id": "t1", "status": "pending"}]}))
        v = AllTasksCompletedValidator()
        assert not v.validate(store).satisfied

    def test_true_when_all_done(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH,
                             content={"tasks": [{"id": "t1", "status": "done"}]}))
        v = AllTasksCompletedValidator()
        assert v.validate(store).satisfied

    def test_false_when_tasks_list_empty(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH,
                             content={"tasks": []}))
        v = AllTasksCompletedValidator()
        assert not v.validate(store).satisfied

    def test_metrics_include_counts(self):
        store = MemoryStore()
        store.write(Artifact(id=ArtifactId("task_graph"), kind=ArtifactKind.TASK_GRAPH,
                             content={"tasks": [
                                 {"id": "t1", "status": "done"},
                                 {"id": "t2", "status": "pending"},
                             ]}))
        r = AllTasksCompletedValidator().validate(store)
        assert r.observations["tasks_done"]  == 1
        assert r.observations["tasks_total"] == 2
