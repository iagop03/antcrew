"""EvalSuite — reusable, named collections of EvalCases for regression testing.

Usage::

    from antcrew.eval import EvalCase, EvalSuite

    suite = EvalSuite.from_requests(
        "auth-flows",
        [
            "Build a login page with JWT auth",
            "Add password reset via email token",
            "Add OAuth2 Google login",
        ],
        expect_min_code_files=1,
    )

    # First run — save as baseline
    baseline = suite.run(team)
    suite.save_reports(baseline, "baseline.json")

    # After refactoring — check for regressions
    current = suite.run(team)
    ok, report = suite.regression_check(baseline, current)
    if not ok:
        print(report)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from antcrew.eval.case import EvalCase, EvalReport
from antcrew.eval.runner import EvalRunner


class EvalSuite:
    """Named collection of :class:`~antcrew.eval.EvalCase` instances.

    Provides a stable interface for:
    - Running the full suite against any team.
    - Saving / loading reports as JSON for baseline comparison.
    - Automated regression checks with a configurable score threshold.

    Args:
        name:  Human-readable suite identifier (used in reports and filenames).
        cases: List of :class:`~antcrew.eval.EvalCase` instances.
    """

    def __init__(self, name: str, cases: list[EvalCase]) -> None:
        self.name = name
        self.cases = list(cases)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_requests(
        cls,
        name: str,
        requests: list[str],
        **case_kwargs: Any,
    ) -> "EvalSuite":
        """Build a suite from a plain list of request strings.

        *case_kwargs* is forwarded to every :class:`~antcrew.eval.EvalCase`::

            suite = EvalSuite.from_requests(
                "sprint-1",
                ["Build auth", "Add RBAC"],
                expect_min_code_files=1,
            )
        """
        cases = [
            EvalCase(request=req, name=f"{name}-{i}", **case_kwargs)
            for i, req in enumerate(requests)
        ]
        return cls(name, cases)

    @classmethod
    def from_eval_reports(cls, name: str, reports: list[EvalReport]) -> "EvalSuite":
        """Reconstruct an EvalSuite from a previously saved list of EvalReport objects.

        Useful for re-running exactly the same cases that produced a baseline::

            baseline_reports = EvalSuite.load_reports("baseline.json")
            suite = EvalSuite.from_eval_reports("auth-flows", baseline_reports)
            current = suite.run(new_team)
        """
        cases = [r.case for r in reports]
        return cls(name, cases)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    def run(
        self,
        team: Any,
        *,
        judge_llm: Optional[Any] = None,
    ) -> list[EvalReport]:
        """Execute all cases against *team* and return reports.

        Args:
            team:      Any AntCrew team (DevTeam, ResearchTeam, CustomTeam, …).
            judge_llm: Optional LLM for LLM-as-judge scoring.
        """
        runner = EvalRunner(team, judge_llm=judge_llm)
        return runner.run(self.cases)

    def summary(self, reports: list[EvalReport]) -> str:
        """Return a human-readable summary table for *reports*."""
        runner = EvalRunner(None)  # type: ignore[arg-type]
        runner._history = reports
        return runner.summary(reports)

    # ------------------------------------------------------------------
    # Regression checking
    # ------------------------------------------------------------------

    def regression_check(
        self,
        baseline: list[EvalReport],
        current: list[EvalReport],
        *,
        threshold: float = 0.02,
    ) -> tuple[bool, str]:
        """Compare *current* against *baseline* and detect regressions.

        A regression is any case whose overall_score dropped by more than
        *threshold* compared to baseline.

        Returns:
            ``(passed, report_str)`` — ``passed`` is True when no regressions
            are found, False otherwise.  ``report_str`` is a human-readable diff.
        """
        runner = EvalRunner(None)  # type: ignore[arg-type]
        diff = runner.compare(baseline, current)

        b_map = {r.case.name: r for r in baseline}
        regressions = [
            r for r in current
            if r.case.name in b_map
            and (b_map[r.case.name].overall_score - r.overall_score) > threshold
        ]
        passed = len(regressions) == 0
        return passed, diff

    def compare(
        self,
        baseline: list[EvalReport],
        current: list[EvalReport],
    ) -> str:
        """Return a human-readable diff string (delegates to EvalRunner.compare)."""
        return EvalRunner(None).compare(baseline, current)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_reports(self, reports: list[EvalReport], path: str | Path) -> None:
        """Serialize *reports* to a JSON file.

        The file can be reloaded later with :meth:`load_reports`::

            suite.save_reports(reports, "baselines/sprint-1.json")
        """
        Path(path).write_text(
            json.dumps([r.to_dict() for r in reports], indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def load_reports(path: str | Path) -> list[EvalReport]:
        """Load reports previously saved with :meth:`save_reports`.

        Returns a list of :class:`~antcrew.eval.EvalReport` instances with the
        case + scores recovered from JSON. Judge results and token details are
        not recoverable (they are returned as empty dicts).
        """
        from antcrew.eval.case import AgentScore
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        reports: list[EvalReport] = []
        for d in raw:
            case_d = d.get("case", {})
            case = EvalCase(
                request=case_d.get("request", ""),
                name=case_d.get("name", ""),
                tags=list(case_d.get("tags") or []),
            )
            agent_scores = {
                agent: AgentScore(
                    agent=agent,
                    metrics=s.get("metrics", {}),
                    details=s.get("details", {}),
                )
                for agent, s in (d.get("agent_scores") or {}).items()
            }
            reports.append(
                EvalReport(
                    case=case,
                    elapsed_ms=float(d.get("elapsed_ms", 0)),
                    token_usage={},
                    agent_scores=agent_scores,
                    overall_score=float(d.get("overall_score", 0.0)),
                    passed=bool(d.get("passed", False)),
                    errors=list(d.get("errors") or []),
                    judge_results={},
                )
            )
        return reports

    def save(self, path: str | Path) -> None:
        """Serialize the suite definition (cases) to a JSON file.

        Does NOT save reports — use :meth:`save_reports` for that.
        """
        data = {
            "name": self.name,
            "cases": [
                {
                    "request": c.request,
                    "name": c.name,
                    "expect_min_tickets": c.expect_min_tickets,
                    "expect_min_code_files": c.expect_min_code_files,
                    "expect_review_verdict": c.expect_review_verdict,
                    "tags": list(c.tags),
                }
                for c in self.cases
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "EvalSuite":
        """Load a suite from a JSON file saved with :meth:`save`."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cases = [
            EvalCase(
                request=c["request"],
                name=c.get("name", ""),
                expect_min_tickets=c.get("expect_min_tickets", 0),
                expect_min_code_files=c.get("expect_min_code_files", 0),
                expect_review_verdict=c.get("expect_review_verdict", ""),
                tags=list(c.get("tags") or []),
            )
            for c in data.get("cases", [])
        ]
        return cls(data.get("name", "unnamed"), cases)

    def feedback(
        self,
        reports: list[EvalReport],
        llm: Any,
    ) -> "ImprovementPlan":
        """Analyse *reports* with an LLM and return a structured improvement plan.

        Shorthand for::

            from antcrew.eval import EvalFeedbackAgent
            plan = EvalFeedbackAgent(llm=llm).analyse(reports)

        Args:
            reports: Eval reports produced by :meth:`run`.
            llm:     Any :class:`~antcrew.models.base.BaseLLM` instance.
        """
        from antcrew.eval.feedback import EvalFeedbackAgent, ImprovementPlan  # noqa: F401
        agent = EvalFeedbackAgent(llm=llm)
        return agent.analyse(reports)

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.cases)

    def __repr__(self) -> str:
        return f"EvalSuite(name={self.name!r}, cases={len(self.cases)})"
