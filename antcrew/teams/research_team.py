from __future__ import annotations

import logging
from typing import Optional

from antcrew.agents.researcher import ResearcherAgent
from antcrew.agents.copywriter import CopywriterAgent
from antcrew.core.run_result import RunResult
from antcrew.core.supervisor import Supervisor
from antcrew.core.state import TeamState
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.base import BaseLLM
from antcrew.teams.base import InteractiveMixin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.memory.store import BaseMemory

log = logging.getLogger(__name__)

_DEFAULT_FLOW = [
    ("researcher", "writer"),
]


class ResearchTeam(InteractiveMixin):
    """
    Research pipeline: ResearcherAgent → writer (CopywriterAgent).

    ResearcherAgent gathers and structures a research document on a topic.
    The writer turns that document into a polished Markdown report.

    Usage:
        team = ResearchTeam()
        state = team.run("What are the main risks of agentic AI systems?")
        print(state["content_piece"].body)

    Interactive HITL:
        state = team.run_interactive("Risks of agentic AI")

    Level 2 — custom model per agent:
        team = ResearchTeam(
            agents={
                "researcher": ResearcherAgent(llm=OllamaModel("llama3")),
            }
        )

    Level 3 — custom flow:
        supervisor = Supervisor(flow=[("researcher", "writer"), ("writer", "editor")])
        team = ResearchTeam(supervisor=supervisor, agents={"editor": EditorAgent(llm=...)})
    """

    def __init__(
        self,
        model: Optional[BaseLLM] = None,
        agents: Optional[dict] = None,
        supervisor: Optional[Supervisor] = None,
        memory: Optional["BaseMemory"] = None,
    ) -> None:
        self.llm = model or AnthropicModel()
        self.memory = memory

        defaults = {
            "researcher": ResearcherAgent(self.llm),
            "writer":     CopywriterAgent(self.llm),
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
        """Execute the research pipeline without human interaction."""
        app = self._supervisor.build(self._agents)
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
