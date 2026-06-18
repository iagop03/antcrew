from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _strip_fences
from antcrew.core.artifacts import Ticket, Priority, TicketStatus
from antcrew.core.state import TeamState

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

    def run(self, state: TeamState) -> dict:
        prd = state.get("prd")
        if prd is None:
            return {"errors": ["PMAgent: no PRD found in state"]}

        context = self._recall(prd.title + " " + prd.summary)
        raw = self.system(_SYSTEM + context, f"PRD:\n{prd.model_dump_json(indent=2)}")
        raw_tickets: list[dict] = json.loads(_strip_fences(raw))
        tickets = [
            Ticket(
                id=f"TICKET-{i + 1:03d}",
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
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([t.model_dump() for t in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the tickets based on the feedback.",
        )
        raw_tickets: list[dict] = json.loads(_strip_fences(raw))
        tickets = [
            Ticket(
                id=t.get("id", f"TICKET-{i + 1:03d}"),
                status=TicketStatus(t.get("status", "open")),
                **{k: v for k, v in t.items() if k in Ticket.model_fields and k not in ("id", "status")},
            )
            for i, t in enumerate(raw_tickets)
        ]
        return {"tickets": tickets}
