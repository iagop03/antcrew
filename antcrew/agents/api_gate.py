"""APIGateAgent — HITL gate for human review of API endpoint proposals."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState


class APIGateAgent(BaseAgent):
    """Pass-through gate that triggers a structured list review of API endpoints.

    Fires a HITL checkpoint so a human can validate the API design before
    backend development starts.  Reviewers can add, remove, or modify endpoint
    definitions before approving.
    """

    name = "api_gate"
    role_description = "Human review gate for API endpoints before backend development"
    review_type = "structured_list"
    item_schema = "endpoints"

    def run(self, state: TeamState) -> dict:
        return {"current_agent": self.name}
