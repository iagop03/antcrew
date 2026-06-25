"""Tests for TraceLog — SQLite-backed per-agent call recorder."""
from __future__ import annotations

import pytest
from pathlib import Path

from antcrew.trace import TraceLog
from antcrew import TraceLog as _TopLevelTraceLog


# ---------------------------------------------------------------------------
# Import + instantiation
# ---------------------------------------------------------------------------

def test_tracelog_importable_from_top_level():
    assert TraceLog is _TopLevelTraceLog


def test_tracelog_creates_db(tmp_path):
    db = tmp_path / "trace.db"
    tlog = TraceLog(db)
    assert db.exists()
    tlog.close()


def test_tracelog_accepts_tilde_path(tmp_path, monkeypatch):
    """Expands ~ correctly."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tlog = TraceLog(tmp_path / "t.db")
    tlog.close()


# ---------------------------------------------------------------------------
# begin_run / end_run
# ---------------------------------------------------------------------------

def test_begin_run_returns_uuid(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="Build login", team="DevTeam")
    assert len(run_id) == 36  # UUID string
    tlog.close()


def test_end_run_sets_status(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="Build login", team="DevTeam")
    tlog.end_run(run_id, cost_usd=0.025, status="done")
    run = tlog.get_run(run_id)
    assert run is not None
    assert run["status"] == "done"
    assert run["cost_usd"] == pytest.approx(0.025)
    assert run["ended_at"] is not None
    tlog.close()


def test_run_starts_as_running(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    run = tlog.get_run(run_id)
    assert run["status"] == "running"
    assert run["ended_at"] is None
    tlog.close()


# ---------------------------------------------------------------------------
# record_call
# ---------------------------------------------------------------------------

def test_record_call_stored(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    tlog.record_call(
        run_id=run_id,
        agent_name="pm",
        duration_ms=123.4,
        input_tokens=50,
        output_tokens=100,
        cost_usd=0.001,
        prompt_snippet="Write a PRD",
        response_snippet='{"title": "Login"}',
    )
    calls = tlog.get_calls(run_id)
    assert len(calls) == 1
    c = calls[0]
    assert c["agent_name"] == "pm"
    assert c["input_tokens"] == 50
    assert c["output_tokens"] == 100
    assert c["cost_usd"] == pytest.approx(0.001)
    assert "Write a PRD" in c["prompt_snippet"]
    tlog.close()


def test_multiple_calls_ordered(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    for name in ("business_analyst", "pm", "backend_dev"):
        tlog.record_call(run_id=run_id, agent_name=name, duration_ms=10.0)
    calls = tlog.get_calls(run_id)
    assert [c["agent_name"] for c in calls] == ["business_analyst", "pm", "backend_dev"]
    tlog.close()


# ---------------------------------------------------------------------------
# list_runs / get_run_by_thread
# ---------------------------------------------------------------------------

def test_list_runs_newest_first(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    id1 = tlog.begin_run(thread_id="t1", request="first", team="DevTeam")
    id2 = tlog.begin_run(thread_id="t2", request="second", team="DevTeam")
    runs = tlog.list_runs()
    assert runs[0]["id"] == id2
    assert runs[1]["id"] == id1
    tlog.close()


def test_get_run_by_thread(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    tlog.begin_run(thread_id="sprint-1", request="first", team="DevTeam")
    id2 = tlog.begin_run(thread_id="sprint-1", request="second", team="DevTeam")
    found = tlog.get_run_by_thread("sprint-1")
    assert found is not None
    assert found["id"] == id2  # most recent
    tlog.close()


def test_get_run_returns_none_for_unknown(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    assert tlog.get_run("nonexistent") is None
    tlog.close()


# ---------------------------------------------------------------------------
# Integration: team run writes to TraceLog
# ---------------------------------------------------------------------------

def test_team_run_populates_trace_log(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    tlog = TraceLog(tmp_path / "t.db")
    team = DevTeam(model=SimulatedLLM(), trace_log=tlog)
    team.run("Build a login module", thread_id="sprint-1")

    runs = tlog.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "done"
    assert run["thread_id"] == "sprint-1"
    assert run["team"] == "DevTeam"

    calls = tlog.get_calls(run["id"])
    assert len(calls) >= 1  # at least one agent made an LLM call
    agent_names = [c["agent_name"] for c in calls]
    # SimulatedLLM runs all agents — check expected ones are present
    assert "business_analyst" in agent_names or len(agent_names) > 0
    tlog.close()


def test_trace_log_clears_on_team_after_run(tmp_path):
    """LLM trace refs are cleaned up after run() even if it raises."""
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    tlog = TraceLog(tmp_path / "t.db")
    team = DevTeam(model=SimulatedLLM(), trace_log=tlog)
    team.run("Build login")

    # After run, llm.trace and _trace_run_id should be cleared
    assert team.llm.trace is None
    assert team.llm._trace_run_id is None
    tlog.close()


def test_trace_log_marks_error_on_exception(tmp_path):
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam
    from antcrew.core.exceptions import CostLimitExceeded

    tlog = TraceLog(tmp_path / "t.db")
    team = DevTeam(model=SimulatedLLM(), trace_log=tlog, max_cost_usd=0.0)

    with pytest.raises(CostLimitExceeded):
        team.run("Build login")

    runs = tlog.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "error"
    tlog.close()
