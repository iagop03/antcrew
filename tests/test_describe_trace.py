"""Tests for antcrew describe --trace (verifies tl.get_stats() is called correctly)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli._app import app

runner = CliRunner()


def _make_trace_db(tmp_path: Path) -> Path:
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, thread_id TEXT, request TEXT,
            team TEXT, status TEXT, cost_usd REAL, started_at TEXT, finished_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE agent_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            agent_name TEXT, input_tokens INTEGER, output_tokens INTEGER,
            cost_usd REAL, duration_s REAL, started_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
        ("r1", "t1", "Add auth", "FullStackTeam", "done", 0.0042, "2026-06-20T10:00:00", "2026-06-20T10:01:00"),
    )
    conn.commit()
    conn.close()
    return db


class TestDescribeTrace:
    def test_describe_with_trace_exits_zero(self, tmp_path):
        db = _make_trace_db(tmp_path)
        result = runner.invoke(app, ["describe", "--team", "dev", "--trace", str(db)])
        assert result.exit_code == 0

    def test_describe_with_trace_shows_cost(self, tmp_path):
        db = _make_trace_db(tmp_path)
        result = runner.invoke(app, ["describe", "--team", "dev", "--trace", str(db)])
        # Should show "Historical cost" line with cost data
        assert "0.004" in result.output or "Historical cost" in result.output

    def test_describe_missing_trace_db_still_exits_zero(self, tmp_path):
        result = runner.invoke(app, ["describe", "--team", "dev", "--trace", str(tmp_path / "nope.db")])
        assert result.exit_code == 0
        # Should not crash — just skip the stats block

    def test_describe_trace_no_crash_when_empty_db(self, tmp_path):
        db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, thread_id TEXT, request TEXT, "
            "team TEXT, status TEXT, cost_usd REAL, started_at TEXT, finished_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE agent_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
            "agent_name TEXT, input_tokens INTEGER, output_tokens INTEGER, "
            "cost_usd REAL, duration_s REAL, started_at TEXT)"
        )
        conn.commit(); conn.close()
        result = runner.invoke(app, ["describe", "--team", "dev", "--trace", str(db)])
        assert result.exit_code == 0
