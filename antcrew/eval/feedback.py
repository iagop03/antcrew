"""EvalFeedbackAgent — LLM-powered improvement suggestions from EvalReport results.

Usage::

    from antcrew.eval import EvalFeedbackAgent, ImprovementPlan
    from antcrew.models.anthropic_model import AnthropicModel

    agent  = EvalFeedbackAgent(llm=AnthropicModel())
    plan   = agent.analyse(reports)

    print(plan.priority_action)
    for suggestion in plan.agents:
        print(f"{suggestion.agent} (score {suggestion.current_score:.2f}):")
        for s in suggestion.suggestions:
            print(f"  • {s}")
"""
from __future__ import annotations

from pydantic import BaseModel

from antcrew.core.agent import BaseAgent
from antcrew.eval.case import EvalReport


class AgentImprovement(BaseModel):
    """Improvement plan for a single agent."""

    agent: str
    current_score: float
    weak_metrics: list[str]
    suggestions: list[str]


class ImprovementPlan(BaseModel):
    """Full improvement plan returned by :class:`EvalFeedbackAgent`."""

    summary: str
    agents: list[AgentImprovement]
    priority_action: str


class EvalFeedbackAgent(BaseAgent):
    """Analyse :class:`~antcrew.eval.EvalReport` results with an LLM and produce
    concrete improvement suggestions.

    This agent is intentionally *not* part of the production pipeline — it is a
    developer tool used offline after an eval run::

        suite   = EvalSuite.from_requests("sprint-1", requests)
        reports = suite.run(team)

        agent = EvalFeedbackAgent(llm=AnthropicModel())
        plan  = agent.analyse(reports)
        print(plan.priority_action)

    Or via the :meth:`~antcrew.eval.EvalSuite.feedback` shortcut::

        plan = suite.feedback(reports, llm=AnthropicModel())
    """

    name = "eval_feedback"
    role_description = "Pipeline quality analyst"

    _SYSTEM = (
        "You are an expert at analysing AI pipeline evaluation results. "
        "Given a set of per-agent score reports, identify which agents are "
        "underperforming, which metrics are weakest, and provide concrete, "
        "actionable improvement suggestions (specific prompt changes, output "
        "format adjustments, or structural pipeline changes). "
        "Return ONLY valid JSON matching the schema — no prose, no fences."
    )

    def run(self, state: dict) -> dict:  # pragma: no cover
        return {}  # not used in production pipelines

    def analyse(self, reports: list[EvalReport]) -> ImprovementPlan:
        """Analyse *reports* and return a structured :class:`ImprovementPlan`.

        Raises the same exceptions as :meth:`~antcrew.core.agent.BaseAgent.system_parsed`
        when the LLM output cannot be parsed after retries.
        """
        lines = self._build_report_text(reports)
        user = "Analyse these evaluation results and suggest improvements:\n\n" + lines
        return self.system_parsed(self._SYSTEM, user, ImprovementPlan, max_retries=1)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_report_text(reports: list[EvalReport]) -> str:
        parts: list[str] = []
        for r in reports:
            status = "PASS" if r.passed else "FAIL"
            parts.append(
                f"Case: {r.case.name} | status={status} | overall_score={r.overall_score:.2f}"
            )
            for agent_name, score in r.agent_scores.items():
                metrics_str = ", ".join(
                    f"{k}={v:.2f}" for k, v in score.metrics.items()
                )
                parts.append(f"  {agent_name}: {metrics_str}")
            if r.errors:
                parts.append("  errors: " + "; ".join(r.errors))
        return "\n".join(parts)
