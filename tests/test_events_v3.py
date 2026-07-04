"""Tests for v0.14.2+ event additions.

Covers:
  - feedback.start / feedback.round / feedback.end from FeedbackLoop
  - feedback.start / feedback.round / feedback.end from run_test_feedback_loop
  - CoherenceAgent agent.start / agent.end outside the LangGraph supervisor
  - EventBus thread-safety under concurrent emitters
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from antcrew.core.events import bus, capture, Event, EventBus, new_run_id
from antcrew.core.feedback import FeedbackLoop, FeedbackResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_bus():
    bus.clear()
    yield
    bus.clear()


# ---------------------------------------------------------------------------
# FeedbackLoop — feedback.start / feedback.round / feedback.end
# ---------------------------------------------------------------------------

def _make_runner(results: list[bool]):
    """Return a FeedbackRunner mock that returns results in sequence."""
    runner = MagicMock()
    runner.run.side_effect = [
        FeedbackResult(ok=ok, output="ok" if ok else "FAIL", returncode=0 if ok else 1, duration_ms=1)
        for ok in results
    ]
    return runner


def _noop_agent(state: dict) -> dict:
    return {"code": "pass"}


def test_feedback_loop_emits_start():
    loop = FeedbackLoop(runner=_make_runner([True]), max_rounds=3)
    with capture("feedback.start") as events:
        loop.run(_noop_agent, {"request": "x"})
    assert len(events) == 1
    assert events[0].payload["max_rounds"] == 3


def test_feedback_loop_emits_round_on_pass():
    loop = FeedbackLoop(runner=_make_runner([True]), max_rounds=3)
    with capture("feedback.round") as events:
        loop.run(_noop_agent, {"request": "x"})
    assert len(events) == 1
    assert events[0].payload["ok"] is True
    assert events[0].payload["round"] == 1


def test_feedback_loop_emits_multiple_rounds_on_retry():
    loop = FeedbackLoop(runner=_make_runner([False, False, True]), max_rounds=3)
    with capture("feedback.round") as events:
        loop.run(_noop_agent, {"request": "x"})
    assert len(events) == 3
    assert events[0].payload["ok"] is False
    assert events[1].payload["ok"] is False
    assert events[2].payload["ok"] is True


def test_feedback_loop_emits_end_on_success():
    loop = FeedbackLoop(runner=_make_runner([True]), max_rounds=3)
    with capture("feedback.end") as events:
        loop.run(_noop_agent, {"request": "x"})
    assert len(events) == 1
    assert events[0].payload["success"] is True
    assert events[0].payload["rounds_used"] == 1


def test_feedback_loop_emits_end_on_exhaustion():
    loop = FeedbackLoop(runner=_make_runner([False, False, False]), max_rounds=3)
    with capture("feedback.end") as events:
        loop.run(_noop_agent, {"request": "x"})
    assert len(events) == 1
    assert events[0].payload["success"] is False
    assert events[0].payload["rounds_used"] == 3


def test_feedback_loop_run_id_propagated():
    loop = FeedbackLoop(runner=_make_runner([True]), max_rounds=2)
    with capture() as events:
        loop.run(_noop_agent, {"request": "x", "_run_id": "abc123", "_thread_id": "t1"})
    for e in events:
        if e.type in ("feedback.start", "feedback.round", "feedback.end"):
            assert e.run_id == "abc123"
            assert e.thread_id == "t1"


def test_feedback_loop_no_run_id_still_emits():
    loop = FeedbackLoop(runner=_make_runner([True]), max_rounds=1)
    with capture("feedback.start") as events:
        loop.run(_noop_agent, {"request": "x"})
    assert len(events) == 1
    assert events[0].run_id is None


def test_feedback_loop_event_order():
    loop = FeedbackLoop(runner=_make_runner([False, True]), max_rounds=3)
    with capture() as events:
        loop.run(_noop_agent, {"request": "x"})
    types = [e.type for e in events]
    assert types[0] == "feedback.start"
    assert types[-1] == "feedback.end"
    assert "feedback.round" in types


# ---------------------------------------------------------------------------
# run_test_feedback_loop — feedback.start / feedback.round / feedback.end
# ---------------------------------------------------------------------------

def _make_test_results(success: bool) -> MagicMock:
    tr = MagicMock()
    tr.success = success
    tr.output = "ok" if success else "FAILED: assertion error"
    return tr


def test_run_test_feedback_loop_emits_start():
    from antcrew.core.feedback import run_test_feedback_loop

    runner = MagicMock()
    runner.run.return_value = _make_test_results(True)
    agent = MagicMock()

    with capture("feedback.start") as events:
        run_test_feedback_loop(
            {"_run_id": "r1", "_thread_id": "t1",
             "test_artifacts": [], "code_artifacts": []},
            agent, runner, max_rounds=2,
        )

    assert len(events) == 1
    assert events[0].payload["max_rounds"] == 2
    assert events[0].run_id == "r1"


def test_run_test_feedback_loop_emits_end_success():
    from antcrew.core.feedback import run_test_feedback_loop

    runner = MagicMock()
    runner.run.return_value = _make_test_results(True)
    agent = MagicMock()

    with capture("feedback.end") as events:
        run_test_feedback_loop(
            {"_run_id": "r1", "test_artifacts": [], "code_artifacts": []},
            agent, runner,
        )

    assert events[-1].payload["success"] is True


def test_run_test_feedback_loop_emits_round_per_attempt():
    from antcrew.core.feedback import run_test_feedback_loop

    runner = MagicMock()
    runner.run.side_effect = [
        _make_test_results(False),
        _make_test_results(True),
    ]
    agent = MagicMock()
    agent.fix_test_failures.return_value = {"code_artifacts": []}

    with capture("feedback.round") as events:
        run_test_feedback_loop(
            {"test_artifacts": [], "code_artifacts": []},
            agent, runner, max_rounds=3,
        )

    assert len(events) == 2
    assert events[0].payload["ok"] is False
    assert events[1].payload["ok"] is True


def test_run_test_feedback_loop_round_type_is_test():
    from antcrew.core.feedback import run_test_feedback_loop

    runner = MagicMock()
    runner.run.return_value = _make_test_results(True)
    agent = MagicMock()

    with capture("feedback.round") as events:
        run_test_feedback_loop(
            {"test_artifacts": [], "code_artifacts": []},
            agent, runner,
        )

    assert events[0].payload["type"] == "test"


# ---------------------------------------------------------------------------
# CoherenceAgent agent.start / agent.end (called outside LangGraph)
# ---------------------------------------------------------------------------

def _make_dev_team_coherence_state():
    from antcrew.core.artifacts import CodeArtifact
    art = CodeArtifact(
        ticket_id="T-1", file_path="auth.py",
        description="Auth", content="def login(): pass", language="python",
    )
    return {
        "request": "x", "messages": [], "prd": None, "tickets": None,
        "code_artifacts": [art], "test_artifacts": None, "test_results": None,
        "review": None, "devops_artifacts": None, "doc_artifacts": None,
        "research_document": None, "content_piece": None,
        "current_agent": "backend_dev", "errors": [], "metadata": {},
        "_run_id": "coh123", "_thread_id": "t1", "_kb_context": "",
    }


def test_dev_team_coherence_emits_agent_start_end():
    """CoherenceAgent called post-supervisor should still emit agent.start/end."""
    from antcrew.teams.dev_team import DevTeam

    llm = MagicMock()
    llm.max_cost_usd = None
    llm.get_usage_summary.return_value = {"total_cost_usd": 0.0}
    llm.trace = None
    llm._trace_run_id = None

    team = DevTeam(llm, enable_coherence=True)

    coherence_agent = MagicMock()
    coherence_agent.run.return_value = {"coherence_issues": []}
    team._agents["coherence"] = coherence_agent

    state = _make_dev_team_coherence_state()

    with capture("agent.start", "agent.end") as events:
        with patch.object(team._supervisor, "build") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.return_value = state
            mock_build.return_value = mock_app
            team.run("build auth")

    agent_names = [e.payload.get("agent_name") for e in events]
    assert "coherence" in agent_names

    start_events = [e for e in events if e.type == "agent.start" and e.payload.get("agent_name") == "coherence"]
    end_events = [e for e in events if e.type == "agent.end" and e.payload.get("agent_name") == "coherence"]
    assert len(start_events) == 1
    assert len(end_events) == 1
    assert "duration_s" in end_events[0].payload


def test_dev_team_coherence_run_id_on_agent_events():
    from antcrew.teams.dev_team import DevTeam

    llm = MagicMock()
    llm.max_cost_usd = None
    llm.get_usage_summary.return_value = {"total_cost_usd": 0.0}
    llm.trace = None
    llm._trace_run_id = None

    team = DevTeam(llm, enable_coherence=True)

    coherence_agent = MagicMock()
    coherence_agent.run.return_value = {"coherence_issues": []}
    team._agents["coherence"] = coherence_agent

    state = _make_dev_team_coherence_state()

    with capture("agent.start", "agent.end") as events:
        with patch.object(team._supervisor, "build") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.return_value = state
            mock_build.return_value = mock_app
            team.run("build auth")

    coherence_events = [e for e in events if e.payload.get("agent_name") == "coherence"]
    for e in coherence_events:
        assert e.run_id is not None


# ---------------------------------------------------------------------------
# EventBus thread-safety
# ---------------------------------------------------------------------------

def test_bus_concurrent_subscribe_emit():
    """Multiple threads subscribing and emitting concurrently should not corrupt state."""
    received = []
    lock = threading.Lock()
    errors = []

    def subscriber(event):
        with lock:
            received.append(event.run_id)

    def emit_worker(run_id: str, n: int):
        try:
            for _ in range(n):
                bus.emit("concurrent.test", {"x": 1}, run_id=run_id)
        except Exception as e:
            with lock:
                errors.append(e)

    bus.subscribe("concurrent.test", subscriber)
    n_threads, n_events = 10, 50
    threads = [
        threading.Thread(target=emit_worker, args=(f"run-{i}", n_events))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    assert len(received) == n_threads * n_events


def test_bus_concurrent_subscribe_unsubscribe():
    """subscribe/unsubscribe from multiple threads should not raise."""
    errors = []

    def worker():
        collected = []
        handler = collected.append
        try:
            bus.subscribe("thread.test", handler)
            bus.emit("thread.test", {})
            bus.unsubscribe("thread.test", handler)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"


def test_bus_emit_does_not_hold_lock_during_handler():
    """Handlers that call bus.subscribe() should not deadlock."""
    results = []

    def handler_that_subscribes(event):
        bus.subscribe("nested.event", lambda e: results.append("nested"))
        results.append("original")

    bus.subscribe("outer.event", handler_that_subscribes)
    bus.emit("outer.event", {})  # Must not deadlock
    assert "original" in results


def test_isolated_bus_instances_are_independent():
    """Two EventBus instances share no state."""
    bus_a = EventBus()
    bus_b = EventBus()
    received_a, received_b = [], []
    bus_a.subscribe("x", received_a.append)
    bus_b.subscribe("x", received_b.append)

    bus_a.emit("x", {})
    assert len(received_a) == 1
    assert len(received_b) == 0
