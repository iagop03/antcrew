"""Tests for antcrew.core.task_classifier."""
from __future__ import annotations

import pytest

from antcrew.core.task_classifier import (
    _AGENTS_FOR_TYPE,
    MinimalPipeline,
    TaskType,
    classify_task,
)
from antcrew.models.simulated import SimulatedLLM


class TestClassifyTask:
    def test_fix_keywords(self):
        assert classify_task("Fix the failing test in auth.py") == TaskType.FIX
        assert classify_task("There is a bug in login") == TaskType.FIX
        assert classify_task("The app crashes on startup") == TaskType.FIX

    def test_refactor_keywords(self):
        assert classify_task("Refactor the auth module") == TaskType.REFACTOR
        assert classify_task("Clean up the database layer") == TaskType.REFACTOR
        assert classify_task("Extract the payment logic into a service") == TaskType.REFACTOR

    def test_test_keywords(self):
        assert classify_task("Write tests for the user service") == TaskType.TEST
        assert classify_task("Add pytest coverage for auth") == TaskType.TEST

    def test_docs_keywords(self):
        assert classify_task("Update the README with installation steps") == TaskType.DOCS
        assert classify_task("Write documentation for the API") == TaskType.DOCS

    def test_feature_keywords(self):
        assert classify_task("Build a login endpoint") == TaskType.FEATURE
        assert classify_task("Implement JWT authentication") == TaskType.FEATURE
        assert classify_task("Add user profile model") == TaskType.FEATURE

    def test_fallback_to_feature(self):
        # Request with no recognisable keywords defaults to full feature pipeline
        assert classify_task("xyzzy frobnicate blorb") == TaskType.FEATURE

    def test_case_insensitive(self):
        assert classify_task("FIX THE BUG") == TaskType.FIX
        assert classify_task("REFACTOR auth") == TaskType.REFACTOR


class TestPipelineConfig:
    def test_fix_uses_minimal_agents(self):
        assert "business_analyst" not in _AGENTS_FOR_TYPE[TaskType.FIX]
        assert "backend_dev" in _AGENTS_FOR_TYPE[TaskType.FIX]

    def test_test_uses_qa_only(self):
        assert _AGENTS_FOR_TYPE[TaskType.TEST] == ["qa"]

    def test_docs_uses_doc_writer_only(self):
        assert _AGENTS_FOR_TYPE[TaskType.DOCS] == ["doc_writer"]

    def test_feature_uses_full_pipeline(self):
        agents = _AGENTS_FOR_TYPE[TaskType.FEATURE]
        assert "business_analyst" in agents
        assert "pm" in agents
        assert "backend_dev" in agents


class TestMinimalPipeline:
    def test_accepts_model(self):
        p = MinimalPipeline(SimulatedLLM())
        assert p._model is not None

    def test_forced_task_type(self):
        p = MinimalPipeline(SimulatedLLM(), task_type=TaskType.FIX)
        assert p._classify("build a full feature") == TaskType.FIX

    def test_forced_task_type_from_string(self):
        p = MinimalPipeline(SimulatedLLM(), task_type="refactor")
        assert p._classify("anything") == TaskType.REFACTOR

    def test_auto_classification(self):
        p = MinimalPipeline(SimulatedLLM())
        assert p._classify("fix the bug") == TaskType.FIX

    def test_build_team_returns_team(self):
        p = MinimalPipeline(SimulatedLLM())
        team = p._build_team(TaskType.FIX)
        assert hasattr(team, "run")

    def test_build_team_single_node_types(self):
        p = MinimalPipeline(SimulatedLLM())
        for tt in (TaskType.TEST, TaskType.DOCS):
            team = p._build_team(tt)
            assert hasattr(team, "run")

    def test_task_type_enum_values(self):
        assert TaskType("fix") == TaskType.FIX
        assert TaskType("feature") == TaskType.FEATURE
        with pytest.raises(ValueError):
            TaskType("invalid_type")
