"""Tests for EvalSuite — regression testing collections."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from antcrew.eval.case import AgentScore, EvalCase, EvalReport
from antcrew.eval.suite import EvalSuite


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(name: str, score: float, passed: bool = True) -> EvalReport:
    case = EvalCase(request=f"Build {name}", name=name)
    return EvalReport(
        case=case,
        elapsed_ms=100.0,
        token_usage={"total_input_tokens": 100, "total_output_tokens": 50, "total_cost_usd": 0.001},
        agent_scores={"pm": AgentScore("pm", metrics={"prd": score}, details={})},
        overall_score=score,
        passed=passed,
        errors=[] if passed else ["expectation failed"],
        judge_results={},
    )


# ---------------------------------------------------------------------------
# from_requests
# ---------------------------------------------------------------------------

def test_from_requests_basic():
    suite = EvalSuite.from_requests("test", ["Build auth", "Add RBAC"])
    assert suite.name == "test"
    assert len(suite) == 2
    assert suite.cases[0].name == "test-0"
    assert suite.cases[1].name == "test-1"
    assert suite.cases[0].request == "Build auth"


def test_from_requests_kwargs_forwarded():
    suite = EvalSuite.from_requests(
        "ci", ["Build X"],
        expect_min_code_files=2, expect_min_tickets=1,
    )
    assert suite.cases[0].expect_min_code_files == 2
    assert suite.cases[0].expect_min_tickets == 1


def test_from_requests_empty():
    suite = EvalSuite.from_requests("empty", [])
    assert len(suite) == 0


# ---------------------------------------------------------------------------
# from_eval_reports
# ---------------------------------------------------------------------------

def test_from_eval_reports():
    reports = [_make_report("case-a", 0.8), _make_report("case-b", 0.6)]
    suite = EvalSuite.from_eval_reports("recovered", reports)
    assert suite.name == "recovered"
    assert len(suite) == 2
    assert suite.cases[0].name == "case-a"
    assert suite.cases[1].request == "Build case-b"


# ---------------------------------------------------------------------------
# regression_check
# ---------------------------------------------------------------------------

def test_regression_check_passes_when_no_drop():
    baseline = [_make_report("a", 0.80), _make_report("b", 0.70)]
    current  = [_make_report("a", 0.82), _make_report("b", 0.71)]
    suite = EvalSuite("s", [r.case for r in baseline])
    ok, diff = suite.regression_check(baseline, current)
    assert ok is True
    assert "↑" in diff or "=" in diff


def test_regression_check_fails_when_drop_exceeds_threshold():
    baseline = [_make_report("a", 0.80)]
    current  = [_make_report("a", 0.70)]  # -0.10, above default 0.02
    suite = EvalSuite("s", [r.case for r in baseline])
    ok, diff = suite.regression_check(baseline, current)
    assert ok is False
    assert "↓" in diff


def test_regression_check_custom_threshold():
    baseline = [_make_report("a", 0.80)]
    current  = [_make_report("a", 0.75)]  # -0.05 drop
    suite = EvalSuite("s", [r.case for r in baseline])
    # threshold=0.10 → drop of 0.05 is below threshold → passes
    ok, _ = suite.regression_check(baseline, current, threshold=0.10)
    assert ok is True
    # threshold=0.02 → drop of 0.05 exceeds threshold → fails
    ok2, _ = suite.regression_check(baseline, current, threshold=0.02)
    assert ok2 is False


def test_regression_check_new_case_not_a_regression():
    baseline = [_make_report("a", 0.80)]
    current  = [_make_report("a", 0.80), _make_report("b", 0.50)]
    suite = EvalSuite("s", [r.case for r in baseline])
    ok, diff = suite.regression_check(baseline, current)
    assert ok is True
    assert "NEW" in diff


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def test_compare_returns_string():
    baseline = [_make_report("a", 0.80)]
    current  = [_make_report("a", 0.70)]
    suite = EvalSuite("s", [r.case for r in baseline])
    diff = suite.compare(baseline, current)
    assert isinstance(diff, str)
    assert "a" in diff


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_returns_string():
    suite = EvalSuite.from_requests("s", ["R"])
    reports = [_make_report("r", 0.75)]
    s = suite.summary(reports)
    assert isinstance(s, str)
    assert "0.75" in s or "Passed" in s


# ---------------------------------------------------------------------------
# save / load (suite definition)
# ---------------------------------------------------------------------------

def test_save_load_roundtrip():
    suite = EvalSuite.from_requests(
        "roundtrip",
        ["Build auth", "Add RBAC"],
        expect_min_code_files=1,
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "suite.json"
        suite.save(path)
        loaded = EvalSuite.load(path)

    assert loaded.name == "roundtrip"
    assert len(loaded) == 2
    assert loaded.cases[0].request == "Build auth"
    assert loaded.cases[0].expect_min_code_files == 1


# ---------------------------------------------------------------------------
# save_reports / load_reports
# ---------------------------------------------------------------------------

def test_save_load_reports_roundtrip():
    reports = [
        _make_report("a", 0.8, passed=True),
        _make_report("b", 0.5, passed=False),
    ]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "reports.json"
        suite = EvalSuite("s", [r.case for r in reports])
        suite.save_reports(reports, path)
        loaded = EvalSuite.load_reports(path)

    assert len(loaded) == 2
    assert loaded[0].case.name == "a"
    assert abs(loaded[0].overall_score - 0.8) < 0.001
    assert loaded[0].passed is True
    assert loaded[1].passed is False
    assert loaded[1].errors == ["expectation failed"]


def test_load_reports_preserves_agent_scores():
    ag = AgentScore("pm", metrics={"prd": 0.9, "tickets": 0.7}, details={"n": 3})
    case = EvalCase(request="Build X", name="bx")
    report = EvalReport(
        case=case, elapsed_ms=500, token_usage={},
        agent_scores={"pm": ag}, overall_score=0.8,
        passed=True, errors=[], judge_results={},
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "r.json"
        suite = EvalSuite("s", [case])
        suite.save_reports([report], path)
        loaded = EvalSuite.load_reports(path)

    assert "pm" in loaded[0].agent_scores
    pm = loaded[0].agent_scores["pm"]
    assert abs(pm.metrics["prd"] - 0.9) < 0.001


# ---------------------------------------------------------------------------
# __repr__ and __len__
# ---------------------------------------------------------------------------

def test_len_and_repr():
    suite = EvalSuite.from_requests("s", ["a", "b", "c"])
    assert len(suite) == 3
    r = repr(suite)
    assert "s" in r
    assert "3" in r
