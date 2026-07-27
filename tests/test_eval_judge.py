"""Tests for LLM-as-judge evaluation (judge.py)."""
from __future__ import annotations

from antcrew.eval.judge import (
    JudgeResult,
    _parse,
    _truncate,
    judge_code,
    judge_prd,
    judge_review,
    judge_tests,
    judge_tickets,
)
from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _prd():
    from antcrew.core.artifacts import PRD
    return PRD(
        title="Auth Module",
        summary="JWT authentication for the API.",
        goals=["Secure login"],
        out_of_scope=["SSO"],
        functional_requirements=["POST /login", "POST /logout"],
        non_functional_requirements=["<100ms P99"],
        open_questions=[],
    )

def _tickets():
    from antcrew.core.artifacts import Priority, Ticket, TicketStatus
    return [
        Ticket(id="T-1", title="Login endpoint", description="POST /auth/login",
               priority=Priority.HIGH, status=TicketStatus.OPEN,
               acceptance_criteria=["Returns JWT"], dependencies=[]),
        Ticket(id="T-2", title="Logout endpoint", description="POST /auth/logout",
               priority=Priority.MEDIUM, status=TicketStatus.OPEN,
               acceptance_criteria=["Invalidates token"], dependencies=[]),
    ]

def _code():
    from antcrew.core.artifacts import CodeArtifact
    return [
        CodeArtifact(ticket_id="T-1", file_path="src/auth.py", description="Auth handler",
                     language="python",
                     content="def login(email, pw):\n    return jwt.encode({'sub': email})\n"),
    ]

def _tests():
    from antcrew.core.artifacts import TestArtifact
    return [
        TestArtifact(ticket_id="T-1", file_path="tests/test_auth.py",
                     description="Auth tests", language="python",
                     content="def test_login(): assert True",
                     coverage_areas=["unit", "error"]),
    ]

def _review():
    from antcrew.core.artifacts import CodeReview, ReviewFinding
    return CodeReview(
        verdict="approve",
        summary="Good code.",
        findings=[ReviewFinding(severity="info", file_path="src/auth.py",
                               message="Good structure", suggestion="Add docstring")],
    )


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

def test_truncate_short():
    assert _truncate("hello", 100) == "hello"

def test_truncate_long():
    result = _truncate("a" * 100, 10)
    assert len(result) > 10  # includes suffix
    assert "truncated" in result
    assert result.startswith("a" * 10)


# ---------------------------------------------------------------------------
# _parse
# ---------------------------------------------------------------------------

def test_parse_valid_json():
    raw = '{"score": 7, "reasoning": "Good", "strengths": ["clear"], "weaknesses": ["short"]}'
    jr = _parse("prd", raw)
    assert jr.artifact == "prd"
    assert jr.raw_score == 7
    assert abs(jr.score - 0.7) < 0.01
    assert jr.reasoning == "Good"
    assert jr.strengths == ["clear"]
    assert jr.weaknesses == ["short"]

def test_parse_clamps_score_above_10():
    raw = '{"score": 15, "reasoning": "x", "strengths": [], "weaknesses": []}'
    jr = _parse("code", raw)
    assert jr.raw_score == 10
    assert jr.score == 1.0

def test_parse_clamps_score_below_1():
    raw = '{"score": -3, "reasoning": "x", "strengths": [], "weaknesses": []}'
    jr = _parse("code", raw)
    assert jr.raw_score == 1
    assert jr.score == 0.1

def test_parse_invalid_json_returns_fallback():
    jr = _parse("prd", "not valid json {{{")
    assert jr.artifact == "prd"
    assert 0.0 <= jr.score <= 1.0
    assert "weaknesses" in str(jr.weaknesses).lower() or jr.weaknesses

def test_parse_fenced_json():
    raw = '```json\n{"score": 8, "reasoning": "Nice", "strengths": [], "weaknesses": []}\n```'
    jr = _parse("tickets", raw)
    assert jr.raw_score == 8


# ---------------------------------------------------------------------------
# JudgeResult
# ---------------------------------------------------------------------------

def test_judge_result_to_dict():
    jr = JudgeResult(artifact="prd", score=0.8, raw_score=8,
                     reasoning="Good PRD", strengths=["clear"], weaknesses=[])
    d = jr.to_dict()
    assert d["artifact"] == "prd"
    assert d["raw_score"] == 8
    assert d["score"] == 0.8
    assert d["reasoning"] == "Good PRD"


# ---------------------------------------------------------------------------
# Judge functions with SimulatedLLM
# ---------------------------------------------------------------------------

def test_judge_prd_with_prd():
    llm = SimulatedLLM()
    jr = judge_prd(_prd(), "Build auth module", llm)
    assert jr.artifact == "prd"
    assert 0.0 <= jr.score <= 1.0
    assert jr.reasoning

def test_judge_prd_none():
    jr = judge_prd(None, "Build x", SimulatedLLM())
    assert jr.score == 0.0
    assert jr.artifact == "prd"

def test_judge_tickets_with_tickets():
    jr = judge_tickets(_tickets(), _prd(), SimulatedLLM())
    assert jr.artifact == "tickets"
    assert 0.0 <= jr.score <= 1.0

def test_judge_tickets_empty():
    jr = judge_tickets([], _prd(), SimulatedLLM())
    assert jr.score == 0.0

def test_judge_code_with_code():
    jr = judge_code(_code(), _tickets(), SimulatedLLM())
    assert jr.artifact == "code"
    assert 0.0 <= jr.score <= 1.0

def test_judge_code_none():
    jr = judge_code(None, _tickets(), SimulatedLLM())
    assert jr.score == 0.0

def test_judge_tests_with_tests():
    jr = judge_tests(_tests(), _code(), SimulatedLLM())
    assert jr.artifact == "tests"
    assert 0.0 <= jr.score <= 1.0

def test_judge_tests_none():
    jr = judge_tests(None, _code(), SimulatedLLM())
    assert jr.score == 0.0

def test_judge_review_with_review():
    jr = judge_review(_review(), SimulatedLLM())
    assert jr.artifact == "review"
    assert 0.0 <= jr.score <= 1.0

def test_judge_review_none():
    jr = judge_review(None, SimulatedLLM())
    assert jr.score == 0.0


# ---------------------------------------------------------------------------
# EvalRunner with judge_llm
# ---------------------------------------------------------------------------

def test_eval_runner_with_judge_llm():
    from antcrew.eval import EvalCase, EvalRunner
    from antcrew.teams.dev_team import DevTeam

    llm = SimulatedLLM()
    team = DevTeam(model=llm)
    runner = EvalRunner(team, judge_llm=SimulatedLLM())
    report = runner.run_one(EvalCase("Build login", name="judge-test"))

    # Judge results should be populated
    assert len(report.judge_results) >= 2  # at minimum prd + tickets
    assert "prd" in report.judge_results
    assert "tickets" in report.judge_results
    assert all(0.0 <= j.score <= 1.0 for j in report.judge_results.values())
    assert report.judge_score > 0.0

def test_eval_runner_without_judge_llm():
    from antcrew.eval import EvalCase, EvalRunner
    from antcrew.teams.dev_team import DevTeam

    runner = EvalRunner(DevTeam(model=SimulatedLLM()))
    report = runner.run_one(EvalCase("Build feature"))
    assert report.judge_results == {}
    assert report.judge_score == 0.0

def test_eval_report_summary_includes_judge():
    from antcrew.eval import EvalCase, EvalRunner
    from antcrew.teams.dev_team import DevTeam

    runner = EvalRunner(DevTeam(model=SimulatedLLM()), judge_llm=SimulatedLLM())
    report = runner.run_one(EvalCase("Build x", name="judge-summary-test"))
    text = report.summary()
    assert "judge" in text.lower()
    assert "prd" in text

def test_eval_report_to_dict_includes_judge():
    from antcrew.eval import EvalCase, EvalRunner
    from antcrew.teams.dev_team import DevTeam

    runner = EvalRunner(DevTeam(model=SimulatedLLM()), judge_llm=SimulatedLLM())
    report = runner.run_one(EvalCase("Build x"))
    d = report.to_dict()
    assert "judge_results" in d
    assert "judge_score" in d
    assert d["judge_score"] > 0


# ---------------------------------------------------------------------------
# Top-level import
# ---------------------------------------------------------------------------

def test_judge_result_importable_from_antcrew():
    from antcrew import JudgeResult as JR
    assert JR is JudgeResult
