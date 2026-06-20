from __future__ import annotations

import logging
from typing import Optional

from antcrew.agents.business import BusinessAnalystAgent
from antcrew.agents.pm import PMAgent
from antcrew.agents.sprint_planner import SprintPlannerAgent
from antcrew.agents.backend_dev import BackendDevAgent
from antcrew.agents.frontend_dev import FrontendDevAgent
from antcrew.agents.qa import QAAgent
from antcrew.agents.reviewer import ReviewerAgent
from antcrew.agents.devops import DevOpsAgent
from antcrew.agents.doc_writer import DocWriterAgent
from antcrew.core.supervisor import Supervisor
from antcrew.core.state import TeamState
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.base import BaseLLM
from antcrew.teams.base import InteractiveMixin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.memory.store import BaseMemory
    from antcrew.memory.repo_index import RepoIndex as _RepoIndexT
    from antcrew.sandbox.runner import SandboxRunner

log = logging.getLogger(__name__)

_DEFAULT_FLOW = [
    ("business_analyst",  "pm"),
    ("pm",                "sprint_planner"),
    ("sprint_planner",    "backend_dev"),
    ("backend_dev",       "frontend_dev"),
    ("frontend_dev",      "qa"),
    # QA always routes to reviewer — bug findings are surfaced there for HITL decision.
    ("qa",                "reviewer"),
    # After each sprint review: loop back if backlog remains, else proceed to DevOps.
    ("reviewer",          "sprint_planner", "sprint_in_progress"),
    ("reviewer",          "devops",         "sprint_complete"),
    ("devops",            "doc_writer"),
]


class FullStackTeam(InteractiveMixin):
    """
    Full-stack development pipeline with all eight specialised agents.

    Default flow:
        BusinessAnalyst → PM → BackendDev → FrontendDev → QA
            → Reviewer (if no critical bugs) → DevOps → DocWriter
            → BackendDev (if critical bugs, then loops back through QA)

    FrontendDev processes all tickets (regardless of status) and appends
    its artifacts to the code_artifacts produced by BackendDev.

    Usage — Level 1 (defaults):
        team = FullStackTeam()
        state = team.run("Build a task management app with React frontend and FastAPI backend")

    Level 2 — swap one agent:
        team = FullStackTeam(
            agents={
                "frontend_dev": FrontendDevAgent(llm=OllamaModel("llama3")),
            }
        )

    Level 3 — skip DocWriter or DevOps:
        supervisor = Supervisor(flow=[
            ("business_analyst", "pm"),
            ("pm",               "backend_dev"),
            ("backend_dev",      "frontend_dev"),
            ("frontend_dev",     "qa"),
            ("qa",               "reviewer"),
        ])
        team = FullStackTeam(supervisor=supervisor)

    Interactive HITL:
        state = team.run_interactive("Build a task management app")
    """

    def __init__(
        self,
        model: Optional[BaseLLM] = None,
        agents: Optional[dict] = None,
        supervisor: Optional[Supervisor] = None,
        integrations: Optional[list] = None,
        memory: Optional["BaseMemory"] = None,
        repo_path: Optional[str] = None,
        runner: "Optional[SandboxRunner]" = None,
        sprint_size: int = 4,
    ) -> None:
        self.llm = model or AnthropicModel()
        self.integrations: list = integrations or []
        self.memory = memory
        self._runner = runner

        defaults = {
            "business_analyst": BusinessAnalystAgent(self.llm),
            "pm":               PMAgent(self.llm),
            "sprint_planner":   SprintPlannerAgent(self.llm, sprint_size=sprint_size),
            "backend_dev":      BackendDevAgent(self.llm),
            "frontend_dev":     FrontendDevAgent(self.llm),
            "qa":               QAAgent(self.llm),
            "reviewer":         ReviewerAgent(self.llm),
            "devops":           DevOpsAgent(self.llm),
            "doc_writer":       DocWriterAgent(self.llm),
        }
        if agents:
            defaults.update(agents)
        self._agents = defaults
        self._supervisor = supervisor or Supervisor(flow=_DEFAULT_FLOW)

        if memory:
            for agent in self._agents.values():
                agent.memory = memory

        self.repo_index: "Optional[_RepoIndexT]" = None
        if repo_path:
            from antcrew.memory.repo_index import RepoIndex
            self.repo_index = RepoIndex(repo_path)
            self.repo_index.build()
            for agent in self._agents.values():
                agent.repo_index = self.repo_index

    def _initial_state(self, request: str) -> TeamState:
        return {
            "request": request,
            "messages": [{"role": "user", "content": request}],
            "prd": None,
            "tickets": None,
            "sprint_backlog": None,
            "sprint_number": 0,
            "code_artifacts": None,
            "test_artifacts": None,
            "test_results": None,
            "review": None,
            "devops_artifacts": None,
            "doc_artifacts": None,
            "research_document": None,
            "content_piece": None,
            "current_agent": "",
            "errors": [],
            "metadata": {},
        }

    def run(self, request: str, *, thread_id: str = "default") -> TeamState:
        """Execute the full-stack pipeline without human interaction."""
        app = self._supervisor.build(self._agents)
        config = {"configurable": {"thread_id": thread_id}}
        state = app.invoke(self._initial_state(request), config=config)
        if self.memory:
            self.memory.store_run(state)
        if self._runner and state.get("test_artifacts"):
            try:
                state["test_results"] = self._runner.run(
                    state["test_artifacts"],
                    code_artifacts=state.get("code_artifacts") or [],
                )
            except Exception as exc:
                log.warning("SandboxRunner failed: %s", exc)
        return state

    # run_interactive() and _apply_edit() come from InteractiveMixin
