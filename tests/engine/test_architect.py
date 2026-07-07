"""Tests for Architect capability."""
from __future__ import annotations

import pytest

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityRegistry, Condition, ConditionId, Constraints,
    DesiredProjectState, EventLog, Goal, MemoryStore, Operator,
)
from antcrew.capabilities.architect import Architect
from antcrew.capabilities.spec_extractor import SpecExtractor
from antcrew.capabilities.validators import ArtifactExistsValidator, artifact_validators
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REQUIREMENTS_CONTENT = """\
# Requirements

## Objective
Build a REST API for managing todo items.

## Functional Requirements
1. The system MUST allow creating, reading, updating, and deleting todo items.
2. The system MUST support user authentication via API key.

## Acceptance Criteria
1. GET /todos returns a list of items.
2. POST /todos creates a new item and returns it.
"""


@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def store_with_requirements():
    store = MemoryStore()
    store.write(Artifact(
        id      = ArtifactId("requirements"),
        kind    = ArtifactKind.REQUIREMENTS,
        content = REQUIREMENTS_CONTENT,
    ))
    return store


@pytest.fixture
def goal():
    req_cond  = Condition(ConditionId("requirements_exists"), "requirements.md present")
    arch_cond = Condition(ConditionId("architecture_exists"), "architecture.md present")
    return Goal(
        description   = "Build a REST API for a todo list application",
        desired_state = DesiredProjectState(frozenset([req_cond, arch_cond])),
        constraints   = Constraints(tech_stack=("Python", "FastAPI", "SQLite")),
    )


# ---------------------------------------------------------------------------
# Unit: Architect._run()
# ---------------------------------------------------------------------------

class TestArchitectUnit:
    def test_creates_architecture_artifact(self, llm, store_with_requirements, goal):
        result = Architect(llm=llm).execute(store_with_requirements, goal)

        assert result.succeeded
        arch = next(a for a in result.delta.created if a.id == ArtifactId("architecture"))
        assert arch.kind == ArtifactKind.ARCHITECTURE

    def test_artifact_content_is_non_empty(self, llm, store_with_requirements, goal):
        result  = Architect(llm=llm).execute(store_with_requirements, goal)
        content = result.delta.created[0].content
        assert isinstance(content, str) and len(content) > 0

    def test_delta_touched_includes_architecture(self, llm, store_with_requirements, goal):
        result = Architect(llm=llm).execute(store_with_requirements, goal)
        assert ArtifactId("architecture") in result.delta.touched

    def test_goal_description_passed_to_llm(self, goal):
        """Verify the goal description is passed directly to the LLM."""
        received: list[str] = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                received.append(user)
                return super().system(prompt, user, **kw)

        Architect(llm=SpyLLM()).execute(MemoryStore(), goal)
        assert "Build a REST API for a todo list application" in received[0]

    def test_creates_requirements_stub_artifact(self, llm, goal):
        """Architect writes a requirements stub so requirements_exists validator passes."""
        result = Architect(llm=llm).execute(MemoryStore(), goal)
        req = next(a for a in result.delta.created if a.id == ArtifactId("requirements"))
        assert req.kind == ArtifactKind.REQUIREMENTS
        assert "Build a REST API for a todo list application" in req.content

    def test_constraints_passed_to_llm(self, goal):
        received: list[str] = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                received.append(user)
                return super().system(prompt, user, **kw)

        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("requirements"),
            kind=ArtifactKind.REQUIREMENTS,
            content="requirements",
        ))
        Architect(llm=SpyLLM()).execute(store, goal)
        assert "FastAPI" in received[0]
        assert "SQLite"  in received[0]

    def test_works_without_requirements_in_store(self, llm, goal):
        """Architect degrades gracefully when requirements artifact is missing."""
        result = Architect(llm=llm).execute(MemoryStore(), goal)
        assert result.succeeded

    def test_no_llm_returns_error_result(self, store_with_requirements, goal):
        result = Architect().execute(store_with_requirements, goal)
        assert not result.succeeded
        assert any("RuntimeError" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Integration: SpecExtractor → Architect in one Operator loop
# ---------------------------------------------------------------------------

class TestArchitectInLoop:
    def test_operator_satisfies_both_conditions(self, llm, goal):
        store    = MemoryStore()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        registry.register(Architect(llm=llm))

        validators = artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
        )
        final_state = Operator(registry, validators, EventLog()).run(store, goal)

        assert ConditionId("requirements_exists") in final_state.satisfied
        assert ConditionId("architecture_exists") in final_state.satisfied
        assert store.has(ArtifactId("requirements"))
        assert store.has(ArtifactId("architecture"))

    def test_operator_dispatches_in_dependency_order(self, llm, goal):
        store    = MemoryStore()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        registry.register(Architect(llm=llm))

        validators = artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
        )
        log = EventLog()
        Operator(registry, validators, log).run(store, goal)

        dispatched = [e.capability_name for e in log.events("capability_dispatched")]
        assert dispatched == ["spec_extractor", "architect"]

    def test_two_iterations_two_artifacts(self, llm, goal):
        store    = MemoryStore()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        registry.register(Architect(llm=llm))

        validators = artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
        )
        log = EventLog()
        Operator(registry, validators, log).run(store, goal)

        assert len(log.events("capability_dispatched")) == 2
        assert len(store.list()) == 2
