"""Tests for EvalFeedbackAgent and ImprovementPlan."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from antcrew.eval.case import EvalCase, EvalReport, AgentScore
from antcrew.eval.feedback import AgentImprovement, ImprovementPlan, EvalFeedbackAgent
from antcrew.testing import SequencedLLM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_report(
    name: str = "Test case",
    passed: bool = True,
    overall_score: float = 0.85,
    agent_scores: dict | None = None,
    errors: list[str] | None = None,
) -> EvalReport:
    case = EvalCase(request="Build auth module", name=name)
    return EvalReport(
        case=case,
        elapsed_ms=1000.0,
        token_usage={},
        agent_scores=agent_scores or {
            "pm": AgentScore("pm", {"ticket_quality": 0.9, "clarity": 0.8}, {}),
            "dev": AgentScore("dev", {"code_quality": 0.85, "test_coverage": 0.8}, {}),
        },
        overall_score=overall_score,
        passed=passed,
        errors=errors or [],
    )


def _plan_json(
    summary: str = "Improve dev agent",
    agents: list[dict] | None = None,
    priority_action: str = "Focus on test coverage",
) -> str:
    return json.dumps({
        "summary": summary,
        "agents": agents or [
            {
                "agent": "dev",
                "current_score": 0.82,
                "weak_metrics": ["test_coverage"],
                "suggestions": ["Add integration tests", "Use fixtures"],
            }
        ],
        "priority_action": priority_action,
    })


# ---------------------------------------------------------------------------
# ImprovementPlan — schema
# ---------------------------------------------------------------------------

def test_improvement_plan_valid():
    plan = ImprovementPlan(
        summary="Need more tests",
        agents=[AgentImprovement(agent="dev", current_score=0.7, weak_metrics=["coverage"], suggestions=["Add tests"])],
        priority_action="Improve test coverage",
    )
    assert plan.summary == "Need more tests"
    assert len(plan.agents) == 1
    assert plan.agents[0].agent == "dev"


def test_agent_improvement_valid():
    imp = AgentImprovement(
        agent="pm",
        current_score=0.65,
        weak_metrics=["clarity", "ticket_count"],
        suggestions=["Write more detailed tickets", "Add acceptance criteria"],
    )
    assert imp.current_score == 0.65
    assert len(imp.weak_metrics) == 2


def test_improvement_plan_empty_agents_is_valid():
    plan = ImprovementPlan(summary="All good", agents=[], priority_action="Keep it up")
    assert plan.agents == []


# ---------------------------------------------------------------------------
# EvalFeedbackAgent._build_report_text
# ---------------------------------------------------------------------------

def test_build_report_text_includes_case_name():
    report = _make_report(name="Auth test")
    text = EvalFeedbackAgent._build_report_text([report])
    assert "Auth test" in text


def test_build_report_text_includes_pass_status():
    report = _make_report(passed=True)
    text = EvalFeedbackAgent._build_report_text([report])
    assert "PASS" in text


def test_build_report_text_includes_fail_status():
    report = _make_report(passed=False)
    text = EvalFeedbackAgent._build_report_text([report])
    assert "FAIL" in text


def test_build_report_text_includes_overall_score():
    report = _make_report(overall_score=0.72)
    text = EvalFeedbackAgent._build_report_text([report])
    assert "0.72" in text


def test_build_report_text_includes_agent_names():
    report = _make_report(agent_scores={
        "pm":  AgentScore("pm",  {"ticket_quality": 0.9}, {}),
        "dev": AgentScore("dev", {"code_quality":   0.5}, {}),
    })
    text = EvalFeedbackAgent._build_report_text([report])
    assert "pm" in text
    assert "dev" in text


def test_build_report_text_includes_metrics():
    report = _make_report(agent_scores={
        "dev": AgentScore("dev", {"test_coverage": 0.60}, {}),
    })
    text = EvalFeedbackAgent._build_report_text([report])
    assert "test_coverage" in text
    assert "0.60" in text


def test_build_report_text_includes_errors():
    report = _make_report(errors=["Timeout after 30s", "No artifacts produced"])
    text = EvalFeedbackAgent._build_report_text([report])
    assert "Timeout after 30s" in text


def test_build_report_text_multiple_reports():
    r1 = _make_report(name="Case 1", passed=True)
    r2 = _make_report(name="Case 2", passed=False)
    text = EvalFeedbackAgent._build_report_text([r1, r2])
    assert "Case 1" in text
    assert "Case 2" in text


def test_build_report_text_empty_reports():
    text = EvalFeedbackAgent._build_report_text([])
    assert text == ""


# ---------------------------------------------------------------------------
# EvalFeedbackAgent.analyse — happy path
# ---------------------------------------------------------------------------

def test_analyse_returns_improvement_plan():
    llm = SequencedLLM([_plan_json()])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([_make_report()])
    assert isinstance(plan, ImprovementPlan)


def test_analyse_plan_has_summary():
    llm = SequencedLLM([_plan_json(summary="Boost dev agent quality")])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([_make_report()])
    assert plan.summary == "Boost dev agent quality"


def test_analyse_plan_has_priority_action():
    llm = SequencedLLM([_plan_json(priority_action="Fix test coverage first")])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([_make_report()])
    assert plan.priority_action == "Fix test coverage first"


def test_analyse_plan_agent_improvements():
    agents_data = [
        {"agent": "pm",  "current_score": 0.6, "weak_metrics": ["clarity"],       "suggestions": ["Be explicit"]},
        {"agent": "dev", "current_score": 0.7, "weak_metrics": ["test_coverage"], "suggestions": ["Add tests"]},
    ]
    llm = SequencedLLM([_plan_json(agents=agents_data)])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([_make_report()])
    assert len(plan.agents) == 2
    assert plan.agents[0].agent == "pm"
    assert plan.agents[1].agent == "dev"


def test_analyse_consumes_llm_call():
    llm = SequencedLLM([_plan_json()])
    agent = EvalFeedbackAgent(llm=llm)
    agent.analyse([_make_report()])
    assert llm.call_count == 1


def test_analyse_empty_reports():
    """analyse() with empty list should still call the LLM and return a plan."""
    llm = SequencedLLM([_plan_json(summary="Nothing to improve", agents=[])])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([])
    assert plan.summary == "Nothing to improve"


def test_analyse_multiple_reports():
    r1 = _make_report(name="Case 1", passed=True)
    r2 = _make_report(name="Case 2", passed=False, overall_score=0.5)
    llm = SequencedLLM([_plan_json()])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([r1, r2])
    assert isinstance(plan, ImprovementPlan)


# ---------------------------------------------------------------------------
# EvalFeedbackAgent — retry on bad JSON
# ---------------------------------------------------------------------------

def test_analyse_retries_on_bad_json():
    """system_parsed retries once on invalid JSON — valid response on second call."""
    good = _plan_json()
    llm = SequencedLLM(["not json at all", good])
    agent = EvalFeedbackAgent(llm=llm)
    plan = agent.analyse([_make_report()])
    assert isinstance(plan, ImprovementPlan)
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# EvalSuite.feedback shortcut
# ---------------------------------------------------------------------------

def test_eval_suite_feedback_shortcut():
    from antcrew.eval.suite import EvalSuite
    suite = EvalSuite.from_requests("test", ["Build something"])
    reports = [_make_report()]
    llm = SequencedLLM([_plan_json()])
    plan = suite.feedback(reports, llm=llm)
    assert isinstance(plan, ImprovementPlan)
