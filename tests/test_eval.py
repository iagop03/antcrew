"""Tests for the agent evaluation system (EvalCase, metrics, EvalRunner)."""
from __future__ import annotations

import json

import pytest

from antcrew.eval.case import AgentScore, EvalCase, EvalReport
from antcrew.eval.metrics import (
    score_code,
    score_prd,
    score_review,
    score_tests,
    score_tickets,
)
from antcrew.eval.runner import EvalRunner
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_prd(**kwargs):
    from antcrew.core.artifacts import PRD
    defaults = dict(
        title="Login Module",
        summary="Implements secure user authentication with JWT.",
        goals=["Secure auth", "Fast login"],
        out_of_scope=["OAuth SSO"],
        functional_requirements=["Login endpoint", "Logout endpoint", "Token refresh"],
        non_functional_requirements=["< 100ms P99"],
        open_questions=[],
    )
    return PRD(**{**defaults, **kwargs})

def _make_ticket(tid="T-001", **kwargs):
    from antcrew.core.artifacts import Priority, Ticket, TicketStatus
    defaults = dict(
        id=tid,
        title="Implement login endpoint",
        description="POST /auth/login returns JWT on valid credentials.",
        priority=Priority.HIGH,
        status=TicketStatus.OPEN,
        acceptance_criteria=["Returns 200 on valid creds", "Returns 401 on bad creds"],
        dependencies=[],
    )
    return Ticket(**{**defaults, **kwargs})

def _make_code(ticket_id="T-001", **kwargs):
    from antcrew.core.artifacts import CodeArtifact
    defaults = dict(
        ticket_id=ticket_id,
        file_path="src/auth/login.py",
        description="Login handler",
        language="python",
        content="def login(email, password):\n    return verify(email, password)\n",
    )
    return CodeArtifact(**{**defaults, **kwargs})

def _make_test(**kwargs):
    from antcrew.core.artifacts import TestArtifact
    defaults = dict(
        ticket_id="T-001",
        file_path="tests/test_login.py",
        description="Tests for login handler",
        language="python",
        content="def test_login(): assert True",
        coverage_areas=["unit", "error"],
    )
    return TestArtifact(**{**defaults, **kwargs})

def _make_review(verdict="approve", n_findings=1, **kwargs):
    from antcrew.core.artifacts import CodeReview, ReviewFinding
    findings = [
        ReviewFinding(
            severity="info",
            file_path="src/auth/login.py",
            message="Good structure",
            suggestion="Add docstring",
        )
        for _ in range(n_findings)
    ]
    return CodeReview(verdict=verdict, summary="Looks good.", findings=findings)


# ---------------------------------------------------------------------------
# EvalCase
# ---------------------------------------------------------------------------

def test_eval_case_defaults():
    c = EvalCase("Build a login module")
    assert c.name == "Build a login module"
    assert c.tags == []
    assert c.expect_min_tickets == 0

def test_eval_case_name_auto_truncated():
    long_req = "x" * 100
    c = EvalCase(long_req)
    assert len(c.name) <= 60

def test_eval_case_explicit_name():
    c = EvalCase("request text", name="my-case")
    assert c.name == "my-case"


# ---------------------------------------------------------------------------
# AgentScore
# ---------------------------------------------------------------------------

def test_agent_score_mean():
    s = AgentScore("pm", metrics={"a": 1.0, "b": 0.5, "c": 0.0}, details={})
    assert abs(s.score - 0.5) < 1e-9

def test_agent_score_empty_metrics():
    s = AgentScore("pm", metrics={}, details={})
    assert s.score == 0.0


# ---------------------------------------------------------------------------
# Metrics — score_prd
# ---------------------------------------------------------------------------

def test_score_prd_none():
    s = score_prd(None)
    assert s.agent == "business_analyst"
    assert s.metrics["present"] == 0.0

def test_score_prd_full():
    prd = _make_prd()
    s = score_prd(prd)
    assert s.metrics["present"] == 1.0
    assert s.metrics["has_goals"] == 1.0
    assert s.metrics["has_func_reqs"] == 1.0
    assert s.metrics["has_nfr"] == 1.0
    assert s.metrics["has_out_of_scope"] == 1.0
    assert s.score > 0.8

def test_score_prd_missing_goals():
    prd = _make_prd(goals=[])
    s = score_prd(prd)
    assert s.metrics["has_goals"] == 0.0

def test_score_prd_req_depth_capped():
    prd = _make_prd(functional_requirements=["r"] * 10)
    s = score_prd(prd)
    assert s.metrics["req_depth"] == 1.0

def test_score_prd_req_depth_partial():
    prd = _make_prd(functional_requirements=["r"] * 2)  # 2/5 = 0.4
    s = score_prd(prd)
    assert abs(s.metrics["req_depth"] - 0.4) < 1e-6


# ---------------------------------------------------------------------------
# Metrics — score_tickets
# ---------------------------------------------------------------------------

def test_score_tickets_none():
    s = score_tickets(None, EvalCase("x"))
    assert s.metrics["produced"] == 0.0

def test_score_tickets_full():
    tickets = [_make_ticket("T-001"), _make_ticket("T-002")]
    s = score_tickets(tickets, EvalCase("x"))
    assert s.metrics["produced"] == 1.0
    assert s.metrics["all_have_criteria"] == 1.0
    assert s.metrics["all_have_desc"] == 1.0

def test_score_tickets_meets_count():
    tickets = [_make_ticket()]
    s = score_tickets(tickets, EvalCase("x", expect_min_tickets=1))
    assert s.metrics["meets_count"] == 1.0

def test_score_tickets_fails_count():
    tickets = [_make_ticket()]
    s = score_tickets(tickets, EvalCase("x", expect_min_tickets=5))
    assert s.metrics["meets_count"] < 1.0

def test_score_tickets_missing_criteria():
    from antcrew.core.artifacts import Priority, Ticket, TicketStatus
    t = Ticket(id="T-1", title="x", description="desc with enough characters here",
               priority=Priority.LOW, status=TicketStatus.OPEN,
               acceptance_criteria=[], dependencies=[])
    s = score_tickets([t], EvalCase("x"))
    assert s.metrics["all_have_criteria"] == 0.0


# ---------------------------------------------------------------------------
# Metrics — score_code
# ---------------------------------------------------------------------------

def test_score_code_none():
    s = score_code(None, None, EvalCase("x"))
    assert s.metrics["produced"] == 0.0

def test_score_code_full_coverage():
    tickets = [_make_ticket("T-001"), _make_ticket("T-002")]
    code = [_make_code("T-001"), _make_code("T-002")]
    s = score_code(code, tickets, EvalCase("x"))
    assert s.metrics["produced"] == 1.0
    assert s.metrics["ticket_coverage"] == 1.0

def test_score_code_partial_coverage():
    tickets = [_make_ticket("T-001"), _make_ticket("T-002")]
    code = [_make_code("T-001")]
    s = score_code(code, tickets, EvalCase("x"))
    assert s.metrics["ticket_coverage"] == 0.5

def test_score_code_meets_min_files():
    code = [_make_code(), _make_code()]
    s = score_code(code, [], EvalCase("x", expect_min_code_files=2))
    assert s.metrics["meets_count"] == 1.0

def test_score_code_fails_min_files():
    code = [_make_code()]
    s = score_code(code, [], EvalCase("x", expect_min_code_files=10))
    assert s.metrics["meets_count"] < 1.0


# ---------------------------------------------------------------------------
# Metrics — score_tests
# ---------------------------------------------------------------------------

def test_score_tests_none():
    s = score_tests(None, None)
    assert s.metrics["produced"] == 0.0

def test_score_tests_with_edge_coverage():
    tests = [_make_test(coverage_areas=["unit", "error", "edge"])]
    s = score_tests(tests, [_make_code()])
    assert s.metrics["produced"] == 1.0
    assert s.metrics["edge_coverage"] == 1.0

def test_score_tests_no_edge_coverage():
    tests = [_make_test(coverage_areas=["unit"])]
    s = score_tests(tests, [_make_code()])
    assert s.metrics["edge_coverage"] == 0.5  # no edge signal = partial credit

def test_score_tests_coverage_ratio():
    code = [_make_code() for _ in range(3)]
    tests = [_make_test() for _ in range(2)]
    s = score_tests(tests, code)
    assert abs(s.metrics["coverage_ratio"] - 2 / 3) < 0.01


# ---------------------------------------------------------------------------
# Metrics — score_review
# ---------------------------------------------------------------------------

def test_score_review_none():
    s = score_review(None, EvalCase("x"))
    assert s.metrics["produced"] == 0.0

def test_score_review_approve():
    review = _make_review("approve")
    s = score_review(review, EvalCase("x"))
    assert s.metrics["produced"] == 1.0
    assert s.metrics["actionability"] == 1.0
    assert s.metrics["specificity"] == 1.0

def test_score_review_verdict_match():
    review = _make_review("approve")
    s = score_review(review, EvalCase("x", expect_review_verdict="approve"))
    assert s.metrics["verdict_match"] == 1.0

def test_score_review_verdict_mismatch():
    review = _make_review("approve")
    s = score_review(review, EvalCase("x", expect_review_verdict="request_changes"))
    assert s.metrics["verdict_match"] == 0.0

def test_score_review_missing_suggestions():
    from antcrew.core.artifacts import CodeReview, ReviewFinding
    review = CodeReview(
        verdict="request_changes",
        summary="Needs work",
        findings=[
            ReviewFinding(severity="warning", file_path="a.py", message="issue", suggestion=None)
        ],
    )
    s = score_review(review, EvalCase("x"))
    assert s.metrics["actionability"] == 0.0


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------

def test_eval_report_token_count():
    case = EvalCase("x")
    usage = {"total_input_tokens": 500, "total_output_tokens": 200, "total_cost_usd": 0.001}
    report = EvalReport(
        case=case, elapsed_ms=1000, token_usage=usage,
        agent_scores={}, overall_score=0.8, passed=True, errors=[],
    )
    assert report.token_count == 700
    assert report.cost_usd == 0.001

def test_eval_report_summary_contains_score():
    case = EvalCase("test request", name="test-case")
    report = EvalReport(
        case=case, elapsed_ms=500, token_usage={},
        agent_scores={"pm": AgentScore("pm", {"a": 1.0}, {})},
        overall_score=0.75, passed=True, errors=[],
    )
    text = report.summary()
    assert "test-case" in text
    assert "0.75" in text
    assert "PASS" in text

def test_eval_report_to_dict():
    case = EvalCase("x", name="my-eval")
    report = EvalReport(
        case=case, elapsed_ms=100, token_usage={"total_cost_usd": 0.0},
        agent_scores={}, overall_score=0.5, passed=False, errors=["oops"],
    )
    d = report.to_dict()
    assert d["case"]["name"] == "my-eval"
    assert d["overall_score"] == 0.5
    assert d["passed"] is False
    assert "oops" in d["errors"]


# ---------------------------------------------------------------------------
# EvalRunner — unit
# ---------------------------------------------------------------------------

def test_eval_runner_run_one_returns_report():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    report = runner.run_one(EvalCase("Build login", name="login-test"))
    assert isinstance(report, EvalReport)
    assert report.overall_score > 0.0

def test_eval_runner_run_batch():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    cases = [EvalCase(f"Feature {i}", name=f"f{i}") for i in range(3)]
    reports = runner.run(cases)
    assert len(reports) == 3
    assert all(isinstance(r, EvalReport) for r in reports)

def test_eval_runner_accumulates_history():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    runner.run([EvalCase("Case A")])
    runner.run([EvalCase("Case B")])
    assert len(runner._history) == 2

def test_eval_runner_summary_no_results():
    from antcrew.teams.dev_team import DevTeam
    runner = EvalRunner(DevTeam(model=SimulatedLLM()))
    assert "No eval results" in runner.summary()

def test_eval_runner_summary_with_results():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    report = runner.run_one(EvalCase("Build login", name="test"))
    text = runner.summary([report])
    assert "AntCrew Eval Summary" in text
    assert "test" in text

def test_eval_runner_to_json():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    report = runner.run_one(EvalCase("Build x", name="x"))
    j = runner.to_json([report])
    data = json.loads(j)
    assert isinstance(data, list)
    assert data[0]["case"]["name"] == "x"


# ---------------------------------------------------------------------------
# EvalRunner — expectations
# ---------------------------------------------------------------------------

def test_eval_runner_passes_with_no_expectations():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    report = runner.run_one(EvalCase("Build feature"))
    # SimulatedLLM always produces valid artifacts → should pass
    assert report.passed is True

def test_eval_runner_fails_on_min_tickets():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    # SimulatedLLM produces 2 tickets; require 100 to force failure
    report = runner.run_one(EvalCase("Build feature", expect_min_tickets=100))
    assert report.passed is False
    assert any("ticket" in e.lower() for e in report.errors)

def test_eval_runner_fails_on_min_code_files():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    report = runner.run_one(EvalCase("Build feature", expect_min_code_files=50))
    assert report.passed is False
    assert any("code" in e.lower() for e in report.errors)


# ---------------------------------------------------------------------------
# EvalRunner — compare
# ---------------------------------------------------------------------------

def test_eval_runner_compare():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    cases = [EvalCase("Build login", name="login")]
    baseline = runner.run(cases)
    current = runner.run(cases)
    text = runner.compare(baseline, current)
    assert "EvalRunner Comparison" in text
    assert "login" in text

def test_eval_runner_compare_new_case():
    from antcrew.teams.dev_team import DevTeam
    team = DevTeam(model=SimulatedLLM())
    runner = EvalRunner(team)
    baseline = runner.run([EvalCase("Old case", name="old")])
    current  = runner.run([EvalCase("New case", name="new")])
    text = runner.compare(baseline, current)
    assert "[NEW]" in text
    assert "new" in text


# ---------------------------------------------------------------------------
# Top-level antcrew import
# ---------------------------------------------------------------------------

def test_eval_importable_from_antcrew():
    from antcrew import EvalCase as EC, EvalRunner as ER, EvalReport as ERep, AgentScore as AS
    assert EC is EvalCase
    assert ER is EvalRunner
