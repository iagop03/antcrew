from __future__ import annotations

import hashlib
import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import ArtifactContract, ContractError, PRD, Ticket, TicketStatus
from antcrew.core.state import TeamState


def _stable_ticket_id(prd_title: str, ticket_title: str, index: int) -> str:
    """Return a deterministic ticket ID that survives re-runs of the same PRD.

    The ID is a short hex digest of (prd_title, ticket_title) so that the
    platform layer can upsert by ID without creating duplicate tickets when
    the same pipeline runs twice.  ``index`` acts as a tie-breaker when two
    tickets share an identical title within the same PRD.
    """
    key = f"{prd_title.strip().lower()}::{ticket_title.strip().lower()}::{index}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    return f"TICKET-{digest.upper()}"

_PRD_CONTRACT: ArtifactContract[PRD] = ArtifactContract("prd", PRD)

_SYSTEM = """\
You are a Product Manager on a software development team.
Given a PRD, break it down into a prioritised list of development tickets.

Respond ONLY with a valid JSON array of ticket objects (no markdown fences, no prose):
[
  {
    "title": "...",
    "description": "...",
    "priority": "low|medium|high|critical",
    "acceptance_criteria": ["...", ...],
    "dependencies": []
  },
  ...
]

Rules:
- Generate between 3 and 10 tickets.
- Each ticket must be independently testable.
- Order tickets so that foundational ones come first.
"""

_REFINE_SYSTEM = """\
You are a Product Manager. The reviewer provided feedback on the tickets you created.
Update the ticket list to address the feedback while keeping everything else intact.

Current tickets:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of ticket objects (no markdown fences, no prose).
Include the "id" field for each ticket exactly as it appeared in the current list.
"""


class PMAgent(BaseAgent):
    name = "pm"
    role_description = "Breaks a PRD into prioritised development tickets."
    conversational = True
    consumes: list[str] = ["prd"]
    produces: list[str] = ["tickets"]

    def run(self, state: TeamState) -> dict:
        try:
            prd = _PRD_CONTRACT.extract(state)
        except ContractError as exc:
            return {"errors": [f"PMAgent: {exc}"]}

        kb_ctx = str(state.get("_kb_context") or "")
        context = self._recall(prd.title + " " + prd.summary)
        raw_tickets: list[dict] = self.system_parsed(
            _SYSTEM + kb_ctx + context,
            f"PRD:\n{prd.model_dump_json(indent=2)}",
            list[dict],
            max_tokens=16384,
        )
        tickets = [
            Ticket(
                id=_stable_ticket_id(prd.title, t.get("title", ""), i),
                status=TicketStatus.OPEN,
                **{k: v for k, v in t.items() if k in Ticket.model_fields},
            )
            for i, t in enumerate(raw_tickets)
        ]
        return {
            "tickets": tickets,
            "current_agent": self.name,
            "messages": [
                {"role": "assistant", "content": f"[PM] {len(tickets)} tickets created."}
            ],
        }

    def refine(self, state: TeamState, artifact: list[Ticket], feedback: str) -> dict:
        raw_tickets: list[dict] = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([t.model_dump() for t in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the tickets based on the feedback.",
            list[dict],
            max_tokens=16384,
        )
        tickets = [
            Ticket(
                id=t.get("id", f"TICKET-{i + 1:03d}"),
                status=TicketStatus(t.get("status", "open")),
                **{k: v for k, v in t.items() if k in Ticket.model_fields and k not in ("id", "status")},
            )
            for i, t in enumerate(raw_tickets)
        ]
        return {"tickets": tickets}
