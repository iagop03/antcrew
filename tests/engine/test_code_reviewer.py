"""Tests for CodeReviewer capability and full pipeline integration."""
from __future__ import annotations

import json
import pytest

from antcrew.engine import (
    Artifact, ArtifactId, ArtifactKind,
    CapabilityRegistry, Condition, ConditionId, Constraints,
    DesiredProjectState, EventLog, Goal, MemoryStore, Operator,
)
from antcrew.capabilities.architect import Architect
from antcrew.capabilities.code_generator import CodeGenerator
from antcrew.capabilities.code_reviewer import CodeReviewer, _safe_parse_review
from antcrew.capabilities.spec_extractor import SpecExtractor
from antcrew.capabilities.task_planner import TaskPlanner
from antcrew.capabilities.test_generator import TestGenerator
from antcrew.capabilities.test_runner import TestRunner
from antcrew.capabilities.validators import (
    AllTasksCompletedValidator, ArtifactExistsValidator,
    CodeReviewedValidator, TestsExistValidator, TestsPassValidator,
    artifact_validators,
)
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def store_with_sources():
    store = MemoryStore()
    store.write(Artifact(
        id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
        content="# Architecture\n\n## Components\n- FastAPI app",
    ))
    store.write(Artifact(
        id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
        content="class TodoItem:\n    def __init__(self, title: str):\n        self.title = title\n",
        metadata={"file_path": "src/models.py"},
    ))
    store.write(Artifact(
        id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
        content={"passed": True, "returncode": 0, "output": "1 passed"},
    ))
    return store


@pytest.fixture
def goal():
    return Goal(
        description="Build a todo API",
        desired_state=DesiredProjectState(frozenset([
            Condition(ConditionId("code_reviewed"), "code reviewed and approved"),
        ])),
        constraints=Constraints(tech_stack=("Python", "FastAPI")),
    )


# ---------------------------------------------------------------------------
# Unit: CodeReviewer._run()
# ---------------------------------------------------------------------------

class TestCodeReviewerUnit:
    def test_creates_review_report_artifact(self, llm, store_with_sources, goal):
        result = CodeReviewer(llm=llm).execute(store_with_sources, goal)
        assert result.succeeded
        assert result.delta.created[0].id   == ArtifactId("review_report")
        assert result.delta.created[0].kind == ArtifactKind.REPORT

    def test_review_content_has_required_keys(self, llm, store_with_sources, goal):
        result  = CodeReviewer(llm=llm).execute(store_with_sources, goal)
        content = result.delta.created[0].content
        assert isinstance(content, dict)
        assert "summary"  in content
        assert "verdict"  in content
        assert "findings" in content

    def test_source_content_sent_to_llm(self, goal):
        received = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                received.append(user)
                return super().system(prompt, user, **kw)

        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
            content="UNIQUE_SOURCE_MARKER",
            metadata={"file_path": "src/models.py"},
        ))
        store.write(Artifact(
            id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
            content={"passed": True, "returncode": 0, "output": ""},
        ))
        CodeReviewer(llm=SpyLLM()).execute(store, goal)
        assert "UNIQUE_SOURCE_MARKER" in received[0]

    def test_architecture_included_in_prompt(self, goal):
        received = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                received.append(user)
                return super().system(prompt, user, **kw)

        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("architecture"), kind=ArtifactKind.ARCHITECTURE,
            content="UNIQUE_ARCH_MARKER",
        ))
        store.write(Artifact(
            id=ArtifactId("src/models.py"), kind=ArtifactKind.SOURCE,
            content="x = 1", metadata={"file_path": "src/models.py"},
        ))
        store.write(Artifact(
            id=ArtifactId("test_report"), kind=ArtifactKind.REPORT,
            content={"passed": True, "returncode": 0, "output": ""},
        ))
        CodeReviewer(llm=SpyLLM()).execute(store, goal)
        assert "UNIQUE_ARCH_MARKER" in received[0]

    def test_error_when_no_sources(self, llm, goal):
        result = CodeReviewer(llm=llm).execute(MemoryStore(), goal)
        assert not result.succeeded

    def test_no_llm_returns_error(self, store_with_sources, goal):
        result = CodeReviewer().execute(store_with_sources, goal)
        assert not result.succeeded


class TestSafeParseReview:
    def test_parses_valid_json(self):
        raw = json.dumps({
            "summary": "Looks good", "verdict": "approved",
            "findings": [{"file": "src/x.py", "severity": "info",
                          "message": "ok", "suggestion": ""}],
        })
        review = _safe_parse_review(raw)
        assert review["verdict"] == "approved"

    def test_fenced_json(self):
        raw = '```json\n{"summary": "ok", "verdict": "approved", "findings": []}\n```'
        assert _safe_parse_review(raw)["verdict"] == "approved"

    def test_fallback_on_invalid_json(self):
        review = _safe_parse_review("not json")
        assert review["verdict"] == "needs_changes"
        assert "not json" in review["summary"]


# ---------------------------------------------------------------------------
# CodeReviewedValidator
# ---------------------------------------------------------------------------

class TestCodeReviewedValidator:
    def test_false_when_no_report(self):
        assert not CodeReviewedValidator().validate(MemoryStore()).satisfied

    def test_true_when_approved(self):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("review_report"), kind=ArtifactKind.REPORT,
            content={"verdict": "approved", "summary": "", "findings": []},
        ))
        assert CodeReviewedValidator().validate(store).satisfied

    def test_false_when_needs_changes(self):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("review_report"), kind=ArtifactKind.REPORT,
            content={"verdict": "needs_changes", "summary": "", "findings": [
                {"file": "x.py", "severity": "error", "message": "bug", "suggestion": "fix it"},
            ]},
        ))
        r = CodeReviewedValidator().validate(store)
        assert not r.satisfied
        assert r.observations["critical_findings"] == 1


# ---------------------------------------------------------------------------
# Full pipeline integration: SpecExtractor → Architect → TaskPlanner →
#   CodeGenerator (×N) → TestGenerator → TestRunner → CodeReviewer
# ---------------------------------------------------------------------------

def _all_validators():
    return [
        *artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
            ("task_graph",   "task_graph_exists"),
        ),
        AllTasksCompletedValidator(),
        TestsExistValidator(),
        TestsPassValidator(),
        CodeReviewedValidator(),
    ]


def _full_registry(llm):
    registry = CapabilityRegistry()
    registry.register(SpecExtractor(llm=llm))
    registry.register(Architect(llm=llm))
    registry.register(TaskPlanner(llm=llm))
    registry.register(CodeGenerator(llm=llm))
    registry.register(TestGenerator(llm=llm))
    registry.register(TestRunner())
    registry.register(CodeReviewer(llm=llm))
    return registry


class TestFullPipeline:
    def test_all_artifacts_created(self, llm):
        """Operator drives the full pipeline to completion with SimulatedLLM."""
        goal = Goal(
            description="Build a minimal todo API in FastAPI",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements written"),
                Condition(ConditionId("architecture_exists"), "architecture designed"),
                Condition(ConditionId("task_graph_exists"),   "tasks planned"),
            ])),
            constraints=Constraints(tech_stack=("Python", "FastAPI")),
        )
        store    = MemoryStore()
        log      = EventLog()
        operator = Operator(_full_registry(llm), _all_validators(), log, max_iterations=30)

        final_state = operator.run(store, goal)

        assert ConditionId("requirements_exists") in final_state.satisfied
        assert ConditionId("architecture_exists") in final_state.satisfied
        assert ConditionId("task_graph_exists")   in final_state.satisfied
        assert store.has(ArtifactId("requirements"))
        assert store.has(ArtifactId("architecture"))
        assert store.has(ArtifactId("task_graph"))

    def test_event_log_records_full_sequence(self, llm):
        goal = Goal(
            description="Build a minimal API",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements"),
                Condition(ConditionId("architecture_exists"), "architecture"),
            ])),
        )
        store = MemoryStore()
        log   = EventLog()
        Operator(_full_registry(llm), _all_validators(), log, max_iterations=20).run(store, goal)

        dispatched = [e.capability_name for e in log.events("capability_dispatched")]
        assert "spec_extractor" in dispatched
        assert "architect"      in dispatched
        assert dispatched.index("spec_extractor") < dispatched.index("architect")

    def test_engine_finishes_successfully(self, llm):
        goal = Goal(
            description="Build a minimal API",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements"),
            ])),
        )
        log = EventLog()
        Operator(_full_registry(llm), _all_validators(), log, max_iterations=10).run(MemoryStore(), goal)

        finished = log.events("engine_finished")
        assert len(finished) == 1
        assert finished[0].success is True
