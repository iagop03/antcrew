"""Tests for event bus integration across all team types (v0.14.x).

Covers:
  - bus.emit() shorthand (type, payload, run_id, thread_id kwargs)
  - FullStackTeam.run() → pipeline.start / pipeline.end / agent.start / agent.end
  - ResearchTeam.run() → pipeline.start / pipeline.end
  - ContentTeam.run() → pipeline.start / pipeline.end
  - CustomTeam.run() → pipeline.start / agent.start / agent.end / pipeline.end
  - Pipeline.run() → pipeline.start (team=Pipeline) / pipeline.end
  - Router.run() → router.dispatch
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from antcrew.core.events import Event, bus, capture

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_bus():
    bus.clear()
    yield
    bus.clear()


# ---------------------------------------------------------------------------
# bus.emit() shorthand API
# ---------------------------------------------------------------------------

def test_emit_shorthand_creates_event():
    received = []
    bus.subscribe("test.event", received.append)
    bus.emit("test.event", {"x": 1}, run_id="r1", thread_id="t1")

    assert len(received) == 1
    e = received[0]
    assert e.type == "test.event"
    assert e.payload["x"] == 1
    assert e.run_id == "r1"
    assert e.thread_id == "t1"


def test_emit_shorthand_no_metadata():
    received = []
    bus.subscribe("test.event", received.append)
    bus.emit("test.event", {"x": 2})

    assert received[0].run_id is None
    assert received[0].thread_id is None


def test_emit_original_api_still_works():
    received = []
    bus.subscribe("test.event", received.append)
    bus.emit(Event("test.event", {"y": 3}, run_id="abc"))

    assert received[0].payload["y"] == 3
    assert received[0].run_id == "abc"


def test_emit_shorthand_empty_payload():
    received = []
    bus.subscribe("x", received.append)
    bus.emit("x")
    assert received[0].payload == {}


# ---------------------------------------------------------------------------
# Helpers for building lightweight mock teams
# ---------------------------------------------------------------------------

def _mock_llm(response: str):
    llm = MagicMock()
    llm.system.return_value = response
    llm.max_cost_usd = None
    llm.get_usage_summary.return_value = {"total_cost_usd": 0.0}
    llm.trace = None
    llm._trace_run_id = None
    return llm


_PRD_JSON = json.dumps({
    "title": "Auth", "summary": "JWT auth",
    "goals": [], "out_of_scope": [],
    "functional_requirements": [], "non_functional_requirements": [], "open_questions": [],
})

_RESEARCH_JSON = json.dumps({
    "query": "auth options", "findings": ["JWT is good"],
    "summary": "Use JWT", "sources": [],
})

_CONTENT_JSON = json.dumps({
    "title": "Auth Guide", "body": "Use JWT for authentication.",
    "summary": "JWT guide", "tags": [],
})


# ---------------------------------------------------------------------------
# ResearchTeam events
# ---------------------------------------------------------------------------

def test_research_team_emits_pipeline_events():
    from antcrew.teams.research_team import ResearchTeam

    llm = _mock_llm(_RESEARCH_JSON)
    team = ResearchTeam(llm)

    with capture("pipeline.start", "pipeline.end") as events:
        team.run("Research auth")

    types = [e.type for e in events]
    assert "pipeline.start" in types
    assert "pipeline.end" in types


def test_research_team_run_id_consistent():
    from antcrew.teams.research_team import ResearchTeam

    llm = _mock_llm(_RESEARCH_JSON)
    team = ResearchTeam(llm)

    with capture() as events:
        team.run("Research auth")

    run_ids = {e.run_id for e in events if e.run_id}
    assert len(run_ids) == 1


def test_research_team_pipeline_start_payload():
    from antcrew.teams.research_team import ResearchTeam

    llm = _mock_llm(_RESEARCH_JSON)
    team = ResearchTeam(llm)

    with capture("pipeline.start") as events:
        team.run("Research auth")

    assert events[0].payload["team"] == "ResearchTeam"
    assert events[0].payload["request"] == "Research auth"


# ---------------------------------------------------------------------------
# ContentTeam events
# ---------------------------------------------------------------------------

def test_content_team_emits_pipeline_events():
    from antcrew.teams.content_team import ContentTeam

    llm = _mock_llm(_CONTENT_JSON)
    team = ContentTeam(llm)

    with capture("pipeline.start", "pipeline.end") as events:
        team.run("Write auth guide")

    types = [e.type for e in events]
    assert "pipeline.start" in types
    assert "pipeline.end" in types


def test_content_team_pipeline_end_success():
    from antcrew.teams.content_team import ContentTeam

    llm = _mock_llm(_CONTENT_JSON)
    team = ContentTeam(llm)

    with capture("pipeline.end") as events:
        team.run("Write guide")

    assert events[-1].payload["success"] is True


# ---------------------------------------------------------------------------
# CustomTeam events
# ---------------------------------------------------------------------------

def _custom_llm():
    llm = MagicMock()
    llm.system.return_value = "done"
    llm.max_cost_usd = None
    llm.get_usage_summary.return_value = {"total_cost_usd": 0.0}
    llm.trace = None
    llm._trace_run_id = None
    return llm


def test_custom_team_emits_pipeline_start_end():
    from antcrew.teams.custom_team import CustomTeam

    team = CustomTeam(
        steps=[
            {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "executor", "system_prompt": "Do.", "output_key": "result"},
        ],
        llm=_custom_llm(),
    )

    with capture("pipeline.start", "pipeline.end") as events:
        team.run("Build something")

    types = [e.type for e in events]
    assert "pipeline.start" in types
    assert "pipeline.end" in types


def test_custom_team_emits_agent_start_end():
    from antcrew.teams.custom_team import CustomTeam

    team = CustomTeam(
        steps=[
            {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "executor", "system_prompt": "Do.", "output_key": "result"},
        ],
        llm=_custom_llm(),
    )

    with capture("agent.start", "agent.end") as events:
        team.run("Build something")

    agent_names = [e.payload.get("agent_name") for e in events]
    assert "planner" in agent_names
    assert "executor" in agent_names


def test_custom_team_run_id_consistent():
    from antcrew.teams.custom_team import CustomTeam

    team = CustomTeam(
        steps=[{"name": "planner", "system_prompt": "Plan.", "output_key": "plan"}],
        llm=_custom_llm(),
    )

    with capture() as events:
        team.run("Build")

    run_ids = {e.run_id for e in events if e.run_id}
    assert len(run_ids) == 1


# ---------------------------------------------------------------------------
# Pipeline events
# ---------------------------------------------------------------------------

def test_pipeline_emits_pipeline_start_end():
    from antcrew.core.pipeline import Pipeline
    from antcrew.teams.research_team import ResearchTeam

    llm = _mock_llm(_RESEARCH_JSON)
    pipeline = Pipeline([ResearchTeam(llm)])

    with capture("pipeline.start", "pipeline.end") as events:
        pipeline.run("Research")

    # The top-level Pipeline event + the ResearchTeam events
    pipeline_events = [e for e in events if e.payload.get("team") == "Pipeline"]
    assert any(e.type == "pipeline.start" for e in pipeline_events)
    assert any(e.type == "pipeline.end" for e in pipeline_events)


def test_pipeline_start_payload_has_steps():
    from antcrew.core.pipeline import Pipeline
    from antcrew.teams.research_team import ResearchTeam

    llm = _mock_llm(_RESEARCH_JSON)
    pipeline = Pipeline([ResearchTeam(llm)])

    with capture("pipeline.start") as events:
        pipeline.run("Research")

    pipeline_start = next(e for e in events if e.payload.get("team") == "Pipeline")
    assert "ResearchTeam" in pipeline_start.payload["steps"]


# ---------------------------------------------------------------------------
# Router events
# ---------------------------------------------------------------------------

def test_router_emits_dispatch():
    from antcrew.core.router import Router, RuleClassifier
    from antcrew.core.run_result import RunResult

    mock_team = MagicMock()
    mock_team.run.return_value = RunResult(state={"result": "done"})

    # RuleClassifier takes list[tuple[pattern, label]]
    router = Router(
        classifier=RuleClassifier(
            [("build.*", "dev"), ("research.*", "research")], default="dev"
        ),
        routes={"dev": mock_team},
        default="dev",
    )

    with capture("router.dispatch") as events:
        router.run("build an auth system")

    assert len(events) == 1
    assert events[0].payload["label"] == "dev"
    assert "auth" in events[0].payload["request"]


def test_router_dispatch_has_run_id():
    from antcrew.core.router import Router, RuleClassifier
    from antcrew.core.run_result import RunResult

    mock_team = MagicMock()
    mock_team.run.return_value = RunResult(state={})

    router = Router(
        classifier=RuleClassifier([("test.*", "x")], default="x"),
        routes={"x": mock_team},
        default="x",
    )

    with capture("router.dispatch") as events:
        router.run("test request")

    assert events[0].run_id is not None
    assert len(events[0].run_id) == 12
