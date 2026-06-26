"""Tests for 'antcrew benchmark' CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()

_MODEL = ["--model", "simulated"]


def _write_cases(tmp: Path, cases: list[dict]) -> Path:
    p = tmp / "bench.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    return p


def _invoke(args: list[str]) -> object:
    return runner.invoke(app, ["benchmark"] + args + _MODEL)


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

def test_benchmark_missing_file_exits(tmp_path):
    result = runner.invoke(app, ["benchmark", str(tmp_path / "nope.json")] + _MODEL)
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_benchmark_invalid_json_exits(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json!", encoding="utf-8")
    result = _invoke([str(f)])
    assert result.exit_code == 1
    assert "parse" in result.output.lower() or "failed" in result.output.lower()


def test_benchmark_empty_array_exits(tmp_path):
    f = _write_cases(tmp_path, [])
    result = _invoke([str(f)])
    assert result.exit_code == 1
    assert "array" in result.output.lower() or "cases" in result.output.lower()


def test_benchmark_single_case_runs(tmp_path):
    cases = [{"request": "Build login", "team": "dev", "label": "Login"}]
    f = _write_cases(tmp_path, cases)
    result = _invoke([str(f)])
    assert result.exit_code == 0
    assert "Login" in result.output


def test_benchmark_shows_summary_line(tmp_path):
    cases = [{"request": "Build auth", "team": "dev"}]
    f = _write_cases(tmp_path, cases)
    result = _invoke([str(f)])
    assert result.exit_code == 0
    assert "Total" in result.output or "ok" in result.output


# ---------------------------------------------------------------------------
# Multiple teams
# ---------------------------------------------------------------------------

def test_benchmark_multiple_cases(tmp_path):
    cases = [
        {"request": "Build API",    "team": "dev",      "label": "Dev"},
        {"request": "Research AI",  "team": "research",  "label": "Research"},
    ]
    f = _write_cases(tmp_path, cases)
    result = _invoke([str(f)])
    assert result.exit_code == 0
    assert "Dev" in result.output
    assert "Research" in result.output


def test_benchmark_content_team(tmp_path):
    cases = [{"request": "Blog about AI", "team": "content", "label": "Blog"}]
    f = _write_cases(tmp_path, cases)
    result = _invoke([str(f)])
    assert result.exit_code == 0
    assert "Blog" in result.output


# ---------------------------------------------------------------------------
# Output file
# ---------------------------------------------------------------------------

def test_benchmark_writes_output_json(tmp_path):
    cases = [{"request": "Build auth", "team": "dev"}]
    f = _write_cases(tmp_path, cases)
    out = tmp_path / "results.json"
    result = _invoke([str(f), "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert isinstance(data, list)
    assert len(data) == 1


def test_benchmark_output_contains_expected_fields(tmp_path):
    cases = [{"request": "Build login", "team": "dev", "label": "Test"}]
    f = _write_cases(tmp_path, cases)
    out = tmp_path / "r.json"
    _invoke([str(f), "--output", str(out)])
    data = json.loads(out.read_text())
    row = data[0]
    assert "label" in row
    assert "status" in row
    assert "elapsed_s" in row
    assert "cost_usd" in row
    assert "artifacts" in row


# ---------------------------------------------------------------------------
# Skipped case (empty request)
# ---------------------------------------------------------------------------

def test_benchmark_skips_empty_request(tmp_path):
    cases = [{"request": "", "team": "dev", "label": "Empty"}]
    f = _write_cases(tmp_path, cases)
    out = tmp_path / "r.json"
    _invoke([str(f), "--output", str(out)])
    data = json.loads(out.read_text())
    assert data[0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Unknown team error
# ---------------------------------------------------------------------------

def test_benchmark_unknown_team_marks_error(tmp_path):
    cases = [{"request": "test", "team": "unicorn", "label": "Bad"}]
    f = _write_cases(tmp_path, cases)
    out = tmp_path / "r.json"
    result = _invoke([str(f), "--output", str(out)])
    assert result.exit_code == 0
    data = json.loads(out.read_text())
    assert data[0]["status"] == "error"


# ---------------------------------------------------------------------------
# Parallel flag
# ---------------------------------------------------------------------------

def test_benchmark_parallel_runs_all_cases(tmp_path):
    cases = [
        {"request": "Build A", "team": "dev", "label": "A"},
        {"request": "Build B", "team": "dev", "label": "B"},
    ]
    f = _write_cases(tmp_path, cases)
    out = tmp_path / "r.json"
    result = _invoke([str(f), "--parallel", "2", "--output", str(out)])
    assert result.exit_code == 0
    data = json.loads(out.read_text())
    assert len(data) == 2
    labels = {d["label"] for d in data}
    assert labels == {"A", "B"}


# ---------------------------------------------------------------------------
# Single dict case (not wrapped in array)
# ---------------------------------------------------------------------------

def test_benchmark_accepts_single_dict(tmp_path):
    f = tmp_path / "single.json"
    f.write_text(json.dumps({"request": "Build auth", "team": "dev"}), encoding="utf-8")
    result = _invoke([str(f)])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Artifacts recorded in output
# ---------------------------------------------------------------------------

def test_benchmark_records_artifacts(tmp_path):
    cases = [{"request": "Build login", "team": "dev", "label": "L"}]
    f = _write_cases(tmp_path, cases)
    out = tmp_path / "r.json"
    _invoke([str(f), "--output", str(out)])
    data = json.loads(out.read_text())
    row = data[0]
    if row["status"] == "ok":
        assert isinstance(row["artifacts"], dict)
