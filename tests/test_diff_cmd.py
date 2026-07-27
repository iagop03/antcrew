"""Tests for 'antcrew diff' CLI command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()


def _write(tmp: Path, name: str, data: dict) -> Path:
    p = tmp / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _base_state(**overrides) -> dict:
    state: dict = {
        "request": "Build login module",
        "messages": [],
        "prd": {"title": "Login Module", "summary": "A login module.", "goals": []},
        "tickets": [
            {"id": "T-1", "title": "Create auth", "description": "", "status": "open", "priority": "medium"},
        ],
        "code_artifacts": [
            {"ticket_id": "T-1", "file_path": "auth.py", "content": "def auth(): pass\n", "language": "python"},
        ],
        "metadata": {},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

def test_diff_identical_files(tmp_path):
    state = _base_state()
    a = _write(tmp_path, "a.json", state)
    b = _write(tmp_path, "b.json", state)
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "No differences found" in result.output


def test_diff_missing_file_exits(tmp_path):
    a = _write(tmp_path, "a.json", _base_state())
    result = runner.invoke(app, ["diff", str(a), str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Request diff
# ---------------------------------------------------------------------------

def test_diff_detects_request_change(tmp_path):
    a = _write(tmp_path, "a.json", _base_state(request="Build login"))
    b = _write(tmp_path, "b.json", _base_state(request="Build login with OAuth"))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "Build login" in result.output
    assert "Build login with OAuth" in result.output


# ---------------------------------------------------------------------------
# PRD diff
# ---------------------------------------------------------------------------

def test_diff_detects_prd_title_change(tmp_path):
    a = _write(tmp_path, "a.json", _base_state(prd={"title": "Old Title", "summary": "x", "goals": []}))
    b = _write(tmp_path, "b.json", _base_state(prd={"title": "New Title", "summary": "x", "goals": []}))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "Old Title" in result.output
    assert "New Title" in result.output


def test_diff_no_prd_section_when_same(tmp_path):
    state = _base_state()
    a = _write(tmp_path, "a.json", state)
    b = _write(tmp_path, "b.json", state)
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert "PRD" not in result.output or "No differences" in result.output


# ---------------------------------------------------------------------------
# Ticket diff
# ---------------------------------------------------------------------------

def test_diff_shows_added_ticket(tmp_path):
    tix_a = [{"id": "T-1", "title": "Auth", "description": "", "status": "open", "priority": "medium"}]
    tix_b = tix_a + [{"id": "T-2", "title": "OAuth", "description": "", "status": "open", "priority": "high"}]
    a = _write(tmp_path, "a.json", _base_state(tickets=tix_a))
    b = _write(tmp_path, "b.json", _base_state(tickets=tix_b))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "T-2" in result.output
    assert "OAuth" in result.output


def test_diff_shows_removed_ticket(tmp_path):
    tix_full = [
        {"id": "T-1", "title": "Auth", "description": "", "status": "open", "priority": "medium"},
        {"id": "T-2", "title": "OAuth", "description": "", "status": "open", "priority": "high"},
    ]
    tix_partial = tix_full[:1]
    a = _write(tmp_path, "a.json", _base_state(tickets=tix_full))
    b = _write(tmp_path, "b.json", _base_state(tickets=tix_partial))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "T-2" in result.output


# ---------------------------------------------------------------------------
# Code artifact diff
# ---------------------------------------------------------------------------

def test_diff_shows_added_file(tmp_path):
    arts_a = [{"ticket_id": "T-1", "file_path": "auth.py", "content": "# auth\n", "language": "python"}]
    arts_b = arts_a + [{"ticket_id": "T-1", "file_path": "oauth.py", "content": "# oauth\n", "language": "python"}]
    a = _write(tmp_path, "a.json", _base_state(code_artifacts=arts_a))
    b = _write(tmp_path, "b.json", _base_state(code_artifacts=arts_b))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "oauth.py" in result.output
    assert "new" in result.output.lower()


def test_diff_shows_removed_file(tmp_path):
    arts_full = [
        {"ticket_id": "T-1", "file_path": "auth.py", "content": "# auth\n", "language": "python"},
        {"ticket_id": "T-1", "file_path": "extra.py", "content": "# extra\n", "language": "python"},
    ]
    arts_short = arts_full[:1]
    a = _write(tmp_path, "a.json", _base_state(code_artifacts=arts_full))
    b = _write(tmp_path, "b.json", _base_state(code_artifacts=arts_short))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "extra.py" in result.output
    assert "removed" in result.output.lower()


def test_diff_shows_changed_file_content(tmp_path):
    arts_a = [{"ticket_id": "T-1", "file_path": "auth.py", "content": "def login(): pass\n", "language": "python"}]
    arts_b = [{"ticket_id": "T-1", "file_path": "auth.py", "content": "def login(): return True\n", "language": "python"}]
    a = _write(tmp_path, "a.json", _base_state(code_artifacts=arts_a))
    b = _write(tmp_path, "b.json", _base_state(code_artifacts=arts_b))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "auth.py" in result.output
    assert "modified" in result.output.lower()
    # With --files (default), unified diff is shown
    assert "return True" in result.output


def test_diff_no_files_flag_suppresses_content(tmp_path):
    arts_a = [{"ticket_id": "T-1", "file_path": "auth.py", "content": "def login(): pass\n", "language": "python"}]
    arts_b = [{"ticket_id": "T-1", "file_path": "auth.py", "content": "def login(): return True\n", "language": "python"}]
    a = _write(tmp_path, "a.json", _base_state(code_artifacts=arts_a))
    b = _write(tmp_path, "b.json", _base_state(code_artifacts=arts_b))
    result = runner.invoke(app, ["diff", str(a), str(b), "--no-files"])
    assert result.exit_code == 0
    # File name should appear but unified diff content should not
    assert "auth.py" in result.output
    assert "return True" not in result.output


def test_diff_unchanged_file_shows_equals(tmp_path):
    arts = [{"ticket_id": "T-1", "file_path": "auth.py", "content": "# auth\n", "language": "python"}]
    a = _write(tmp_path, "a.json", _base_state(code_artifacts=arts))
    b = _write(tmp_path, "b.json", _base_state(code_artifacts=arts))
    result = runner.invoke(app, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    # Identical → "No differences found"
    assert "No differences found" in result.output
