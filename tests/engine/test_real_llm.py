"""Integration tests against a real LLM (AnthropicModel).

Skipped automatically when ANTHROPIC_API_KEY is not set.
Run manually:
    pytest tests/engine/test_real_llm.py -v -s

Scope: kept narrow (SpecExtractor → Architect → TaskPlanner only) to minimise
cost and latency.  Each test makes 1-3 real API calls.
"""
from __future__ import annotations

import os
import pytest

from antcrew.engine import (
    ArtifactId, ArtifactKind,
    CapabilityRegistry, Condition, ConditionId, Constraints,
    DesiredProjectState, EventLog, FilesystemStore, Goal, MemoryStore, Operator,
)
from antcrew.capabilities import Architect, SpecExtractor, TaskPlanner
from antcrew.capabilities.validators import artifact_validators


# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
pytestmark = pytest.mark.skipif(not _HAS_KEY, reason="ANTHROPIC_API_KEY not set")


def _llm():
    from antcrew.models.anthropic_model import AnthropicModel
    return AnthropicModel()


def _goal(description: str, *cond_pairs: tuple[str, str]) -> Goal:
    conds = frozenset(Condition(ConditionId(cid), desc) for cid, desc in cond_pairs)
    return Goal(
        description=description,
        desired_state=DesiredProjectState(conds),
        constraints=Constraints(tech_stack=("Python",)),
    )


def _registry(*executors) -> CapabilityRegistry:
    r = CapabilityRegistry()
    for e in executors:
        r.register(e)
    return r


# ---------------------------------------------------------------------------
# SpecExtractor — real LLM
# ---------------------------------------------------------------------------

class TestSpecExtractorReal:
    def test_produces_requirements_artifact(self):
        llm   = _llm()
        store = MemoryStore()
        goal  = _goal("Build a Python function to calculate factorial",
                      ("requirements_exists", "requirements written"))

        result = SpecExtractor(llm=llm).execute(store, goal)

        assert result.succeeded, result.errors
        artifact = result.delta.created[0]
        assert artifact.id   == ArtifactId("requirements")
        assert artifact.kind == ArtifactKind.REQUIREMENTS
        assert len(artifact.content) > 100

    def test_content_references_goal(self):
        llm   = _llm()
        store = MemoryStore()
        goal  = _goal("Build a UNIQUE_MARKER_XYZ todo list application",
                      ("requirements_exists", "requirements"))

        result = SpecExtractor(llm=llm).execute(store, goal)

        assert "UNIQUE_MARKER_XYZ" in result.delta.created[0].content \
            or "todo" in result.delta.created[0].content.lower()


# ---------------------------------------------------------------------------
# Architect — real LLM (reads from store populated by SpecExtractor)
# ---------------------------------------------------------------------------

class TestArchitectReal:
    def test_produces_architecture_artifact(self):
        llm   = _llm()
        store = MemoryStore()
        goal  = _goal("Build a FastAPI todo API",
                      ("architecture_exists", "architecture designed"))

        # Pre-populate requirements so Architect has something to read.
        SpecExtractor(llm=llm).execute(store, goal)
        store.apply(SpecExtractor(llm=llm).execute(store, goal).delta)  # idempotent, updates store
        result = Architect(llm=llm).execute(store, goal)

        assert result.succeeded, result.errors
        arch = result.delta.created[0]
        assert arch.id   == ArtifactId("architecture")
        assert arch.kind == ArtifactKind.ARCHITECTURE
        assert len(arch.content) > 200

    def test_architecture_mentions_python(self):
        llm   = _llm()
        store = MemoryStore()
        goal  = _goal("Build a Python CLI tool that reads CSV files",
                      ("architecture_exists", "architecture"))

        SpecExtractor(llm=llm).execute(store, goal)
        result = Architect(llm=llm).execute(store, goal)

        assert "python" in result.delta.created[0].content.lower() \
            or "csv" in result.delta.created[0].content.lower()


# ---------------------------------------------------------------------------
# TaskPlanner — real LLM
# ---------------------------------------------------------------------------

class TestTaskPlannerReal:
    def test_produces_valid_task_list(self):
        llm   = _llm()
        store = MemoryStore()
        goal  = _goal("Build a Python CLI todo manager",
                      ("task_graph_exists", "tasks planned"))

        SpecExtractor(llm=llm).execute(store, goal)
        Architect(llm=llm).execute(store, goal)
        result = TaskPlanner(llm=llm).execute(store, goal)

        assert result.succeeded, result.errors
        tg    = result.delta.created[0]
        tasks = tg.content.get("tasks", [])
        assert len(tasks) >= 1
        for t in tasks:
            assert "id"    in t
            assert "title" in t
            assert t.get("status") == "pending"


# ---------------------------------------------------------------------------
# Operator loop — real LLM, plan-only (SpecExtractor → Architect → TaskPlanner)
# ---------------------------------------------------------------------------

class TestOperatorPlanLoop:
    def test_reaches_three_conditions(self):
        llm = _llm()
        registry = _registry(
            SpecExtractor(llm=llm),
            Architect(llm=llm),
            TaskPlanner(llm=llm),
        )
        validators = artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
            ("task_graph",   "task_graph_exists"),
        )
        goal = _goal(
            "Build a Python CLI tool for managing personal tasks",
            ("requirements_exists", "requirements written"),
            ("architecture_exists", "architecture designed"),
            ("task_graph_exists",   "tasks planned"),
        )
        store = MemoryStore()
        log   = EventLog()
        state = Operator(registry, validators, log, max_iterations=15).run(store, goal)

        assert ConditionId("requirements_exists") in state.satisfied
        assert ConditionId("architecture_exists") in state.satisfied
        assert ConditionId("task_graph_exists")   in state.satisfied

    def test_artifacts_persisted_to_filesystem(self, tmp_path):
        """When using FilesystemStore the artifacts survive after run."""
        llm = _llm()
        registry = _registry(SpecExtractor(llm=llm), Architect(llm=llm))
        validators = artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
        )
        goal = _goal(
            "Build a Python function that checks if a string is a palindrome",
            ("requirements_exists", "requirements written"),
            ("architecture_exists", "architecture designed"),
        )
        store = FilesystemStore(tmp_path)
        Operator(registry, validators, [], max_iterations=10).run(store, goal)

        # After the run, a new FilesystemStore on the same path should see the artifacts.
        s2 = FilesystemStore(tmp_path)
        assert s2.has(ArtifactId("requirements"))
        assert s2.has(ArtifactId("architecture"))
        req = s2.read(ArtifactId("requirements"))
        assert len(req.content) > 50
