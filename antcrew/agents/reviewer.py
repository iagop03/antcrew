from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import CodeReview, ReviewFinding
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a Senior Software Engineer conducting a code review.
Given code artifacts and the original tickets, produce a structured code review.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "verdict": "approve" | "request_changes" | "reject",
  "summary": "<overall summary of the review>",
  "findings": [
    {
      "severity": "info" | "warning" | "error" | "critical",
      "file_path": "src/...",
      "message": "<what is wrong or notable>",
      "suggestion": "<how to fix it>",
      "line": <line number or null>
    },
    ...
  ]
}

Rules:
- verdict "approve": code is production-ready, ≤2 minor findings.
- verdict "request_changes": non-critical issues that must be fixed.
- verdict "reject": security vulnerabilities, critical bugs, or major design flaws.
- Always populate "findings" — even for approved code, list what was done well (severity "info").
- Be specific: name the file, quote the problematic pattern, suggest a concrete fix.
"""

_REFINE_SYSTEM = """\
You are a Senior Software Engineer. The reviewer of the review provided feedback on your assessment.
Update the code review to address the feedback while keeping everything else intact.

Current review:
{artifact_json}

Feedback:
{feedback}

Respond ONLY with the complete updated code review JSON (no markdown fences, no prose).
"""


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    role_description = "Conducts a senior code review and produces a structured verdict."
    conversational = True

    def __init__(self, llm, *, channel=None, approval_required=False, response_options=None):
        super().__init__(
            llm,
            channel=channel,
            approval_required=approval_required,
            response_options=response_options or ["approve", "fix", "reject"],
        )

    def run(self, state: TeamState) -> dict:
        code_artifacts = state.get("code_artifacts") or []
        tickets = state.get("tickets") or []
        meta = state.get("metadata") or {}

        if not code_artifacts:
            return {
                "current_agent": self.name,
                "messages": [
                    {"role": "assistant", "content": "[Reviewer] No code artifacts to review."}
                ],
                "metadata": {"review_verdict": "approve"},
            }

        ticket_summary = " ".join(t.title for t in tickets[:3])
        memory_context = self._recall(ticket_summary or "code review")
        repo_context   = self._search_repo(ticket_summary or "code conventions style")

        qa_note = ""
        if meta.get("has_critical_bugs"):
            qa_note = (
                f"\n\nQA Bug Report: CRITICAL BUGS DETECTED — {meta.get('qa_summary', '')}\n"
                "Include these bugs as 'critical' severity findings and set verdict to 'reject'.\n"
            )

        context = (
            f"Tickets:\n{json.dumps([t.model_dump() for t in tickets], indent=2)}\n\n"
            f"Code Artifacts:\n{json.dumps([a.model_dump() for a in code_artifacts], indent=2)}"
            + qa_note
        )
        raw = self.system(_SYSTEM + memory_context + repo_context, context)
        data: dict = _json_loads(_strip_fences(raw))
        findings = [
            ReviewFinding(**{k: v for k, v in f.items() if k in ReviewFinding.model_fields})
            for f in data.get("findings", [])
        ]
        review = CodeReview(
            verdict=data.get("verdict", "approve"),
            summary=data.get("summary", ""),
            findings=findings,
        )
        return {
            "review": review,
            "current_agent": self.name,
            "metadata": {
                "review_verdict": review.verdict,
                "review_approved": review.verdict == "approve",
                "review_needs_changes": review.verdict != "approve",
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Reviewer] Verdict: {review.verdict.upper()}. "
                        f"{len(review.findings)} findings. {review.summary}"
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: CodeReview, feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Revise the code review based on the feedback.",
        )
        data: dict = _json_loads(_strip_fences(raw))
        findings = [
            ReviewFinding(**{k: v for k, v in f.items() if k in ReviewFinding.model_fields})
            for f in data.get("findings", [])
        ]
        review = CodeReview(
            verdict=data.get("verdict", artifact.verdict),
            summary=data.get("summary", artifact.summary),
            findings=findings,
        )
        return {
            "review": review,
            "metadata": {
                "review_verdict": review.verdict,
                "review_approved": review.verdict == "approve",
                "review_needs_changes": review.verdict != "approve",
            },
        }
