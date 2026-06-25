from __future__ import annotations

import logging
from typing import Optional

from antcrew.agents.idea import IdeaAgent
from antcrew.agents.copywriter import CopywriterAgent
from antcrew.agents.editor import EditorAgent
from antcrew.core.run_result import RunResult
from antcrew.core.supervisor import Supervisor
from antcrew.core.state import TeamState
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.base import BaseLLM
from antcrew.teams.base import InteractiveMixin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.memory.store import BaseMemory
    from langgraph.checkpoint.base import BaseCheckpointSaver

log = logging.getLogger(__name__)

_DEFAULT_FLOW = [
    ("idea", "copywriter"),
    ("copywriter", "editor"),
]


class ContentTeam(InteractiveMixin):
    """
    Content creation pipeline: IdeaAgent → CopywriterAgent → EditorAgent.

    IdeaAgent:       content request → content brief (title, audience, tone, outline)
    CopywriterAgent: content brief   → full draft body
    EditorAgent:     draft           → polished final content

    Usage:
        team = ContentTeam()
        state = team.run("Write a blog post about multi-agent frameworks")
        print(state["content_piece"].body)

    Interactive HITL:
        state = team.run_interactive("Blog post about multi-agent frameworks")

    Level 2 — swap model for one agent:
        team = ContentTeam(
            agents={
                "copywriter": CopywriterAgent(llm=OllamaModel("llama3")),
            }
        )

    Level 3 — skip the editor (two-stage pipeline):
        supervisor = Supervisor(flow=[("idea", "copywriter")])
        team = ContentTeam(supervisor=supervisor)
    """

    def __init__(
        self,
        model: Optional[BaseLLM] = None,
        agents: Optional[dict] = None,
        supervisor: Optional[Supervisor] = None,
        memory: Optional["BaseMemory"] = None,
        checkpointer: "Optional[BaseCheckpointSaver]" = None,
    ) -> None:
        self.llm = model or AnthropicModel()
        self.memory = memory
        self._checkpointer = checkpointer

        defaults = {
            "idea":       IdeaAgent(self.llm),
            "copywriter": CopywriterAgent(self.llm),
            "editor":     EditorAgent(self.llm),
        }
        if agents:
            defaults.update(agents)
        self._agents = defaults
        self._supervisor = supervisor or Supervisor(flow=_DEFAULT_FLOW)
        if memory:
            for agent in self._agents.values():
                agent.memory = memory

    def _initial_state(self, request: str) -> TeamState:
        return {
            "request": request,
            "messages": [{"role": "user", "content": request}],
            "prd": None,
            "tickets": None,
            "code_artifacts": None,
            "test_artifacts": None,
            "review": None,
            "devops_artifacts": None,
            "doc_artifacts": None,
            "research_document": None,
            "content_piece": None,
            "current_agent": "",
            "errors": [],
            "metadata": {},
        }

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Execute the content pipeline without human interaction."""
        app = self._supervisor.build(self._agents, checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        state = app.invoke(self._initial_state(request), config=config)
        if self.memory:
            self.memory.store_run(state)
        cost = 0.0
        try:
            cost = (self.llm.get_usage_summary() or {}).get("total_cost_usd") or 0.0
        except Exception:
            pass
        return RunResult(state=state, thread_id=thread_id, cost_usd=cost)

    # run_interactive() and _apply_edit() come from InteractiveMixin
