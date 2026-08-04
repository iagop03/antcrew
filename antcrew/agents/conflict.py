from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    CodeArtifact,
    ConflictItem,
    ConflictReport,
    Ticket,
    coerce_list,
)
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a technical consistency analyst.
Given a set of software artifacts (PRD, tickets, code, review), identify contradictions,
misalignments, or conflicts between them.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "conflicts": [
    {
      "artifact_a": "<which artifact, e.g. 'prd' or 'ticket:TICKET-001'>",
      "artifact_b": "<which artifact, e.g. 'review' or 'code:src/auth.py'>",
      "description": "<what contradicts what, and why it matters>",
      "severity": "<low|medium|high>"
    }
  ],
  "summary": "<overall conflict status in 1-2 sentences — 'No conflicts found.' if clean>",
  "rationale": "<brief explanation of your analysis approach>"
}

If no conflicts are found, return an empty conflicts array.
Focus on semantic contradictions: spec says X but code does Y, ticket requires A but review rejects A.
"""


class ConflictAgent(BaseAgent):
    name = "conflict_detector"
    role_description = "Detects contradictions between artifacts (PRD, tickets, code, review)."
    consumes: list[str] = ["prd", "tickets", "code_artifacts", "review"]
    produces: list[str] = ["conflict_report"]

    def run(self, state: TeamState) -> dict:
        prd = state.get("prd")
        tickets = coerce_list(state, "tickets", Ticket)
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        review = state.get("review")

        parts: list[str] = []
        if prd:
            raw = prd.model_dump() if hasattr(prd, "model_dump") else prd
            parts.append(f"PRD:\n{json.dumps(raw, indent=2)}")
        if tickets:
            parts.append(
                f"Tickets ({len(tickets)}):\n"
                + json.dumps(
                    [t.model_dump() if hasattr(t, "model_dump") else t for t in tickets],
                    indent=2,
                )
            )
        if code_artifacts:
            summaries = [
                {"file": a.file_path, "ticket": a.ticket_id, "language": a.language}
                for a in code_artifacts
            ]
            parts.append(
                f"Code artifacts ({len(code_artifacts)} files):\n"
                + json.dumps(summaries, indent=2)
            )
        if review:
            raw = review.model_dump() if hasattr(review, "model_dump") else review
            parts.append(f"Code review:\n{json.dumps(raw, indent=2)}")

        if not parts:
            report = ConflictReport(summary="No artifacts to analyze.")
            return {
                "conflict_report": report,
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[ConflictDetector] No artifacts to analyze."}],
            }

        context = "\n\n".join(parts)
        data: dict = self.system_parsed(_SYSTEM, context, dict)

        try:
            conflicts = [
                ConflictItem(
                    artifact_a=c.get("artifact_a", ""),
                    artifact_b=c.get("artifact_b", ""),
                    description=c.get("description", ""),
                    severity=c.get("severity", "medium"),
                )
                for c in (data.get("conflicts") or [])
            ]
            report = ConflictReport(
                conflicts=conflicts,
                summary=data.get("summary", ""),
                rationale=data.get("rationale"),
            )
        except Exception:
            report = ConflictReport(summary=data.get("summary", "Analysis incomplete."))

        count = len(report.conflicts)
        severity_tag = "high" if any(c.severity == "high" for c in report.conflicts) else "ok"
        return {
            "conflict_report": report,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[ConflictDetector] {count} conflict(s) found "
                        f"(severity={severity_tag}). {report.summary}"
                    ),
                }
            ],
        }
