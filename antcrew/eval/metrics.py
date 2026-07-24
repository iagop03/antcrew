"""Deterministic (no-LLM) scoring functions for each pipeline artifact type.

Every function returns an AgentScore with metrics normalised to [0.0, 1.0].
A score of 1.0 means the artifact fully met the structural quality bar.
"""
from __future__ import annotations

from typing import Any

from antcrew.eval.case import AgentScore, EvalCase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ratio(count: int, denominator: int) -> float:
    return min(1.0, count / denominator) if denominator > 0 else 0.0


# ---------------------------------------------------------------------------
# PRD — BusinessAnalystAgent output
# ---------------------------------------------------------------------------

def score_prd(prd: Any) -> AgentScore:
    """Score a PRD for structural completeness."""
    if prd is None:
        return AgentScore(
            agent="business_analyst",
            metrics={"present": 0.0},
            details={"present": False},
        )

    goals = getattr(prd, "goals", []) or []
    func  = getattr(prd, "functional_requirements", []) or []
    nfr   = getattr(prd, "non_functional_requirements", []) or []
    oos   = getattr(prd, "out_of_scope", []) or []
    title = getattr(prd, "title", "") or ""
    summ  = getattr(prd, "summary", "") or ""

    return AgentScore(
        agent="business_analyst",
        metrics={
            "present":          1.0,
            "has_title":        1.0 if len(title) >= 5 else 0.0,
            "has_summary":      1.0 if len(summ) >= 20 else 0.0,
            "has_goals":        1.0 if goals else 0.0,
            "has_func_reqs":    1.0 if func else 0.0,
            "has_nfr":          1.0 if nfr else 0.0,
            "has_out_of_scope": 1.0 if oos else 0.0,
            "req_depth":        _ratio(len(func), 5),   # 5+ reqs → 1.0
        },
        details={
            "title":           title,
            "goal_count":      len(goals),
            "func_req_count":  len(func),
            "nfr_count":       len(nfr),
        },
    )


# ---------------------------------------------------------------------------
# Tickets — PMAgent output
# ---------------------------------------------------------------------------

def score_tickets(tickets: Any, case: EvalCase) -> AgentScore:
    """Score ticket quality and expectation coverage."""
    if not tickets:
        return AgentScore(
            agent="pm",
            metrics={"produced": 0.0},
            details={"ticket_count": 0},
        )

    t_list = list(tickets)
    n = len(t_list)

    criteria_pct = sum(
        1 for t in t_list if getattr(t, "acceptance_criteria", [])
    ) / n

    desc_pct = sum(
        1 for t in t_list
        if len(getattr(t, "description", "") or "") >= 20
    ) / n

    avg_criteria = sum(
        len(getattr(t, "acceptance_criteria", []) or []) for t in t_list
    ) / n

    min_expected = case.expect_min_tickets
    meets_count = 1.0 if (min_expected == 0 or n >= min_expected) else _ratio(n, min_expected)

    return AgentScore(
        agent="pm",
        metrics={
            "produced":           1.0,
            "all_have_criteria":  criteria_pct,
            "all_have_desc":      desc_pct,
            "criteria_depth":     _ratio(round(avg_criteria), 3),  # 3+ criteria → 1.0
            "meets_count":        meets_count,
        },
        details={
            "ticket_count":    n,
            "with_criteria":   round(criteria_pct * n),
            "avg_criteria":    round(avg_criteria, 1),
            "expected_min":    min_expected,
        },
    )


# ---------------------------------------------------------------------------
# Code artifacts — BackendDevAgent / FrontendDevAgent output
# ---------------------------------------------------------------------------

def score_code(
    code_artifacts: Any,
    tickets: Any,
    case: EvalCase,
) -> AgentScore:
    """Score code artifact coverage and substance."""
    if not code_artifacts:
        return AgentScore(
            agent="developer",
            metrics={"produced": 0.0},
            details={"file_count": 0},
        )

    arts = list(code_artifacts)
    n = len(arts)
    ticket_ids = {getattr(t, "id", "") for t in (tickets or [])}
    covered_ids = {getattr(a, "ticket_id", "") for a in arts}
    coverage = _ratio(len(covered_ids & ticket_ids), len(ticket_ids)) if ticket_ids else 1.0

    non_empty = sum(1 for a in arts if len(getattr(a, "content", "") or "") >= 10)

    min_expected = case.expect_min_code_files
    meets_count = 1.0 if (min_expected == 0 or n >= min_expected) else _ratio(n, min_expected)

    # Average content length as a proxy for substance (capped at 200 lines)
    avg_lines = sum(
        len((getattr(a, "content", "") or "").splitlines()) for a in arts
    ) / n

    return AgentScore(
        agent="developer",
        metrics={
            "produced":        1.0,
            "ticket_coverage": coverage,
            "non_empty":       _ratio(non_empty, n),
            "meets_count":     meets_count,
            "substance":       _ratio(round(avg_lines), 20),  # 20+ lines avg → 1.0
        },
        details={
            "file_count":      n,
            "tickets_covered": len(covered_ids & ticket_ids),
            "tickets_total":   len(ticket_ids),
            "avg_lines":       round(avg_lines, 1),
        },
    )


# ---------------------------------------------------------------------------
# Test artifacts — QAAgent output
# ---------------------------------------------------------------------------

def score_tests(
    test_artifacts: Any,
    code_artifacts: Any,
) -> AgentScore:
    """Score test suite coverage and quality signals."""
    if not test_artifacts:
        return AgentScore(
            agent="qa",
            metrics={"produced": 0.0},
            details={"test_file_count": 0},
        )

    tests = list(test_artifacts)
    code_n = len(list(code_artifacts)) if code_artifacts else 1
    n = len(tests)

    has_coverage_areas = sum(
        1 for t in tests if getattr(t, "coverage_areas", [])
    ) / n

    # Look for edge-case / error coverage in coverage_areas strings
    edge_signal = any(
        any(kw in area.lower() for kw in ("edge", "error", "invalid", "fail", "exception"))
        for t in tests
        for area in (getattr(t, "coverage_areas", []) or [])
    )

    return AgentScore(
        agent="qa",
        metrics={
            "produced":          1.0,
            "coverage_ratio":    _ratio(n, code_n),          # 1 test file per code file → 1.0
            "has_areas":         has_coverage_areas,
            "edge_coverage":     1.0 if edge_signal else 0.5, # 0.5 = no signal (not penalised heavily)
        },
        details={
            "test_file_count": n,
            "code_file_count": code_n,
            "edge_signal":     edge_signal,
        },
    )


# ---------------------------------------------------------------------------
# Code review — ReviewerAgent output
# ---------------------------------------------------------------------------

def score_review(review: Any, case: EvalCase) -> AgentScore:
    """Score review quality and expectation match."""
    if review is None:
        return AgentScore(
            agent="reviewer",
            metrics={"produced": 0.0},
            details={"verdict": None},
        )

    verdict  = getattr(review, "verdict", "") or ""
    findings = list(getattr(review, "findings", []) or [])
    n = len(findings)

    actionability = (
        sum(1 for f in findings if getattr(f, "suggestion", None)) / n
        if n else 1.0
    )
    specificity = (
        sum(1 for f in findings if getattr(f, "file_path", None)) / n
        if n else 1.0
    )
    has_info = any(getattr(f, "severity", "") == "info" for f in findings)

    expected_verdict = case.expect_review_verdict
    verdict_match = (
        1.0 if not expected_verdict or verdict == expected_verdict else 0.0
    )

    return AgentScore(
        agent="reviewer",
        metrics={
            "produced":      1.0,
            "actionability": actionability,
            "specificity":   specificity,
            "has_info":      1.0 if has_info else 0.5,
            "verdict_match": verdict_match,
        },
        details={
            "verdict":          verdict,
            "finding_count":    n,
            "expected_verdict": expected_verdict,
        },
    )
