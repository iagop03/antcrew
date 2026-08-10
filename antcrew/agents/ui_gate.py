"""UIGateAgent — HITL gate for human review of UI screen proposals."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState


class UIGateAgent(BaseAgent):
    """Pass-through gate that triggers a structured list review of UI screens.

    Fires a HITL checkpoint so a human can validate the UI design specification
    before frontend development starts.  Reviewers can add, remove, or modify
    screen definitions before approving.
    """

    name = "ui_gate"
    role_description = "Human review gate for UI screens before frontend development"
    review_type = "structured_list"
    item_schema = "screens"

    def run(self, state: TeamState) -> dict:
        return {"current_agent": self.name}
