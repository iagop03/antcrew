"""Tests for 'antcrew export' CLI command."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()


def _write(tmp: Path, name: str, data: dict) -> Path:
    p = tmp / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _state_with_artifacts(**overrides) -> dict:
    state: dict = {
        "request": "Build app",
        "messages": [],
        "code_artifacts": [
            {"ticket_id": "T-1", "file_path": "main.py", "content": "# main\n", "language": "python"},
            {"ticket_id": "T-1", "file_path": "utils.py", "content": "# utils\n", "language": "python"},
        ],
        "test_artifacts": [
            {"ticket_id": "T-1", "file_path": "test_main.py", "content": "# test\n", "language": "python"},
        ],
        "devops_artifacts": [
            {"file_path": "Dockerfile", "content": "FROM python:3.12\n"},
        ],
        "doc_artifacts": [
            {"file_path": "README.md", "content": "# Docs\n"},
        ],
        "metadata": {},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

def test_export_creates_zip(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    result = runner.invoke(app, ["export", str(src)])
    assert result.exit_code == 0
    zip_path = tmp_path / "run.zip"
    assert zip_path.exists()


def test_export_default_zip_name_matches_input(tmp_path):
    src = _write(tmp_path, "myrun.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    assert (tmp_path / "myrun.zip").exists()


def test_export_custom_output_path(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    out = tmp_path / "bundle.zip"
    result = runner.invoke(app, ["export", str(src), "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_export_missing_file_exits(tmp_path):
    result = runner.invoke(app, ["export", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Zip contents
# ---------------------------------------------------------------------------

def test_export_contains_code_artifacts(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert "src/main.py" in names
    assert "src/utils.py" in names


def test_export_contains_test_artifacts(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert "tests/test_main.py" in names


def test_export_contains_devops_artifacts(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert "devops/Dockerfile" in names


def test_export_contains_doc_artifacts(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert "docs/README.md" in names


def test_export_contains_state_json_by_default(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert "state.json" in names


def test_export_no_state_flag_omits_state_json(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src), "--no-state"])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert "state.json" not in names


def test_export_content_is_correct(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src)])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        content = zf.read("src/main.py").decode()
    assert content == "# main\n"


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

def test_export_no_tests_flag(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src), "--no-tests"])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert not any(n.startswith("tests/") for n in names)


def test_export_no_devops_flag(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src), "--no-devops"])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert not any(n.startswith("devops/") for n in names)


def test_export_no_docs_flag(tmp_path):
    src = _write(tmp_path, "run.json", _state_with_artifacts())
    runner.invoke(app, ["export", str(src), "--no-docs"])
    with zipfile.ZipFile(tmp_path / "run.zip") as zf:
        names = zf.namelist()
    assert not any(n.startswith("docs/") for n in names)


def test_export_nothing_to_export_exits_cleanly(tmp_path):
    """With no artifacts and --no-state, the command exits cleanly with a message."""
    src = _write(tmp_path, "empty.json", {"request": "hi", "metadata": {}})
    result = runner.invoke(app, ["export", str(src), "--no-state"])
    assert result.exit_code == 0
    assert "nothing to export" in result.output.lower()


def test_export_state_only_when_no_artifacts(tmp_path):
    """With no artifacts but default --state, state.json is still included."""
    src = _write(tmp_path, "empty.json", {"request": "hi", "metadata": {}})
    result = runner.invoke(app, ["export", str(src)])
    assert result.exit_code == 0
    with zipfile.ZipFile(tmp_path / "empty.zip") as zf:
        assert "state.json" in zf.namelist()


# ---------------------------------------------------------------------------
# Project-file nesting (state under "state" key)
# ---------------------------------------------------------------------------

def test_export_handles_nested_state_key(tmp_path):
    project = {"state": _state_with_artifacts(), "run_id": "abc"}
    src = _write(tmp_path, "project.json", project)
    result = runner.invoke(app, ["export", str(src)])
    assert result.exit_code == 0
    with zipfile.ZipFile(tmp_path / "project.zip") as zf:
        assert "src/main.py" in zf.namelist()


# ---------------------------------------------------------------------------
# Research document
# ---------------------------------------------------------------------------

def test_export_includes_research_document(tmp_path):
    state = {
        "request": "Research AI",
        "code_artifacts": None,
        "research_document": {"title": "AI Overview", "body": "# AI\n\nContent here.\n"},
        "metadata": {},
    }
    src = _write(tmp_path, "research.json", state)
    result = runner.invoke(app, ["export", str(src)])
    assert result.exit_code == 0
    with zipfile.ZipFile(tmp_path / "research.zip") as zf:
        names = zf.namelist()
    assert any("AI Overview" in n for n in names)
