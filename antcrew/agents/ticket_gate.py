"""TicketGateAgent — HITL gate for human review of tickets before design."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState


class TicketGateAgent(BaseAgent):
    """Pass-through gate that triggers a structured list review of tickets.

    Fires a HITL checkpoint so a human can validate and trim the PM's ticket
    list before the API and UI design phases start.  Reviewers can add, remove,
    or edit tickets before approving.
    """

    name = "ticket_gate"
    role_description = "Human review gate for tickets before design phase"
    review_type = "structured_list"
    item_schema = "tickets"

    def run(self, state: TeamState) -> dict:
        return {"current_agent": self.name}
