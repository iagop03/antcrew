"""Tests for SpecExtractor — the template capability for all LLM-backed executors."""
from __future__ import annotations

import pytest

from antcrew.capabilities.spec_extractor import SpecExtractor
from antcrew.capabilities.validators import ArtifactExistsValidator
from antcrew.engine import (
    ArtifactId,
    ArtifactKind,
    CapabilityRegistry,
    Condition,
    ConditionId,
    Constraints,
    DesiredProjectState,
    EventLog,
    Goal,
    MemoryStore,
    Operator,
)
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def llm():
    return SimulatedLLM()


@pytest.fixture
def goal():
    return Goal(
        description="Build a REST API for a todo list application",
        desired_state=DesiredProjectState(
            frozenset([Condition(ConditionId("requirements_exists"), "requirements.md is present")])
        ),
        constraints=Constraints(tech_stack=("Python", "FastAPI"), excluded=("Redis",)),
    )


# ---------------------------------------------------------------------------
# Unit: SpecExtractor._run()
# ---------------------------------------------------------------------------

class TestSpecExtractorUnit:
    def test_creates_requirements_artifact(self, llm, goal):
        executor = SpecExtractor(llm=llm)
        store    = MemoryStore()

        result = executor.execute(store, goal)

        assert result.succeeded
        assert len(result.delta.created) == 1
        artifact = result.delta.created[0]
        assert artifact.id   == ArtifactId("requirements")
        assert artifact.kind == ArtifactKind.REQUIREMENTS

    def test_artifact_content_is_non_empty(self, llm, goal):
        executor = SpecExtractor(llm=llm)
        result   = executor.execute(MemoryStore(), goal)

        content = result.delta.created[0].content
        assert isinstance(content, str)
        assert len(content) > 0

    def test_delta_touched_includes_requirements(self, llm, goal):
        executor = SpecExtractor(llm=llm)
        result   = executor.execute(MemoryStore(), goal)

        assert ArtifactId("requirements") in result.delta.touched

    def test_no_errors_on_success(self, llm, goal):
        executor = SpecExtractor(llm=llm)
        result   = executor.execute(MemoryStore(), goal)

        assert result.errors == []

    def test_execution_time_recorded(self, llm, goal):
        executor = SpecExtractor(llm=llm)
        result   = executor.execute(MemoryStore(), goal)

        assert result.execution_time >= 0.0

    def test_goal_description_influences_prompt(self, goal):
        """Verify the LLM receives the goal text (SimulatedLLM echoes the prompt)."""
        recorded: list[str] = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                recorded.append(user)
                return super().system(prompt, user, **kw)

        SpecExtractor(llm=SpyLLM()).execute(MemoryStore(), goal)
        assert "todo list" in recorded[0].lower()

    def test_constraints_included_in_prompt(self, goal):
        recorded: list[str] = []

        class SpyLLM(SimulatedLLM):
            def system(self, prompt, user, **kw):
                recorded.append(user)
                return super().system(prompt, user, **kw)

        SpecExtractor(llm=SpyLLM()).execute(MemoryStore(), goal)
        assert "FastAPI" in recorded[0]
        assert "Redis"   in recorded[0]

    def test_no_llm_raises_on_run(self, goal):
        executor = SpecExtractor()  # no llm
        result   = executor.execute(MemoryStore(), goal)

        assert not result.succeeded
        assert any("RuntimeError" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Integration: SpecExtractor inside the Operator loop
# ---------------------------------------------------------------------------

class TestSpecExtractorInLoop:
    def test_operator_reaches_goal(self, llm, goal):
        store    = MemoryStore()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))

        validators = [
            ArtifactExistsValidator(ArtifactId("requirements"), ConditionId("requirements_exists"))
        ]
        log      = EventLog()
        operator = Operator(registry, validators, log)

        final_state = operator.run(store, goal)

        assert ConditionId("requirements_exists") in final_state.satisfied
        assert store.has(ArtifactId("requirements"))

    def test_operator_emits_expected_events(self, llm, goal):
        store    = MemoryStore()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))

        validators = [
            ArtifactExistsValidator(ArtifactId("requirements"), ConditionId("requirements_exists"))
        ]
        log      = EventLog()
        Operator(registry, validators, log).run(store, goal)

        kinds = [e.kind for e in log.events()]
        assert "engine_started"         in kinds
        assert "capability_dispatched"  in kinds
        assert "capability_completed"   in kinds
        assert "condition_satisfied"    in kinds
        assert "engine_finished"        in kinds

    def test_operator_runs_exactly_one_iteration(self, llm, goal):
        store    = MemoryStore()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))

        validators = [
            ArtifactExistsValidator(ArtifactId("requirements"), ConditionId("requirements_exists"))
        ]
        log      = EventLog()
        Operator(registry, validators, log).run(store, goal)

        dispatched = log.events("capability_dispatched")
        assert len(dispatched) == 1
        assert dispatched[0].capability_name == "spec_extractor"
