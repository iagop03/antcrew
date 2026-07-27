"""Tests for Pipeline — multi-team sequential composition."""
from __future__ import annotations

import pytest

from antcrew.core.pipeline import Pipeline
from antcrew.core.run_result import RunResult
from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.content_team import ContentTeam
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.research_team import ResearchTeam

# ---------------------------------------------------------------------------
# Minimal stub team for unit tests
# ---------------------------------------------------------------------------

class _StubTeam:
    """Minimal team that records calls and returns a preset state."""

    def __init__(self, output: dict, *, cost: float = 0.1) -> None:
        self._output = output
        self._cost = cost
        self.calls: list[tuple[str, str]] = []  # (request, thread_id)
        self._received_state: dict | None = None

    def _initial_state(self, request: str) -> dict:
        return {
            "request": request,
            "messages": [],
            "prd": None,
            "tickets": None,
            "code_artifacts": None,
            "test_artifacts": None,
            "review": None,
            "devops_artifacts": None,
            "doc_artifacts": None,
            "research_document": None,
            "content_piece": None,
            "current_agent": "",
            "errors": [],
            "metadata": {},
        }

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        self.calls.append((request, thread_id))
        # Capture what _initial_state returned (with carry-over applied)
        self._received_state = self._initial_state(request)
        merged = dict(self._received_state)
        merged.update(self._output)
        return RunResult(state=merged, thread_id=thread_id, cost_usd=self._cost)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_pipeline_requires_at_least_one_team():
    with pytest.raises(ValueError, match="at least one team"):
        Pipeline([])


def test_pipeline_single_team_runs():
    team = _StubTeam({"prd": "p1"})
    p = Pipeline([team])
    result = p.run("Build something")
    assert team.calls
    assert result.state["prd"] == "p1"


# ---------------------------------------------------------------------------
# Sequential execution
# ---------------------------------------------------------------------------

def test_pipeline_runs_all_teams_in_order():
    order: list[int] = []

    class _OrderedTeam(_StubTeam):
        def __init__(self, n, **kw):
            super().__init__({}, **kw)
            self._n = n

        def run(self, request, *, thread_id="default"):
            order.append(self._n)
            return super().run(request, thread_id=thread_id)

    p = Pipeline([_OrderedTeam(1), _OrderedTeam(2), _OrderedTeam(3)])
    p.run("test")
    assert order == [1, 2, 3]


def test_pipeline_passes_request_to_all_teams():
    a = _StubTeam({})
    b = _StubTeam({})
    Pipeline([a, b]).run("Build a login service")
    assert a.calls[0][0] == "Build a login service"
    assert b.calls[0][0] == "Build a login service"


def test_pipeline_thread_ids_are_unique_per_team():
    a = _StubTeam({})
    b = _StubTeam({})
    Pipeline([a, b]).run("test", thread_id="main")
    assert a.calls[0][1] != b.calls[0][1]
    assert "main" in a.calls[0][1]
    assert "main" in b.calls[0][1]


# ---------------------------------------------------------------------------
# State carry-over
# ---------------------------------------------------------------------------

def test_pipeline_carries_research_document_to_next_team():
    from antcrew.core.artifacts import ResearchDocument
    rd = ResearchDocument(title="AI Report", topic="AI", key_findings=["f1"])

    producer = _StubTeam({"research_document": rd})
    consumer = _StubTeam({"prd": "built from research"})

    p = Pipeline([producer, consumer])
    p.run("test")

    # consumer._initial_state was patched — check what it received
    assert consumer._received_state["research_document"] is rd


def test_pipeline_carries_code_artifacts():
    from antcrew.core.artifacts import CodeArtifact
    art = CodeArtifact(ticket_id="T-1", file_path="main.py", content="# main")

    producer = _StubTeam({"code_artifacts": [art]})
    consumer = _StubTeam({})

    p = Pipeline([producer, consumer])
    p.run("test")
    assert consumer._received_state["code_artifacts"] == [art]


def test_pipeline_does_not_carry_none_values():
    """None values in previous state don't overwrite next team's initial None."""
    producer = _StubTeam({"research_document": None, "prd": None})
    consumer = _StubTeam({})

    p = Pipeline([producer, consumer])
    p.run("test")
    # consumer's initial state should still have None (not changed)
    assert consumer._received_state["research_document"] is None


def test_pipeline_custom_carry_keys():
    producer = _StubTeam({"prd": "PRD-X", "research_document": "research"})
    consumer = _StubTeam({})

    # Only carry prd, not research_document
    p = Pipeline([producer, consumer], carry_keys=frozenset({"prd"}))
    p.run("test")
    assert consumer._received_state["prd"] == "PRD-X"
    assert consumer._received_state["research_document"] is None


# ---------------------------------------------------------------------------
# Cost aggregation
# ---------------------------------------------------------------------------

def test_pipeline_aggregates_cost_across_teams():
    a = _StubTeam({}, cost=1.5)
    b = _StubTeam({}, cost=2.0)
    c = _StubTeam({}, cost=0.5)
    result = Pipeline([a, b, c]).run("test")
    assert abs(result.cost_usd - 4.0) < 1e-9


def test_pipeline_single_team_cost():
    team = _StubTeam({}, cost=3.0)
    result = Pipeline([team]).run("test")
    assert abs(result.cost_usd - 3.0) < 1e-9


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------

def test_pipeline_returns_run_result():
    result = Pipeline([_StubTeam({})]).run("test")
    assert isinstance(result, RunResult)


def test_pipeline_final_state_is_last_team_output():
    a = _StubTeam({"prd": "from-a"})
    b = _StubTeam({"prd": "from-b", "tickets": ["t1"]})
    result = Pipeline([a, b]).run("test")
    assert result.state["prd"] == "from-b"
    assert result.state["tickets"] == ["t1"]


def test_pipeline_thread_id_in_result():
    result = Pipeline([_StubTeam({})]).run("test", thread_id="my-thread")
    assert result.thread_id == "my-thread"


# ---------------------------------------------------------------------------
# Real team integration (SimulatedLLM)
# ---------------------------------------------------------------------------

def test_pipeline_research_then_dev():
    """ResearchTeam → DevTeam: research_document from ResearchTeam carries to DevTeam."""
    llm = SimulatedLLM()
    pipeline = Pipeline([
        ResearchTeam(model=llm),
        DevTeam(model=llm),
    ])
    result = pipeline.run("Build an AI analytics service")
    assert isinstance(result, RunResult)
    # research_document produced by ResearchTeam carried into final state
    assert result.state.get("research_document") is not None
    # DevTeam also ran and produced code artifacts
    assert result.state.get("code_artifacts") is not None


def test_pipeline_two_content_teams():
    """Two ContentTeams in sequence — both run; last team's output wins."""
    llm = SimulatedLLM()
    pipeline = Pipeline([
        ContentTeam(model=llm),
        ContentTeam(model=llm),
    ])
    result = pipeline.run("AI blog post")
    assert isinstance(result, RunResult)
    assert result.state.get("content_piece") is not None
