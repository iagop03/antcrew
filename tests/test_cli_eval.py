"""Tests for `antcrew eval cases.json` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_cases(tmp_path: Path, cases) -> Path:
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    return p

def _single_case():
    return {"request": "Build login module", "name": "login"}

def _two_cases():
    return [
        {"request": "Build login module",  "name": "login"},
        {"request": "Add search feature",  "name": "search"},
    ]


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------

def test_eval_single_case(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert result.exit_code == 0, result.output

def test_eval_list_of_cases(tmp_path):
    cases_file = _write_cases(tmp_path, _two_cases())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert result.exit_code == 0, result.output

def test_eval_shows_pass_fail_in_output(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    output = result.output
    # Should contain pass/fail indicator and score
    assert "PASS" in output or "FAIL" in output
    assert "structural" in output.lower() or "0." in output

def test_eval_shows_summary_table(tmp_path):
    cases_file = _write_cases(tmp_path, _two_cases())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    # Table should contain case names
    assert "login" in result.output
    assert "search" in result.output

def test_eval_shows_passed_count(tmp_path):
    cases_file = _write_cases(tmp_path, _two_cases())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert "passed" in result.output.lower()


# ---------------------------------------------------------------------------
# Exit codes — CI integration
# ---------------------------------------------------------------------------

def test_eval_exit_0_when_all_pass(tmp_path):
    # With simulated LLM all structural checks pass
    cases_file = _write_cases(tmp_path, [{"request": "Build login", "name": "x"}])
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert result.exit_code == 0

def test_eval_exit_1_when_expect_not_met(tmp_path):
    # Demand 99 tickets — simulated will never produce that many → FAIL
    cases_file = _write_cases(tmp_path, [
        {"request": "Build login", "name": "x", "expect_min_tickets": 99}
    ])
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# File errors
# ---------------------------------------------------------------------------

def test_eval_missing_file(tmp_path):
    result = runner.invoke(app, ["eval", str(tmp_path / "nope.json"), "--model", "simulated"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()

def test_eval_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    result = runner.invoke(app, ["eval", str(bad), "--model", "simulated"])
    assert result.exit_code == 1
    assert "json" in result.output.lower()

def test_eval_invalid_case_fields(tmp_path):
    # 'request' is required — missing it should error
    cases_file = _write_cases(tmp_path, [{"name": "no-request"}])
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# --verbose flag
# ---------------------------------------------------------------------------

def test_eval_verbose_shows_agent_scores(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    result = runner.invoke(
        app, ["eval", str(cases_file), "--model", "simulated", "--verbose"]
    )
    assert result.exit_code == 0
    # Verbose prints the summary which includes agent names
    assert "business_analyst" in result.output or "pm" in result.output or "structural" in result.output.lower()


# ---------------------------------------------------------------------------
# --output flag
# ---------------------------------------------------------------------------

def test_eval_output_saves_json(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    out_file   = tmp_path / "results.json"
    result = runner.invoke(
        app, ["eval", str(cases_file), "--model", "simulated", "--output", str(out_file)]
    )
    assert result.exit_code == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert isinstance(data, list)
    assert len(data) == 1
    assert "overall_score" in data[0]

def test_eval_output_contains_all_cases(tmp_path):
    cases_file = _write_cases(tmp_path, _two_cases())
    out_file   = tmp_path / "results.json"
    runner.invoke(
        app, ["eval", str(cases_file), "--model", "simulated", "--output", str(out_file)]
    )
    data = json.loads(out_file.read_text())
    assert len(data) == 2


# ---------------------------------------------------------------------------
# --fail-fast flag
# ---------------------------------------------------------------------------

def test_eval_fail_fast_stops_early(tmp_path):
    cases_file = _write_cases(tmp_path, [
        {"request": "Build login", "name": "fail", "expect_min_tickets": 999},
        {"request": "Build search", "name": "should-not-run"},
    ])
    result = runner.invoke(
        app, ["eval", str(cases_file), "--model", "simulated", "--fail-fast"]
    )
    assert result.exit_code == 1
    assert "should-not-run" not in result.output


# ---------------------------------------------------------------------------
# --judge-model flag (simulated judge)
# ---------------------------------------------------------------------------

def test_eval_with_judge_model(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    result = runner.invoke(
        app,
        ["eval", str(cases_file), "--model", "simulated", "--judge-model", "simulated"],
    )
    assert result.exit_code == 0
    # Judge scores should appear
    assert "judge" in result.output.lower()

def test_eval_judge_score_not_dash_when_judge_enabled(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    result = runner.invoke(
        app,
        ["eval", str(cases_file), "--model", "simulated", "--judge-model", "simulated"],
    )
    # Without judge, the column shows "—". With judge it shows a number.
    lines = result.output.splitlines()
    # Find the summary table line that contains the case name
    case_lines = [line for line in lines if "login" in line.lower()]
    assert case_lines, f"Expected 'login' in output:\n{result.output}"
    # The judge column should not be "—"
    assert "—" not in case_lines[-1] or "0." in case_lines[-1]


# ---------------------------------------------------------------------------
# Team variants
# ---------------------------------------------------------------------------

def test_eval_research_team(tmp_path):
    cases_file = _write_cases(tmp_path, [{"request": "Risks of agentic AI", "name": "risk"}])
    result = runner.invoke(
        app, ["eval", str(cases_file), "--model", "simulated", "--team", "research"]
    )
    # Research team may not produce tickets/code — won't crash
    assert result.exit_code in (0, 1)  # may fail due to structural expectations

def test_eval_content_team(tmp_path):
    cases_file = _write_cases(tmp_path, [{"request": "Write about AI", "name": "blog"}])
    result = runner.invoke(
        app, ["eval", str(cases_file), "--model", "simulated", "--team", "content"]
    )
    assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Header / metadata in output
# ---------------------------------------------------------------------------

def test_eval_output_shows_team_and_model(tmp_path):
    cases_file = _write_cases(tmp_path, _single_case())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert "simulated" in result.output
    assert "dev" in result.output   # default team

def test_eval_output_shows_case_count(tmp_path):
    cases_file = _write_cases(tmp_path, _two_cases())
    result = runner.invoke(app, ["eval", str(cases_file), "--model", "simulated"])
    assert "2" in result.output
