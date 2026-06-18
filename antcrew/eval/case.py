"""EvalCase and result data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from antcrew.eval.judge import JudgeResult


@dataclass
class EvalCase:
    """A single evaluation case for a pipeline run.

    Required:
        request   — the natural-language request sent to the team

    Optional expectations (violations set passed=False on the report):
        expect_min_tickets      — pipeline must produce at least N tickets
        expect_min_code_files   — pipeline must produce at least N code files
        expect_review_verdict   — "approve" | "request_changes" | "reject"
    """

    request: str
    name: str = ""
    tags: list[str] = field(default_factory=list)

    # Soft expectations -------------------------------------------------------
    expect_min_tickets: int = 0
    expect_min_code_files: int = 0
    expect_review_verdict: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.request[:60]


@dataclass
class AgentScore:
    """Scoring for a single agent's output in one pipeline run."""

    agent: str
    metrics: dict[str, float]  # name → 0.0–1.0
    details: dict[str, Any]    # raw values for inspection

    @property
    def score(self) -> float:
        if not self.metrics:
            return 0.0
        return sum(self.metrics.values()) / len(self.metrics)


@dataclass
class EvalReport:
    """Full evaluation report for one EvalCase."""

    case: EvalCase
    elapsed_ms: float
    token_usage: dict[str, Any]                       # from llm.get_usage_summary()
    agent_scores: dict[str, AgentScore]
    overall_score: float                              # structural score 0.0-1.0
    passed: bool
    errors: list[str]
    judge_results: "dict[str, JudgeResult]" = field(default_factory=dict)  # LLM-as-judge

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def token_count(self) -> int:
        return (
            self.token_usage.get("total_input_tokens", 0)
            + self.token_usage.get("total_output_tokens", 0)
        )

    @property
    def cost_usd(self) -> float:
        return float(self.token_usage.get("total_cost_usd", 0.0))

    @property
    def judge_score(self) -> float:
        """Mean LLM-judge score across all judged artifacts (0.0-1.0).  0.0 if no judges ran."""
        if not self.judge_results:
            return 0.0
        return sum(j.score for j in self.judge_results.values()) / len(self.judge_results)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        judge_str = f"  judge={self.judge_score:.2f}" if self.judge_results else ""
        lines = [
            f"[{status}] {self.case.name}",
            f"  structural={self.overall_score:.2f}{judge_str}"
            f"  tokens={self.token_count}"
            f"  cost=${self.cost_usd:.4f}  {self.elapsed_ms:.0f}ms",
        ]
        for agent, s in self.agent_scores.items():
            metric_str = "  ".join(f"{k}={v:.2f}" for k, v in s.metrics.items())
            lines.append(f"  [struct] {agent}: {metric_str}")
        for artifact, j in self.judge_results.items():
            lines.append(
                f"  [judge]  {artifact}: {j.raw_score}/10 — {j.reasoning[:80]}"
            )
        if self.errors:
            for e in self.errors:
                lines.append(f"  ! {e}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "case": {
                "name": self.case.name,
                "request": self.case.request,
                "tags": self.case.tags,
            },
            "passed": self.passed,
            "overall_score": self.overall_score,
            "judge_score": self.judge_score,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "token_count": self.token_count,
            "cost_usd": self.cost_usd,
            "agent_scores": {
                agent: {
                    "score": round(s.score, 3),
                    "metrics": {k: round(v, 3) for k, v in s.metrics.items()},
                    "details": s.details,
                }
                for agent, s in self.agent_scores.items()
            },
            "judge_results": {
                artifact: j.to_dict() for artifact, j in self.judge_results.items()
            },
            "errors": self.errors,
        }
