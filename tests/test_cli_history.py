"""Tests for the `antcrew history` CLI command."""
from __future__ import annotations

import csv
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli import _parse_since, app
from antcrew.trace import TraceLog

cli = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed(db: Path, runs: list[dict]) -> list[str]:
    """Insert runs into a TraceLog and return their IDs."""
    tlog = TraceLog(db)
    ids = []
    for r in runs:
        rid = tlog.begin_run(
            thread_id=r["thread_id"],
            request=r.get("request", "task"),
            team=r.get("team", "DevTeam"),
        )
        tlog.end_run(rid, cost_usd=r.get("cost_usd", 0.0), status=r.get("status", "done"))
        ids.append(rid)
    tlog.close()
    return ids


# ---------------------------------------------------------------------------
# _parse_since helper
# ---------------------------------------------------------------------------

def test_parse_since_absolute_date():
    result = _parse_since("2025-01-15")
    assert result == "2025-01-15T00:00:00+00:00"


def test_parse_since_relative_days():
    result = _parse_since("7d")
    assert "T" in result  # is an ISO timestamp
    # roughly in the past 7 days (not in the future, not 8+ days ago)
    from datetime import datetime, timedelta, timezone
    dt = datetime.fromisoformat(result)
    now = datetime.now(timezone.utc)
    assert now - timedelta(days=8) < dt < now


def test_parse_since_zero_days():
    result = _parse_since("0d")
    from datetime import datetime, timedelta, timezone
    dt = datetime.fromisoformat(result)
    assert datetime.now(timezone.utc) - dt < timedelta(minutes=1)


def test_parse_since_strips_whitespace():
    result = _parse_since("  2024-06-01  ")
    assert result.startswith("2024-06-01")


# ---------------------------------------------------------------------------
# File-not-found guard
# ---------------------------------------------------------------------------

def test_history_file_not_found(tmp_path):
    result = cli.invoke(app, ["history", str(tmp_path / "missing.db")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Empty database
# ---------------------------------------------------------------------------

def test_history_empty_db(tmp_path):
    db = tmp_path / "t.db"
    TraceLog(db).close()

    result = cli.invoke(app, ["history", str(db)])
    assert result.exit_code == 0
    assert "No runs found" in result.output


# ---------------------------------------------------------------------------
# Basic output
# ---------------------------------------------------------------------------

def test_history_shows_runs(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "sprint-1", "request": "Build login", "team": "DevTeam"}])

    result = cli.invoke(app, ["history", str(db)])
    assert result.exit_code == 0
    assert "sprint-1" in result.output
    # Rich may wrap the Request cell; normalise whitespace before checking.
    normalised = " ".join(result.output.split())
    assert "Build login" in normalised


def test_history_shows_summary_panel(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "t1", "team": "DevTeam", "cost_usd": 0.05}])

    result = cli.invoke(app, ["history", str(db)])
    assert result.exit_code == 0
    assert "Summary" in result.output


def test_history_shows_cost(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "t1", "team": "DevTeam", "cost_usd": 1.2345}])

    result = cli.invoke(app, ["history", str(db)])
    assert result.exit_code == 0
    assert "1.2345" in result.output


def test_history_shows_by_team_breakdown(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [
        {"thread_id": "a", "team": "DevTeam"},
        {"thread_id": "b", "team": "ResearchTeam"},
    ])

    result = cli.invoke(app, ["history", str(db)])
    assert result.exit_code == 0
    assert "By team" in result.output
    assert "DevTeam" in result.output
    assert "ResearchTeam" in result.output


# ---------------------------------------------------------------------------
# --stats flag
# ---------------------------------------------------------------------------

def test_history_stats_only(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "t1", "team": "DevTeam", "request": "Build auth"}])

    result = cli.invoke(app, ["history", str(db), "--stats"])
    assert result.exit_code == 0
    assert "Summary" in result.output
    # Run table should not appear (thread IDs absent when --stats hides the table)
    assert "t1" not in result.output


# ---------------------------------------------------------------------------
# --team filter
# ---------------------------------------------------------------------------

def test_history_team_filter_includes(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [
        {"thread_id": "d1", "team": "DevTeam"},
        {"thread_id": "r1", "team": "ResearchTeam"},
    ])

    result = cli.invoke(app, ["history", str(db), "--team", "DevTeam"])
    assert result.exit_code == 0
    assert "d1" in result.output
    assert "r1" not in result.output


def test_history_team_filter_no_match(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "t1", "team": "DevTeam"}])

    result = cli.invoke(app, ["history", str(db), "--team", "ContentTeam"])
    assert result.exit_code == 0
    # Either "No runs found" or "No runs match" is fine
    assert "no runs" in result.output.lower()


# ---------------------------------------------------------------------------
# --status filter
# ---------------------------------------------------------------------------

def test_history_status_done_filter(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    rid1 = tlog.begin_run(thread_id="ok", request="ok", team="Dev")
    tlog.end_run(rid1, status="done")
    rid2 = tlog.begin_run(thread_id="fail", request="fail", team="Dev")
    tlog.end_run(rid2, status="error")
    tlog.close()

    result = cli.invoke(app, ["history", str(db), "--status", "done"])
    assert result.exit_code == 0
    assert "ok" in result.output
    assert "fail" not in result.output


def test_history_status_error_filter(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    rid1 = tlog.begin_run(thread_id="thread-pass", request="passing task", team="Dev")
    tlog.end_run(rid1, status="done")
    rid2 = tlog.begin_run(thread_id="thread-fail", request="failing task", team="Dev")
    tlog.end_run(rid2, status="error")
    tlog.close()

    result = cli.invoke(app, ["history", str(db), "--status", "error"])
    assert result.exit_code == 0
    assert "thread-fail" in result.output
    assert "thread-pass" not in result.output


# ---------------------------------------------------------------------------
# --since filter
# ---------------------------------------------------------------------------

def test_history_since_absolute_excludes_old(tmp_path):
    db = tmp_path / "t.db"
    tlog = TraceLog(db)
    # Insert a run with a past started_at directly via SQL
    conn = tlog._conn
    conn.execute(
        "INSERT INTO runs(id,thread_id,request,team,started_at,status) VALUES (?,?,?,?,?,?)",
        ("old-id", "old-thread", "old task", "DevTeam", "2020-01-01T00:00:00+00:00", "done"),
    )
    conn.commit()
    tlog.close()

    result = cli.invoke(app, ["history", str(db), "--since", "2024-01-01"])
    assert result.exit_code == 0
    assert "old-thread" not in result.output


def test_history_since_relative_includes_recent(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "recent", "team": "DevTeam"}])

    result = cli.invoke(app, ["history", str(db), "--since", "7d"])
    assert result.exit_code == 0
    assert "recent" in result.output


def test_history_since_invalid_value(tmp_path):
    db = tmp_path / "t.db"
    TraceLog(db).close()

    result = cli.invoke(app, ["history", str(db), "--since", "not-a-date"])
    # _parse_since for "not-a-date" won't raise — it returns "not-a-dateT00:00:00+00:00"
    # which is just an always-past ISO string, so runs still show. No crash expected.
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --limit flag
# ---------------------------------------------------------------------------

def test_history_limit_truncates(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": f"t{i}", "team": "Dev"} for i in range(5)])

    result = cli.invoke(app, ["history", str(db), "--limit", "2"])
    assert result.exit_code == 0
    assert "Showing 2" in result.output


def test_history_limit_no_truncation_message_when_under(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "only", "team": "Dev"}])

    result = cli.invoke(app, ["history", str(db), "--limit", "50"])
    assert result.exit_code == 0
    assert "Showing 50" not in result.output


# ---------------------------------------------------------------------------
# --export CSV
# ---------------------------------------------------------------------------

def test_history_export_creates_csv(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "sprint-1", "request": "Build login", "team": "DevTeam", "cost_usd": 0.5}])

    csv_path = tmp_path / "runs.csv"
    result = cli.invoke(app, ["history", str(db), "--export", str(csv_path)])
    assert result.exit_code == 0
    assert csv_path.exists()


def test_history_export_csv_content(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "sprint-99", "request": "Add OAuth", "team": "DevTeam"}])

    csv_path = tmp_path / "runs.csv"
    cli.invoke(app, ["history", str(db), "--export", str(csv_path)])

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "sprint-99"
    assert rows[0]["team"] == "DevTeam"
    assert "Add OAuth" in rows[0]["request"]


def test_history_export_respects_filters(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [
        {"thread_id": "keep",   "team": "DevTeam"},
        {"thread_id": "remove", "team": "ResearchTeam"},
    ])

    csv_path = tmp_path / "filtered.csv"
    cli.invoke(app, ["history", str(db), "--team", "DevTeam", "--export", str(csv_path)])

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    threads = [r["thread_id"] for r in rows]
    assert "keep" in threads
    assert "remove" not in threads


def test_history_export_prints_confirmation(tmp_path):
    db = tmp_path / "t.db"
    _seed(db, [{"thread_id": "t1", "team": "Dev"}])

    csv_path = tmp_path / "out.csv"
    result = cli.invoke(app, ["history", str(db), "--export", str(csv_path)])
    assert result.exit_code == 0
    assert "Exported" in result.output
