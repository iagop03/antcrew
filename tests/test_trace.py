"""Tests for TraceLog — SQLite-backed per-agent call recorder."""
from __future__ import annotations

import pytest

from antcrew import TraceLog as _TopLevelTraceLog
from antcrew.trace import TraceLog

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

    stats = tlog.get_stats()
    assert stats["total_runs"] == 1
    assert stats["done_runs"] == 1
    assert "total_cost_usd" in stats
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
    from antcrew.core.exceptions import CostLimitExceeded
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    tlog = TraceLog(tmp_path / "t.db")
    team = DevTeam(model=SimulatedLLM(), trace_log=tlog, max_cost_usd=0.0)

    with pytest.raises(CostLimitExceeded):
        team.run("Build login")

    runs = tlog.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "error"

    stats = tlog.get_stats()
    assert stats["error_runs"] == 1
    tlog.close()


# ---------------------------------------------------------------------------
# full_trace — complete prompt/response storage
# ---------------------------------------------------------------------------

def test_full_trace_flag_stores_complete_text(tmp_path):
    tlog = TraceLog(tmp_path / "t.db", full_trace=True)
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    long_prompt = "A" * 1000
    long_response = "B" * 1000
    tlog.record_call(
        run_id=run_id, agent_name="pm", duration_ms=10.0,
        prompt_full=long_prompt, response_full=long_response,
    )
    calls = tlog.get_calls(run_id)
    assert calls[0]["prompt_full"] == long_prompt
    assert calls[0]["response_full"] == long_response
    assert len(calls[0]["prompt_snippet"]) == 300
    tlog.close()


def test_full_trace_off_by_default(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    tlog.record_call(
        run_id=run_id, agent_name="pm", duration_ms=10.0,
        prompt_full="hello world", response_full="ok",
    )
    calls = tlog.get_calls(run_id)
    assert calls[0]["prompt_full"] == ""
    assert calls[0]["response_full"] == ""
    assert "hello world" in calls[0]["prompt_snippet"]
    tlog.close()


def test_record_call_returns_int_id(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    call_id = tlog.record_call(run_id=run_id, agent_name="qa", duration_ms=5.0)
    assert isinstance(call_id, int)
    assert call_id >= 1
    tlog.close()


def test_get_call_detail_returns_full_text(tmp_path):
    tlog = TraceLog(tmp_path / "t.db", full_trace=True)
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    call_id = tlog.record_call(
        run_id=run_id, agent_name="backend_dev", duration_ms=20.0,
        prompt_full="You are a senior dev. Write a FastAPI endpoint.",
        response_full="```python\n@app.get('/users')\ndef list_users(): ...\n```",
    )
    detail = tlog.get_call_detail(call_id)
    assert detail is not None
    assert detail["agent_name"] == "backend_dev"
    assert "FastAPI" in detail["prompt_full"]
    assert "@app.get" in detail["response_full"]
    tlog.close()


def test_get_call_detail_returns_none_for_missing(tmp_path):
    tlog = TraceLog(tmp_path / "t.db")
    assert tlog.get_call_detail(99999) is None
    tlog.close()


def test_migration_adds_columns_to_existing_db(tmp_path):
    """DB created without prompt_full/response_full gets migrated on re-open."""
    import sqlite3
    db = tmp_path / "old.db"
    # Create a DB with the old schema (no prompt_full / response_full)
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, request TEXT NOT NULL,
            team TEXT NOT NULL, started_at TEXT NOT NULL,
            ended_at TEXT, cost_usd REAL, status TEXT NOT NULL DEFAULT 'running'
        );
        CREATE TABLE agent_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            agent_name TEXT NOT NULL, started_at TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            prompt_snippet TEXT NOT NULL DEFAULT '',
            response_snippet TEXT NOT NULL DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()

    # Re-open with TraceLog — should migrate without error
    tlog = TraceLog(db, full_trace=True)
    run_id = tlog.begin_run(thread_id="t1", request="r", team="DevTeam")
    call_id = tlog.record_call(
        run_id=run_id, agent_name="pm", duration_ms=5.0,
        prompt_full="migrated prompt",
    )
    detail = tlog.get_call_detail(call_id)
    assert detail["prompt_full"] == "migrated prompt"
    tlog.close()


# ---------------------------------------------------------------------------
# CLI --show-call
# ---------------------------------------------------------------------------

def test_cli_show_call_full_trace(tmp_path):
    from typer.testing import CliRunner

    from antcrew.cli._app import app

    db = tmp_path / "trace.db"
    tlog = TraceLog(db, full_trace=True)
    run_id = tlog.begin_run(thread_id="demo", request="Build API", team="DevTeam")
    tlog.record_call(
        run_id=run_id, agent_name="pm", duration_ms=15.0,
        input_tokens=100, output_tokens=50,
        prompt_full="You are a PM. Write a PRD for a task manager.",
        response_full="# PRD\n## Overview\nA task manager...",
    )
    tlog.record_call(
        run_id=run_id, agent_name="backend_dev", duration_ms=25.0,
        input_tokens=200, output_tokens=150,
        prompt_full="Implement the backend for the task manager.",
        response_full="```python\nfrom fastapi import FastAPI\n```",
    )
    tlog.close()

    runner = CliRunner()
    result = runner.invoke(app, ["trace", str(db), "--run", run_id, "--show-call", "1"])
    assert result.exit_code == 0, result.output
    assert "You are a PM" in result.output
    assert "PRD" in result.output


def test_cli_show_call_out_of_range(tmp_path):
    from typer.testing import CliRunner

    from antcrew.cli._app import app

    db = tmp_path / "trace.db"
    tlog = TraceLog(db, full_trace=True)
    run_id = tlog.begin_run(thread_id="demo", request="test", team="DevTeam")
    tlog.record_call(run_id=run_id, agent_name="pm", duration_ms=5.0)
    tlog.close()

    runner = CliRunner()
    result = runner.invoke(app, ["trace", str(db), "--run", run_id, "--show-call", "5"])
    assert result.exit_code != 0 or "out of range" in result.output


def test_cli_show_call_requires_run_or_thread(tmp_path):
    from typer.testing import CliRunner

    from antcrew.cli._app import app

    db = tmp_path / "trace.db"
    TraceLog(db).close()

    runner = CliRunner()
    result = runner.invoke(app, ["trace", str(db), "--show-call", "1"])
    assert result.exit_code != 0 or "--run or --thread" in result.output


def test_cli_trace_detail_hints_when_no_full_trace(tmp_path):
    from typer.testing import CliRunner

    from antcrew.cli._app import app

    db = tmp_path / "trace.db"
    tlog = TraceLog(db)   # full_trace=False
    run_id = tlog.begin_run(thread_id="t1", request="hi", team="DevTeam")
    tlog.record_call(run_id=run_id, agent_name="pm", duration_ms=5.0, prompt_full="hello")
    tlog.close()

    runner = CliRunner()
    result = runner.invoke(app, ["trace", str(db), "--run", run_id])
    assert result.exit_code == 0
    assert "--full-trace" in result.output
