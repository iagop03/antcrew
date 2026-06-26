"""Tests for antcrew replay and antcrew trace CLI commands."""
from __future__ import annotations

import pytest
from pathlib import Path
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.trace import TraceLog

cli = CliRunner()


# ---------------------------------------------------------------------------
# antcrew trace — list view
# ---------------------------------------------------------------------------

def test_trace_list_empty(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    tlog.close()
    result = cli.invoke(app, ["trace", str(db)])
    assert result.exit_code == 0
    assert "No runs recorded" in result.output


def test_trace_list_shows_runs(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    tlog.begin_run(thread_id="sprint-1", request="Build login", team="DevTeam")
    tlog.close()

    result = cli.invoke(app, ["trace", str(db)])
    assert result.exit_code == 0
    assert "sprint-1" in result.output
    assert "DevTeam" in result.output


def test_trace_file_not_found(tmp_path):
    result = cli.invoke(app, ["trace", str(tmp_path / "missing.db")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_trace_detail_by_run_id(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    run_id = tlog.begin_run(thread_id="t1", request="Build login", team="DevTeam")
    tlog.record_call(
        run_id=run_id, agent_name="pm", duration_ms=200.0,
        input_tokens=50, output_tokens=100, cost_usd=0.001,
        prompt_snippet="Write a PRD",
    )
    tlog.end_run(run_id, cost_usd=0.001)
    tlog.close()

    result = cli.invoke(app, ["trace", str(db), "--run", run_id])
    assert result.exit_code == 0
    assert "pm" in result.output
    assert "Build login" in result.output


def test_trace_detail_by_thread(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    run_id = tlog.begin_run(thread_id="sprint-42", request="Add OAuth", team="DevTeam")
    tlog.end_run(run_id)
    tlog.close()

    result = cli.invoke(app, ["trace", str(db), "--thread", "sprint-42"])
    assert result.exit_code == 0
    assert "Add OAuth" in result.output


def test_trace_unknown_run_id(tmp_path):
    db = tmp_path / "t.db"
    TraceLog(db).close()
    result = cli.invoke(app, ["trace", str(db), "--run", "nonexistent-id"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_trace_unknown_thread(tmp_path):
    db = tmp_path / "t.db"
    TraceLog(db).close()
    result = cli.invoke(app, ["trace", str(db), "--thread", "ghost-thread"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# antcrew replay — argument resolution
# ---------------------------------------------------------------------------

def test_replay_requires_checkpointer(tmp_path):
    """--checkpointer is required (typer enforces this)."""
    result = cli.invoke(app, ["replay", "sprint-1"])
    assert result.exit_code != 0


def test_replay_no_trace_no_request_fails(tmp_path):
    """Without --trace and without --request, replay cannot determine request."""
    from antcrew.checkpointers import SqliteSaver as _SS
    if _SS is None:
        pytest.skip("langgraph-checkpoint-sqlite not installed")

    db = tmp_path / "cp.db"
    result = cli.invoke(app, [
        "replay", "sprint-1",
        "--checkpointer", str(db),
        "--team", "dev",
        "--model", "simulated",
        # missing --request and --trace
    ])
    assert result.exit_code == 1
    assert "request" in result.output.lower()


def test_replay_with_trace_auto_detects_request(tmp_path):
    """With a TraceLog that has the thread, replay auto-detects request + team."""
    from antcrew.checkpointers import SqliteSaver as _SS
    if _SS is None:
        pytest.skip("langgraph-checkpoint-sqlite not installed")

    # Seed TraceLog with a prior run on the thread
    trace_db = tmp_path / "trace.db"
    tlog = TraceLog(trace_db)
    run_id = tlog.begin_run(
        thread_id="sprint-99", request="Build a login module", team="DevTeam"
    )
    tlog.end_run(run_id, cost_usd=0.05, status="done")
    tlog.close()

    cp_db = tmp_path / "threads.db"

    result = cli.invoke(app, [
        "replay", "sprint-99",
        "--checkpointer", str(cp_db),
        "--trace", str(trace_db),
        "--model", "simulated",
    ])
    # Should run to completion (SimulatedLLM, no API key needed)
    assert result.exit_code == 0, result.output


def test_replay_explicit_request_and_team(tmp_path):
    """Without --trace, explicit --request and --team suffice."""
    from antcrew.checkpointers import SqliteSaver as _SS
    if _SS is None:
        pytest.skip("langgraph-checkpoint-sqlite not installed")

    cp_db = tmp_path / "threads.db"
    result = cli.invoke(app, [
        "replay", "sprint-1",
        "--checkpointer", str(cp_db),
        "--team", "dev",
        "--model", "simulated",
        "--request", "Build a login module",
    ])
    assert result.exit_code == 0, result.output


def test_replay_trace_thread_not_found_warns(tmp_path):
    """If thread_id is not in TraceLog, shows a warning and requires explicit args."""
    from antcrew.checkpointers import SqliteSaver as _SS
    if _SS is None:
        pytest.skip("langgraph-checkpoint-sqlite not installed")

    trace_db = tmp_path / "trace.db"
    TraceLog(trace_db).close()  # empty trace log
    cp_db = tmp_path / "threads.db"

    result = cli.invoke(app, [
        "replay", "missing-thread",
        "--checkpointer", str(cp_db),
        "--trace", str(trace_db),
        # No --team or --request → should fail after warning
    ])
    assert result.exit_code == 1
    assert "request" in result.output.lower() or "Warning" in result.output
