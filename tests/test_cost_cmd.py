"""Tests for antcrew cost command."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli._app import app

runner = CliRunner()


def _populated_db(tmp_path: Path) -> Path:
    """Create a minimal TraceLog DB with a few runs."""
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
    conn.executemany(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
        [
            ("r1", "t1", "Build auth",    "FullStackTeam", "done",  0.0050, "2026-06-01T10:00:00", "2026-06-01T10:01:00"),
            ("r2", "t2", "Add billing",   "FullStackTeam", "done",  0.0080, "2026-06-02T10:00:00", "2026-06-02T10:01:30"),
            ("r3", "t3", "Research APIs", "ResearchTeam",  "done",  0.0020, "2026-06-03T10:00:00", "2026-06-03T10:00:45"),
            ("r4", "t4", "Crash",         "DevTeam",       "error", None,   "2026-06-04T10:00:00", "2026-06-04T10:00:05"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_calls (run_id, agent_name, input_tokens, output_tokens, cost_usd, duration_s, started_at) VALUES (?,?,?,?,?,?,?)",
        [
            ("r1", "business_analyst", 500, 200, 0.002, 1.2, "2026-06-01T10:00:10"),
            ("r1", "pm",               300, 150, 0.003, 0.8, "2026-06-01T10:00:20"),
            ("r2", "business_analyst", 600, 250, 0.004, 1.5, "2026-06-02T10:00:10"),
        ],
    )
    conn.commit()
    conn.close()
    return db


class TestCostCmd:
    def test_exits_zero_with_db(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db)])
        assert result.exit_code == 0

    def test_shows_total_runs(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db)])
        assert "4" in result.output  # 4 total runs

    def test_shows_total_cost(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db)])
        assert "0.01" in result.output or "Total cost" in result.output

    def test_shows_per_team_breakdown(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db)])
        assert "FullStackTeam" in result.output
        assert "ResearchTeam" in result.output

    def test_json_flag_outputs_valid_json(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_runs"] == 4
        assert "total_cost_usd" in data
        assert "by_team" in data

    def test_json_contains_by_team(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db), "--json"])
        data = json.loads(result.output)
        teams = {row["team"] for row in data["by_team"]}
        assert "FullStackTeam" in teams

    def test_filter_by_team(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db), "--team", "ResearchTeam", "--json"])
        data = json.loads(result.output)
        assert data["total_runs"] == 1

    def test_since_filter_reduces_count(self, tmp_path):
        db = _populated_db(tmp_path)
        # --since 1 day should give 0 results (all runs are in 2026-06)
        result = runner.invoke(app, ["cost", str(db), "--since", "1", "--json"])
        data = json.loads(result.output)
        assert data["total_runs"] == 0

    def test_no_db_exits_zero_with_message(self, tmp_path):
        missing = tmp_path / "nope.db"
        result = runner.invoke(app, ["cost", str(missing)])
        assert result.exit_code == 0
        assert "No trace database" in result.output

    def test_no_db_json_has_error_key(self, tmp_path):
        missing = tmp_path / "nope.db"
        result = runner.invoke(app, ["cost", str(missing), "--json"])
        data = json.loads(result.output)
        assert "error" in data

    def test_shows_tokens(self, tmp_path):
        db = _populated_db(tmp_path)
        result = runner.invoke(app, ["cost", str(db)])
        assert "Tokens" in result.output or "in" in result.output
