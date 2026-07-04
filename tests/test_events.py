"""Tests for antcrew.core.events — EventBus, capture, _make_evented_run."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from antcrew.core.events import (
    Event,
    EventBus,
    bus,
    capture,
    new_run_id,
    _make_evented_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_bus():
    """Reset the global bus before and after every test."""
    bus.clear()
    yield
    bus.clear()


def _evt(type_: str = "test.event", **payload) -> Event:
    return Event(type_, payload)


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

def test_event_defaults():
    e = Event("agent.start", {"agent_name": "pm"})
    assert e.type == "agent.start"
    assert e.payload["agent_name"] == "pm"
    assert e.timestamp > 0
    assert e.run_id is None
    assert e.thread_id is None


def test_event_with_ids():
    e = Event("pipeline.end", {}, run_id="abc123", thread_id="sess-1")
    assert e.run_id == "abc123"
    assert e.thread_id == "sess-1"


# ---------------------------------------------------------------------------
# EventBus — subscribe / emit
# ---------------------------------------------------------------------------

def test_subscribe_and_emit():
    received = []
    bus.subscribe("agent.end", received.append)
    bus.emit(_evt("agent.end", agent_name="backend_dev"))

    assert len(received) == 1
    assert received[0].payload["agent_name"] == "backend_dev"


def test_wildcard_receives_all():
    received = []
    bus.subscribe("*", received.append)
    bus.emit(_evt("agent.start"))
    bus.emit(_evt("pipeline.end"))

    assert len(received) == 2


def test_non_matching_handler_not_called():
    received = []
    bus.subscribe("agent.start", received.append)
    bus.emit(_evt("pipeline.end"))

    assert received == []


def test_handler_exception_does_not_propagate():
    def bad_handler(event: Event) -> None:
        raise RuntimeError("handler blew up")

    bus.subscribe("agent.end", bad_handler)
    # Must not raise:
    bus.emit(_evt("agent.end"))


def test_duplicate_subscribe_ignored():
    received = []
    handler = received.append
    bus.subscribe("agent.start", handler)
    bus.subscribe("agent.start", handler)
    bus.emit(_evt("agent.start"))

    assert len(received) == 1


# ---------------------------------------------------------------------------
# EventBus — unsubscribe
# ---------------------------------------------------------------------------

def test_unsubscribe_specific():
    received = []
    handler = received.append
    bus.subscribe("agent.end", handler)
    bus.unsubscribe("agent.end", handler)
    bus.emit(_evt("agent.end"))

    assert received == []


def test_unsubscribe_wildcard():
    received = []
    handler = received.append
    bus.subscribe("*", handler)
    bus.unsubscribe("*", handler)
    bus.emit(_evt("anything"))

    assert received == []


# ---------------------------------------------------------------------------
# EventBus — clear and __contains__
# ---------------------------------------------------------------------------

def test_clear_all():
    bus.subscribe("agent.start", lambda e: None)
    bus.subscribe("*", lambda e: None)
    bus.clear()
    assert "agent.start" not in bus
    assert "*" not in bus


def test_clear_specific_type():
    bus.subscribe("agent.start", lambda e: None)
    bus.subscribe("pipeline.end", lambda e: None)
    bus.clear("agent.start")
    assert "agent.start" not in bus
    assert "pipeline.end" in bus


def test_contains_true_when_handler_registered():
    bus.subscribe("agent.end", lambda e: None)
    assert "agent.end" in bus


def test_contains_false_when_no_handler():
    assert "agent.end" not in bus


def test_contains_true_via_wildcard():
    bus.subscribe("*", lambda e: None)
    assert "agent.end" in bus


# ---------------------------------------------------------------------------
# capture() context manager
# ---------------------------------------------------------------------------

def test_capture_specific_type():
    with capture("agent.end") as events:
        bus.emit(_evt("agent.start"))
        bus.emit(_evt("agent.end", agent_name="qa"))

    assert len(events) == 1
    assert events[0].payload["agent_name"] == "qa"


def test_capture_multiple_types():
    with capture("agent.start", "pipeline.end") as events:
        bus.emit(_evt("agent.start"))
        bus.emit(_evt("pipeline.end"))
        bus.emit(_evt("kb.updated"))

    assert len(events) == 2


def test_capture_all_when_no_types():
    with capture() as events:
        bus.emit(_evt("agent.start"))
        bus.emit(_evt("pipeline.end"))

    assert len(events) == 2


def test_capture_cleans_up_after_exit():
    with capture("agent.end"):
        pass
    assert "agent.end" not in bus


def test_capture_cleans_up_on_exception():
    try:
        with capture("agent.end") as events:
            raise ValueError("test error")
    except ValueError:
        pass
    assert "agent.end" not in bus


# ---------------------------------------------------------------------------
# new_run_id
# ---------------------------------------------------------------------------

def test_run_id_is_12_chars():
    rid = new_run_id()
    assert len(rid) == 12


def test_run_ids_are_unique():
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# _make_evented_run — Supervisor node wrapper
# ---------------------------------------------------------------------------

def _agent_run(state: dict) -> dict:
    return {"code_artifacts": ["file.py"], "current_agent": "backend_dev"}


def test_evented_run_emits_start_and_end():
    wrapped = _make_evented_run(_agent_run, "backend_dev")
    with capture("agent.start", "agent.end") as events:
        wrapped({"_run_id": "r1", "_thread_id": "t1"})

    types = [e.type for e in events]
    assert types == ["agent.start", "agent.end"]


def test_evented_run_start_payload():
    wrapped = _make_evented_run(_agent_run, "backend_dev")
    with capture("agent.start") as events:
        wrapped({"_run_id": "r1", "_thread_id": "t1"})

    assert events[0].payload["agent_name"] == "backend_dev"
    assert events[0].run_id == "r1"
    assert events[0].thread_id == "t1"


def test_evented_run_end_payload_has_duration():
    wrapped = _make_evented_run(_agent_run, "backend_dev")
    with capture("agent.end") as events:
        wrapped({"_run_id": "r1", "_thread_id": "t1"})

    e = events[0]
    assert e.payload["duration_s"] >= 0
    assert "code_artifacts" in e.payload["produced_keys"]
    # private / metadata keys excluded from produced_keys
    assert "current_agent" not in e.payload["produced_keys"]


def test_evented_run_works_without_run_id_in_state():
    wrapped = _make_evented_run(_agent_run, "backend_dev")
    with capture("agent.start") as events:
        wrapped({})

    assert events[0].run_id is None
    assert events[0].thread_id == "default"


def test_evented_run_propagates_result():
    def my_run(state):
        return {"code_artifacts": ["auth.py"]}

    wrapped = _make_evented_run(my_run, "backend_dev")
    result = wrapped({})
    assert result == {"code_artifacts": ["auth.py"]}


def test_evented_run_propagates_exception():
    def bad_run(state):
        raise RuntimeError("agent failed")

    wrapped = _make_evented_run(bad_run, "backend_dev")
    with pytest.raises(RuntimeError, match="agent failed"):
        wrapped({})


# ---------------------------------------------------------------------------
# _SingleAgentTeam emits pipeline events
# ---------------------------------------------------------------------------

def test_single_agent_team_emits_pipeline_events():
    import json
    from antcrew.core.task_classifier import _SingleAgentTeam

    mock_llm = MagicMock()
    mock_llm.system.return_value = json.dumps({
        "title": "T", "summary": "S", "goals": [], "out_of_scope": [],
        "functional_requirements": [], "non_functional_requirements": [], "open_questions": [],
    })
    from antcrew.agents.business import BusinessAnalystAgent
    agent = BusinessAnalystAgent(mock_llm)
    team = _SingleAgentTeam(agent)

    with capture("pipeline.start", "pipeline.end", "agent.start", "agent.end") as events:
        team.run("Build auth")

    types = [e.type for e in events]
    assert "pipeline.start" in types
    assert "pipeline.end" in types
    assert "agent.start" in types
    assert "agent.end" in types


def test_single_agent_team_run_id_is_consistent():
    """All events in a run share the same run_id."""
    import json
    from antcrew.core.task_classifier import _SingleAgentTeam

    mock_llm = MagicMock()
    mock_llm.system.return_value = json.dumps({
        "title": "T", "summary": "S", "goals": [], "out_of_scope": [],
        "functional_requirements": [], "non_functional_requirements": [], "open_questions": [],
    })
    from antcrew.agents.business import BusinessAnalystAgent
    agent = BusinessAnalystAgent(mock_llm)
    team = _SingleAgentTeam(agent)

    with capture() as events:
        team.run("Build auth")

    run_ids = {e.run_id for e in events}
    assert len(run_ids) == 1
    assert list(run_ids)[0] is not None
