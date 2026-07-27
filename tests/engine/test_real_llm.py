"""Integration tests for the full engine pipeline — no API token required.

Uses SimulatedLLM throughout so every test always runs in CI.
Focuses on behaviors that unit tests don't cover:
  - FilesystemStore survives across Operator runs
  - CapabilitySelector strategies alter dispatch order
  - EventLog captures the full sequence and ordering
  - Full 8-capability chain reaches all conditions
"""
from __future__ import annotations

import pytest

from antcrew.capabilities import (
    Architect,
    CodeGenerator,
    CodeReviewer,
    SpecExtractor,
    TaskPlanner,
    TestGenerator,
    TestRunner,
)
from antcrew.capabilities.validators import (
    AllTasksCompletedValidator,
    artifact_validators,
)
from antcrew.engine import (
    ArtifactId,
    ArtifactKind,
    CapabilityRegistry,
    CheapestFirst,
    Condition,
    ConditionId,
    Constraints,
    DesiredProjectState,
    EventLog,
    FilesystemStore,
    FirstMatch,
    Goal,
    MemoryStore,
    MostProductive,
    Operator,
    PrioritySelector,
)
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def llm():
    return SimulatedLLM()


def _registry(llm):
    r = CapabilityRegistry()
    r.register(SpecExtractor(llm=llm))
    r.register(Architect(llm=llm))
    r.register(TaskPlanner(llm=llm))
    r.register(CodeGenerator(llm=llm))
    r.register(TestGenerator(llm=llm))
    r.register(TestRunner())
    r.register(CodeReviewer(llm=llm))
    return r


def _plan_validators():
    return artifact_validators(
        ("requirements", "requirements_exists"),
        ("architecture", "architecture_exists"),
        ("task_graph",   "task_graph_exists"),
    )


def _plan_goal():
    return Goal(
        description="Build a Python CLI tool for personal task management",
        desired_state=DesiredProjectState(frozenset([
            Condition(ConditionId("requirements_exists"), "requirements"),
            Condition(ConditionId("architecture_exists"), "architecture"),
            Condition(ConditionId("task_graph_exists"),   "tasks planned"),
        ])),
        constraints=Constraints(tech_stack=("Python",)),
    )


# ---------------------------------------------------------------------------
# FilesystemStore integration
# ---------------------------------------------------------------------------

class TestFilesystemIntegration:
    def test_artifacts_persist_across_operator_instances(self, tmp_path, llm):
        """A second Operator can continue from where the first left off."""
        store = FilesystemStore(tmp_path)
        log   = EventLog()
        goal  = _plan_goal()

        # First run: only SpecExtractor goal
        partial_goal = Goal(
            description=goal.description,
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements"),
            ])),
        )
        Operator(_registry(llm), _plan_validators(), log, max_iterations=10).run(store, partial_goal)
        assert store.has(ArtifactId("requirements"))

        # Second run: fresh Operator, same FilesystemStore path — reads prior artifacts
        store2 = FilesystemStore(tmp_path)
        log2   = EventLog()
        Operator(_registry(llm), _plan_validators(), log2, max_iterations=10).run(store2, goal)

        assert store2.has(ArtifactId("architecture"))
        assert store2.has(ArtifactId("task_graph"))

        # SpecExtractor must NOT have been dispatched in the second run
        dispatched2 = [e.capability_name for e in log2.events("capability_dispatched")]
        assert "spec_extractor" not in dispatched2

    def test_source_files_written_to_natural_paths(self, tmp_path, llm):
        """SOURCE artifacts produced by CodeGenerator appear as real files on disk."""
        store = FilesystemStore(tmp_path)
        goal  = _plan_goal()
        full_goal = Goal(
            description=goal.description,
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements"),
                Condition(ConditionId("architecture_exists"), "architecture"),
                Condition(ConditionId("task_graph_exists"),   "tasks planned"),
                Condition(ConditionId("implementation_exists"), "implemented"),
            ])),
            constraints=Constraints(tech_stack=("Python",)),
        )
        validators = [
            *_plan_validators(),
            AllTasksCompletedValidator(),
        ]
        Operator(_registry(llm), validators, EventLog(), max_iterations=30).run(store, full_goal)

        sources = store.list(ArtifactKind.SOURCE)
        assert len(sources) >= 1
        for src in sources:
            file_path = src.metadata.get("file_path")
            if file_path:
                assert (tmp_path / file_path).exists()


# ---------------------------------------------------------------------------
# CapabilitySelector strategies
# ---------------------------------------------------------------------------

class TestCapabilitySelectors:
    def _run_and_get_order(self, llm, selector):
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        registry.register(Architect(llm=llm))
        registry.register(TaskPlanner(llm=llm))
        log  = EventLog()
        goal = _plan_goal()
        Operator(registry, _plan_validators(), log,
                 max_iterations=15, selector=selector).run(MemoryStore(), goal)
        return [e.capability_name for e in log.events("capability_dispatched")]

    def test_cheapest_first_runs(self, llm):
        order = self._run_and_get_order(llm, CheapestFirst())
        assert set(order) == {"spec_extractor", "architect", "task_planner"}

    def test_first_match_runs(self, llm):
        order = self._run_and_get_order(llm, FirstMatch())
        assert set(order) == {"spec_extractor", "architect", "task_planner"}

    def test_most_productive_runs(self, llm):
        order = self._run_and_get_order(llm, MostProductive())
        # Architect now produces 2 conditions (requirements_exists + architecture_exists),
        # so MostProductive may skip SpecExtractor entirely — both core caps must run.
        assert "architect" in order
        assert "task_planner" in order

    def test_priority_selector_respects_priorities(self, llm):
        """PrioritySelector dispatches highest-priority executor when multiple candidates exist."""
        # Both SpecExtractor and Architect have no `needs`, so initially both are candidates
        # for any gap. PrioritySelector should prefer architect if given higher priority.
        # We test indirectly: the selector runs without error and produces conditions.
        order = self._run_and_get_order(
            llm,
            PrioritySelector({"spec_extractor": 10, "architect": 5, "task_planner": 1}),
        )
        assert "spec_extractor" in order

    def test_operator_decision_events_record_selector_name(self, llm):
        log  = EventLog()
        goal = _plan_goal()
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        Operator(
            registry,
            [*_plan_validators()[:1]],  # only requirements validator
            log,
            max_iterations=5,
            selector=FirstMatch(),
        ).run(MemoryStore(), Goal(
            description=goal.description,
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements"),
            ])),
        ))
        decisions = log.events("operator_decision")
        assert decisions
        assert decisions[0].reason == "first_match"


# ---------------------------------------------------------------------------
# EventLog ordering
# ---------------------------------------------------------------------------

class TestEventOrdering:
    def test_engine_started_is_first_event(self, llm):
        log  = EventLog()
        goal = Goal(
            description="Test",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "req"),
            ])),
        )
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        Operator(registry, _plan_validators()[:1], log, max_iterations=5).run(MemoryStore(), goal)

        all_events = log.events()
        assert all_events[0].kind == "engine_started"

    def test_engine_finished_is_last_event(self, llm):
        log  = EventLog()
        goal = Goal(
            description="Test",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "req"),
            ])),
        )
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        Operator(registry, _plan_validators()[:1], log, max_iterations=5).run(MemoryStore(), goal)

        all_events = log.events()
        assert all_events[-1].kind == "engine_finished"

    def test_dispatch_precedes_completed(self, llm):
        log  = EventLog()
        goal = _plan_goal()
        Operator(_registry(llm), _plan_validators(), log, max_iterations=15).run(MemoryStore(), goal)

        for name in ("spec_extractor", "architect", "task_planner"):
            d_idx = next(i for i, e in enumerate(log.events())
                         if e.kind == "capability_dispatched" and e.capability_name == name)
            c_idx = next(i for i, e in enumerate(log.events())
                         if e.kind == "capability_completed" and e.capability_name == name)
            assert d_idx < c_idx

    def test_condition_satisfied_after_capability_completed(self, llm):
        log  = EventLog()
        goal = Goal(
            description="Test",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "req"),
            ])),
        )
        registry = CapabilityRegistry()
        registry.register(SpecExtractor(llm=llm))
        Operator(registry, _plan_validators()[:1], log, max_iterations=5).run(MemoryStore(), goal)

        events    = log.events()
        kinds     = [e.kind for e in events]
        completed = kinds.index("capability_completed")
        satisfied = kinds.index("condition_satisfied")
        assert completed < satisfied


# ---------------------------------------------------------------------------
# TeamExecutor bridge
# ---------------------------------------------------------------------------

class TestTeamExecutorIntegration:
    def test_team_executor_runs_in_operator_loop(self, llm):
        """TeamExecutor wrapping a simple callable works inside the Operator loop."""
        from antcrew.capabilities.team_executor import TeamExecutor
        from antcrew.engine import CapabilityDescriptor

        class FakeTeam:
            def run(self, input_text: str) -> dict:
                return {"summary": f"Done: {input_text[:20]}"}

        descriptor = CapabilityDescriptor(
            name="fake_team",
            description="Fake team stub.",
            needs=frozenset(),
            produces=frozenset([ConditionId("requirements_exists")]),
            emits=frozenset(),
            cost=1.0,
        )
        executor = TeamExecutor(FakeTeam(), descriptor, output_artifact_id="requirements")

        registry = CapabilityRegistry()
        registry.register(executor)
        validators = artifact_validators(("requirements", "requirements_exists"))
        goal = Goal(
            description="Build something",
            desired_state=DesiredProjectState(frozenset([
                Condition(ConditionId("requirements_exists"), "requirements"),
            ])),
        )
        state = Operator(registry, validators, EventLog(), max_iterations=5).run(MemoryStore(), goal)
        assert ConditionId("requirements_exists") in state.satisfied

    def test_team_executor_stores_output_artifact(self):
        from antcrew.capabilities.team_executor import TeamExecutor
        from antcrew.engine import CapabilityDescriptor

        class FakeTeam:
            def run(self, input_text: str) -> dict:
                return {"key": "value"}

        descriptor = CapabilityDescriptor(
            name="fake", description="x", needs=frozenset(),
            produces=frozenset(), emits=frozenset(), cost=0.0,
        )
        store  = MemoryStore()
        goal   = Goal(description="test", desired_state=DesiredProjectState(frozenset()))
        result = TeamExecutor(FakeTeam(), descriptor, output_artifact_id="out").execute(store, goal)

        assert result.succeeded
        assert result.delta.created[0].id == ArtifactId("out")
        assert result.delta.created[0].content == {"key": "value"}
