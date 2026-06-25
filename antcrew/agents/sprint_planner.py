from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState

_DEFAULT_SPRINT_SIZE = 4


class SprintPlannerAgent(BaseAgent):
    """Divides the PM backlog into fixed-size sprints.

    Runs automatically (no LLM call, no approval).  On each pass it moves
    the next ``sprint_size`` tickets from ``sprint_backlog`` into ``tickets``
    so that downstream agents (backend_dev, frontend_dev, QA) only work on
    a manageable slice.  Sets routing flags in ``metadata`` so the Supervisor
    can loop back after the Reviewer or proceed to DevOps when done.
    """

    name = "sprint_planner"
    role_description = "Divides the product backlog into focused sprints."
    consumes: list[str] = ["tickets", "sprint_backlog"]
    produces: list[str] = ["tickets", "sprint_backlog", "sprint_number", "metadata"]

    def __init__(self, llm, *, sprint_size: int = _DEFAULT_SPRINT_SIZE, **kwargs) -> None:
        super().__init__(llm, **kwargs)
        self.sprint_size = sprint_size

    def run(self, state: TeamState) -> dict:
        backlog = list(state.get("sprint_backlog") or [])
        sprint_num = (state.get("sprint_number") or 0) + 1

        # First sprint: backlog is empty — populate from PM-generated tickets.
        if not backlog:
            backlog = list(state.get("tickets") or [])

        sprint = backlog[: self.sprint_size]
        remaining = backlog[self.sprint_size :]
        sprint_complete = len(remaining) == 0

        remaining_str = "last sprint" if sprint_complete else f"{len(remaining)} remaining"
        return {
            "tickets": sprint,
            "sprint_backlog": remaining,
            "sprint_number": sprint_num,
            "current_agent": self.name,
            "metadata": {
                "sprint_in_progress": not sprint_complete,
                "sprint_complete": sprint_complete,
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Sprint {sprint_num}] {len(sprint)} tickets selected "
                        f"({remaining_str})."
                    ),
                }
            ],
        }
