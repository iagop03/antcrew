from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    CodeArtifact,
    RetroReport,
    Ticket,
    coerce_list,
)
from antcrew.core.state import TeamState

_SYSTEM = """\
You are an Agile coach facilitating a sprint retrospective.
Given the sprint artifacts (tickets completed, code produced, review feedback), generate a concise retro.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "sprint_number": <integer>,
  "what_went_well": ["<item>", ...],
  "what_could_improve": ["<item>", ...],
  "action_items": ["<concrete next step>", ...],
  "velocity_note": "<e.g. '5 tickets closed, 2 carried forward'>",
  "rationale": "<brief note on how you derived these insights>"
}

Guidelines:
- what_went_well: 2-4 specific positives from the sprint artifacts.
- what_could_improve: 2-4 honest issues observed in the code, review, or process.
- action_items: 2-3 concrete, actionable improvements for the next sprint.
- Keep each item concise (1 sentence max).
"""


class RetroAgent(BaseAgent):
    name = "retro"
    role_description = "Generates a sprint retrospective from completed tickets, code, and review."
    consumes: list[str] = ["tickets", "code_artifacts", "review", "sprint_number", "sprint_backlog"]
    produces: list[str] = ["retro_report"]

    def run(self, state: TeamState) -> dict:
        sprint_number = state.get("sprint_number") or 1
        tickets = coerce_list(state, "tickets", Ticket)
        backlog = coerce_list(state, "sprint_backlog", Ticket)
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        review = state.get("review")

        done_tickets = [t for t in tickets if getattr(t, "status", None) != "open"]
        parts: list[str] = [f"Sprint: {sprint_number}"]

        if done_tickets:
            parts.append(
                f"Completed tickets ({len(done_tickets)}):\n"
                + json.dumps(
                    [{"id": t.id, "title": t.title, "priority": str(t.priority)} for t in done_tickets],
                    indent=2,
                )
            )

        if backlog:
            parts.append(
                f"Remaining backlog ({len(backlog)} tickets not started): "
                + ", ".join(t.id for t in backlog[:5])
            )

        if code_artifacts:
            parts.append(
                f"Code produced: {len(code_artifacts)} file(s) — "
                + ", ".join(a.file_path for a in code_artifacts[:8])
            )

        if review:
            raw = review.model_dump() if hasattr(review, "model_dump") else review
            verdict = raw.get("verdict", "")
            findings_count = len(raw.get("findings", []))
            parts.append(
                f"Code review verdict: {verdict} ({findings_count} finding(s)). "
                f"Summary: {raw.get('summary', '')}"
            )

        context = "\n\n".join(parts)
        data: dict = self.system_parsed(_SYSTEM, context, dict)

        try:
            report = RetroReport(
                sprint_number=data.get("sprint_number", sprint_number),
                what_went_well=data.get("what_went_well") or [],
                what_could_improve=data.get("what_could_improve") or [],
                action_items=data.get("action_items") or [],
                velocity_note=data.get("velocity_note", ""),
                rationale=data.get("rationale"),
            )
        except Exception:
            report = RetroReport(sprint_number=sprint_number)

        return {
            "retro_report": report,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Retro] Sprint {report.sprint_number}: "
                        f"{len(report.what_went_well)} positives, "
                        f"{len(report.action_items)} action item(s). "
                        f"{report.velocity_note}"
                    ),
                }
            ],
        }
