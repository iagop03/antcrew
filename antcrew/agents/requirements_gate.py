"""RequirementsGateAgent — HITL gate for human review of requirements (PRD)."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState


class RequirementsGateAgent(BaseAgent):
    """Pass-through gate that triggers a structured list review of requirements.

    Fires a HITL checkpoint so a human can validate the business analyst's
    requirements before the PM breaks them down into tickets.  The artifact
    shown is the PRD; the reviewer can approve, request changes, or add
    requirements via edit.
    """

    name = "requirements_gate"
    role_description = "Human review gate for requirements (PRD) before ticket creation"
    review_type = "structured_list"
    item_schema = "requirements"

    def run(self, state: TeamState) -> dict:
        return {"current_agent": self.name}
