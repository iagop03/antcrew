"""EvalRunner — execute EvalCases against a team and collect quality reports."""
from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM

from antcrew.eval.case import AgentScore, EvalCase, EvalReport
from antcrew.eval.metrics import (
    score_code,
    score_prd,
    score_review,
    score_tests,
    score_tickets,
)


class EvalRunner:
    """Run evaluation cases against any AntCrew team and measure output quality.

    All metrics are deterministic (no LLM calls during evaluation).

    Usage:
        from antcrew import DevTeam
        from antcrew.eval import EvalRunner, EvalCase

        runner = EvalRunner(DevTeam(model=llm))

        cases = [
            EvalCase("Build a login module", expect_min_tickets=2, expect_min_code_files=1),
            EvalCase("Add password reset", name="password-reset"),
        ]
        reports = runner.run(cases)
        print(runner.summary(reports))

    Compare two runs (regression detection):
        baseline = runner.run(cases)
        # ... swap model or prompts ...
        current  = runner.run(cases)
        print(runner.compare(baseline, current))

    Export results:
        json_str = runner.to_json(reports)
    """

    def __init__(self, team: Any, *, judge_llm: "Optional[BaseLLM]" = None) -> None:
        self.team = team
        self.judge_llm = judge_llm   # if set, LLM-as-judge runs after structural scoring
        self._history: list[EvalReport] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, cases: list[EvalCase]) -> list[EvalReport]:
        """Run a list of cases sequentially. Appends results to history."""
        reports: list[EvalReport] = []
        for case in cases:
            report = self._run_one(case)
            reports.append(report)
            self._history.append(report)
        return reports

    def run_one(self, case: EvalCase) -> EvalReport:
        """Run a single case. Convenience wrapper around run()."""
        return self.run([case])[0]

    def summary(self, reports: list[EvalReport] | None = None) -> str:
        """Return a human-readable table of results.

        If reports is None, uses the accumulated history from all run() calls.
        """
        target = reports if reports is not None else self._history
        if not target:
            return "No eval results."

        lines = ["AntCrew Eval Summary", "=" * 70]
        for r in target:
            lines.append("")
            lines.append(r.summary())

        avg   = sum(r.overall_score for r in target) / len(target)
        passed = sum(1 for r in target if r.passed)
        lines += [
            "",
            "-" * 70,
            f"Cases: {len(target)}  Passed: {passed}/{len(target)}"
            f"  Avg score: {avg:.2f}"
            f"  Total tokens: {sum(r.token_count for r in target):,}",
        ]
        return "\n".join(lines)

    def compare(
        self,
        baseline: list[EvalReport],
        current: list[EvalReport],
    ) -> str:
        """Diff two sets of reports by case name. Highlights regressions and improvements."""
        b_map = {r.case.name: r for r in baseline}
        lines = ["EvalRunner Comparison (baseline → current)", "=" * 70]

        improved = regressed = unchanged = new = 0

        for r in current:
            b = b_map.get(r.case.name)
            if b is None:
                lines.append(f"  [NEW] {r.case.name}: score={r.overall_score:.2f}")
                new += 1
                continue
            delta = r.overall_score - b.overall_score
            if delta > 0.02:
                arrow = "↑"
                improved += 1
            elif delta < -0.02:
                arrow = "↓"
                regressed += 1
            else:
                arrow = "="
                unchanged += 1
            lines.append(
                f"  [{arrow}] {r.case.name}: "
                f"{b.overall_score:.2f} → {r.overall_score:.2f} ({delta:+.2f})"
            )

        # Per-metric breakdown for regressions
        for r in current:
            b = b_map.get(r.case.name)
            if b is None:
                continue
            for agent, s in r.agent_scores.items():
                b_s = b.agent_scores.get(agent)
                if b_s is None:
                    continue
                for metric, val in s.metrics.items():
                    b_val = b_s.metrics.get(metric, val)
                    if val - b_val < -0.05:
                        lines.append(
                            f"      regression: {agent}.{metric} "
                            f"{b_val:.2f} → {val:.2f}"
                        )

        lines += [
            "",
            f"Improved: {improved}  Regressed: {regressed}"
            f"  Unchanged: {unchanged}  New: {new}",
        ]
        return "\n".join(lines)

    def to_json(self, reports: list[EvalReport] | None = None) -> str:
        """Serialise reports to a JSON string."""
        target = reports if reports is not None else self._history
        return json.dumps([r.to_dict() for r in target], indent=2)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_one(self, case: EvalCase) -> EvalReport:
        thread_id = f"eval-{uuid.uuid4().hex[:8]}"
        errors: list[str] = []
        token_usage: dict = {}

        t0 = time.perf_counter()
        try:
            state = self.team.run(case.request, thread_id=thread_id)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return EvalReport(
                case=case,
                elapsed_ms=elapsed,
                token_usage={},
                agent_scores={},
                overall_score=0.0,
                passed=False,
                errors=[f"Pipeline error: {exc}"],
            )

        elapsed = (time.perf_counter() - t0) * 1000

        # Token usage from the team's LLM
        if hasattr(self.team, "llm") and hasattr(self.team.llm, "get_usage_summary"):
            token_usage = self.team.llm.get_usage_summary()

        # Score each artifact that is present in state
        scores: dict[str, AgentScore] = {}
        _add_score(scores, score_prd(state.get("prd")))
        _add_score(scores, score_tickets(state.get("tickets"), case))
        _add_score(scores, score_code(state.get("code_artifacts"), state.get("tickets"), case))
        if state.get("test_artifacts") is not None:
            _add_score(scores, score_tests(state.get("test_artifacts"), state.get("code_artifacts")))
        if state.get("review") is not None:
            _add_score(scores, score_review(state.get("review"), case))

        # Overall score = unweighted mean across all agent scores
        overall = (
            sum(s.score for s in scores.values()) / len(scores)
            if scores else 0.0
        )

        # Check hard expectations
        _check_expectations(case, state, errors)

        # Optional LLM-as-judge scoring
        judge_results: dict = {}
        if self.judge_llm:
            from antcrew.eval.judge import (
                judge_code,
                judge_prd,
                judge_review,
                judge_tests,
                judge_tickets,
            )
            judge_results["prd"]     = judge_prd(state.get("prd"), case.request, self.judge_llm)
            judge_results["tickets"] = judge_tickets(state.get("tickets"), state.get("prd"), self.judge_llm)
            if state.get("code_artifacts"):
                judge_results["code"] = judge_code(
                    state.get("code_artifacts"), state.get("tickets"), self.judge_llm
                )
            if state.get("test_artifacts"):
                judge_results["tests"] = judge_tests(
                    state.get("test_artifacts"), state.get("code_artifacts"), self.judge_llm
                )
            if state.get("review"):
                judge_results["review"] = judge_review(state.get("review"), self.judge_llm)

        return EvalReport(
            case=case,
            elapsed_ms=elapsed,
            token_usage=token_usage,
            agent_scores=scores,
            overall_score=round(overall, 3),
            passed=len(errors) == 0,
            errors=errors,
            judge_results=judge_results,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_score(scores: dict[str, AgentScore], s: AgentScore) -> None:
    """Merge or add an AgentScore into the accumulator."""
    if s.agent in scores:
        # Two agents write to the same slot (e.g. backend + frontend both → "developer")
        # Merge by averaging metrics
        existing = scores[s.agent]
        merged = {k: (existing.metrics.get(k, v) + v) / 2 for k, v in s.metrics.items()}
        scores[s.agent] = AgentScore(
            agent=s.agent,
            metrics=merged,
            details={**existing.details, **s.details},
        )
    else:
        scores[s.agent] = s


def _check_expectations(
    case: EvalCase,
    state: dict,
    errors: list[str],
) -> None:
    tickets = list(state.get("tickets") or [])
    code    = list(state.get("code_artifacts") or [])
    review  = state.get("review")

    if case.expect_min_tickets > 0 and len(tickets) < case.expect_min_tickets:
        errors.append(
            f"Expected >= {case.expect_min_tickets} tickets, got {len(tickets)}"
        )
    if case.expect_min_code_files > 0 and len(code) < case.expect_min_code_files:
        errors.append(
            f"Expected >= {case.expect_min_code_files} code files, got {len(code)}"
        )
    if case.expect_review_verdict and review is not None:
        verdict = getattr(review, "verdict", "")
        if verdict != case.expect_review_verdict:
            errors.append(
                f"Expected review verdict '{case.expect_review_verdict}', got '{verdict}'"
            )
