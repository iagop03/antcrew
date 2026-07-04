from __future__ import annotations

import logging
from typing import Optional

from antcrew.agents.idea import IdeaAgent
from antcrew.agents.copywriter import CopywriterAgent
from antcrew.agents.editor import EditorAgent
from antcrew.core.events import bus, new_run_id
from antcrew.core.run_result import RunResult
from antcrew.core.supervisor import Supervisor
from antcrew.core.state import TeamState
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.base import BaseLLM
from antcrew.teams.base import InteractiveMixin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.memory.store import BaseMemory
    from antcrew.trace import TraceLog
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
        max_cost_usd: Optional[float] = None,
        trace_log: "Optional[TraceLog]" = None,
        agent_models: Optional[dict[str, BaseLLM]] = None,
    ) -> None:
        self.llm = model or AnthropicModel()
        self.memory = memory
        self._checkpointer = checkpointer
        self._trace_log = trace_log
        if max_cost_usd is not None:
            self.llm.max_cost_usd = max_cost_usd

        _am = agent_models or {}
        defaults = {
            "idea":       IdeaAgent(_am.get("idea", self.llm)),
            "copywriter": CopywriterAgent(_am.get("copywriter", self.llm)),
            "editor":     EditorAgent(_am.get("editor", self.llm)),
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
        _llms = self._unique_llms()
        if self.llm.max_cost_usd is not None:
            self.llm._cost_limit_offset = self.llm.get_usage_summary()["total_cost_usd"]

        _run_id = new_run_id()
        _trace_run_id: Optional[str] = None
        if self._trace_log is not None:
            _trace_run_id = self._trace_log.begin_run(
                thread_id=thread_id, request=request, team=type(self).__name__,
            )
            for _llm in _llms:
                _llm.trace = self._trace_log
                _llm._trace_run_id = _trace_run_id

        bus.emit("pipeline.start", {"team": type(self).__name__, "request": request},
                 run_id=_run_id, thread_id=thread_id)
        try:
            app = self._supervisor.build(self._build_agent_map(), checkpointer=self._checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            initial = self._initial_state(request)
            initial["_run_id"] = _run_id
            initial["_thread_id"] = thread_id
            state = app.invoke(initial, config=config)
            if self.memory:
                self.memory.store_run(state)
            cost = sum(
                (llm.get_usage_summary() or {}).get("total_cost_usd") or 0.0
                for llm in _llms
            )
            if self._trace_log is not None and _trace_run_id is not None:
                self._trace_log.end_run(_trace_run_id, cost_usd=cost)
            bus.emit("pipeline.end",
                     {"cost_usd": cost, "success": not state.get("errors"),
                      "artifact_summary": {"content_piece": bool(state.get("content_piece"))}},
                     run_id=_run_id, thread_id=thread_id)
            return RunResult(state=state, thread_id=thread_id, cost_usd=cost)
        except Exception:
            if self._trace_log is not None and _trace_run_id is not None:
                self._trace_log.end_run(_trace_run_id, cost_usd=0.0, status="error")
            bus.emit("pipeline.end",
                     {"cost_usd": 0.0, "success": False, "artifact_summary": {}},
                     run_id=_run_id, thread_id=thread_id)
            raise
        finally:
            if self._trace_log is not None:
                for _llm in _llms:
                    _llm.trace = None
                    _llm._trace_run_id = None

    # run_interactive() and _apply_edit() come from InteractiveMixin
