from __future__ import annotations

import logging
from typing import Optional

from antcrew.agents.codebase_scanner import CodebaseScannerAgent
from antcrew.agents.business import BusinessAnalystAgent
from antcrew.agents.coherence import CoherenceAgent
from antcrew.agents.pm import PMAgent
from antcrew.agents.sprint_planner import SprintPlannerAgent
from antcrew.agents.backend_dev import BackendDevAgent
from antcrew.agents.frontend_dev import FrontendDevAgent
from antcrew.agents.qa import QAAgent
from antcrew.agents.reviewer import ReviewerAgent
from antcrew.agents.devops import DevOpsAgent
from antcrew.agents.doc_writer import DocWriterAgent
from antcrew.core.run_result import RunResult
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
    from antcrew.trace import TraceLog
    from langgraph.checkpoint.base import BaseCheckpointSaver

log = logging.getLogger(__name__)

_DEFAULT_FLOW = [
    ("codebase_scanner",  "business_analyst"),
    ("business_analyst",  "pm"),
    ("pm",                "sprint_planner"),
    ("sprint_planner",    "backend_dev"),
    ("backend_dev",       "frontend_dev"),
    ("frontend_dev",      "qa"),
    # QA always routes to reviewer — bug findings are surfaced there for HITL decision.
    ("qa",                "reviewer"),
    # After reviewer HITL: fix-mode takes priority; then sprint routing.
    ("reviewer",          "backend_dev",    "reviewer_fix_requested"),
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
        project_dir: Optional[str] = None,
        project_dirs: Optional[dict] = None,
        scan_context: Optional[dict] = None,
        checkpointer: "Optional[BaseCheckpointSaver]" = None,
        max_cost_usd: Optional[float] = None,
        trace_log: "Optional[TraceLog]" = None,
        agent_models: Optional[dict[str, BaseLLM]] = None,
        feedback_rounds: int = 0,
        lint_cmd: "Optional[list[str] | str]" = None,
        work_dir: "Optional[str]" = None,
        enable_coherence: bool = False,
        project_kb_path: "Optional[str]" = None,
    ) -> None:
        self.llm = model or AnthropicModel()
        self.integrations: list = integrations or []
        self.memory = memory
        self._runner = runner
        self._feedback_rounds: int = feedback_rounds
        self._lint_cmd = lint_cmd
        self._work_dir = work_dir
        self._enable_coherence: bool = enable_coherence
        self._checkpointer = checkpointer
        self._trace_log = trace_log
        if max_cost_usd is not None:
            self.llm.max_cost_usd = max_cost_usd
        self.project_dir: Optional[str] = project_dir
        self.project_dirs: Optional[dict] = project_dirs
        self.scan_context: Optional[dict] = scan_context
        self._project_kb = None
        if project_kb_path:
            from antcrew.core.project_kb import ProjectKB
            from pathlib import Path as _Path
            self._project_kb = ProjectKB.load(project_kb_path)
            self._project_kb._path = _Path(project_kb_path)

        _am = agent_models or {}
        defaults = {
            "codebase_scanner": CodebaseScannerAgent(_am.get("codebase_scanner", self.llm)),
            "coherence":        CoherenceAgent(_am.get("coherence", self.llm)),
            "business_analyst": BusinessAnalystAgent(_am.get("business_analyst", self.llm)),
            "pm":               PMAgent(_am.get("pm", self.llm)),
            "sprint_planner":   SprintPlannerAgent(_am.get("sprint_planner", self.llm), sprint_size=sprint_size),
            "backend_dev":      BackendDevAgent(_am.get("backend_dev", self.llm)),
            "frontend_dev":     FrontendDevAgent(_am.get("frontend_dev", self.llm)),
            "qa":               QAAgent(_am.get("qa", self.llm)),
            "reviewer":         ReviewerAgent(_am.get("reviewer", self.llm)),
            "devops":           DevOpsAgent(_am.get("devops", self.llm)),
            "doc_writer":       DocWriterAgent(_am.get("doc_writer", self.llm)),
        }
        if agents:
            defaults.update(agents)
        self._agents = defaults
        self._supervisor = supervisor or Supervisor(flow=_DEFAULT_FLOW)

        if memory:
            for agent in self._agents.values():
                agent.memory = memory

        self.repo_index: "Optional[_RepoIndexT]" = None
        self.symbol_index = None
        scan_paths: list[str] = []
        if repo_path:
            scan_paths.append(repo_path)
        if project_dir:
            scan_paths.append(project_dir)
        if project_dirs:
            scan_paths.extend(project_dirs.values())

        if repo_path:
            from antcrew.memory.repo_index import RepoIndex
            self.repo_index = RepoIndex(repo_path)
            self.repo_index.build()
            for agent in self._agents.values():
                agent.repo_index = self.repo_index

        if scan_paths:
            from antcrew.core.symbol_index import SymbolIndex
            self.symbol_index = SymbolIndex.build(scan_paths)
            for agent in self._agents.values():
                agent.symbol_index = self.symbol_index

    def _parse_scan_context(self):
        """Return (codebase_analysis, codebase_analyses) from self.scan_context or (None, None)."""
        if not self.scan_context:
            return None, None
        from antcrew.core.artifacts import CodebaseAnalysis
        ctx = self.scan_context
        if "components" in ctx:
            analyses = [CodebaseAnalysis(**{k: v for k, v in c.items()
                                           if k in CodebaseAnalysis.model_fields})
                        for c in ctx["components"]]
            return analyses[0] if analyses else None, analyses or None
        # Single-component format
        ca = CodebaseAnalysis(**{k: v for k, v in ctx.items()
                                 if k in CodebaseAnalysis.model_fields})
        return ca, None

    def _initial_state(self, request: str) -> TeamState:
        pre_ca, pre_cas = self._parse_scan_context()
        return {
            "request": request,
            "messages": [{"role": "user", "content": request}],
            "project_dir": self.project_dir or None,
            "project_dirs": self.project_dirs or None,
            "codebase_analysis": pre_ca,
            "codebase_analyses": pre_cas,
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
            "_kb_context": "",
        }

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Execute the full-stack pipeline without human interaction."""
        _llms = self._unique_llms()
        if self.llm.max_cost_usd is not None:
            self.llm._cost_limit_offset = self.llm.get_usage_summary()["total_cost_usd"]
        _run_id: Optional[str] = None
        if self._trace_log is not None:
            _run_id = self._trace_log.begin_run(
                thread_id=thread_id, request=request, team=type(self).__name__,
            )
            for _llm in _llms:
                _llm.trace = self._trace_log
                _llm._trace_run_id = _run_id
        try:
            app = self._supervisor.build(self._agents, checkpointer=self._checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            initial = self._initial_state(request)
            if self._project_kb:
                initial["_kb_context"] = self._project_kb.context_for_agent("backend_dev")
            state = app.invoke(initial, config=config)
            if self._project_kb:
                try:
                    self._project_kb.update_from_state(state)
                    self._project_kb.save()
                except Exception as exc:
                    log.warning("ProjectKB update failed: %s", exc)
            if self._enable_coherence and "coherence" in self._agents and state.get("code_artifacts"):
                try:
                    coherence_result = self._agents["coherence"].run(state)
                    state.update(coherence_result)
                except Exception as exc:
                    log.warning("CoherenceAgent failed: %s", exc)
            if self.memory:
                self.memory.store_run(state)
            if self._runner and state.get("test_artifacts"):
                try:
                    state["test_results"] = self._runner.run(
                        state["test_artifacts"],
                        code_artifacts=state.get("code_artifacts") or [],
                    )
                    if (
                        self._feedback_rounds > 0
                        and not state["test_results"].success
                        and "backend_dev" in self._agents
                    ):
                        from antcrew.core.feedback import run_test_feedback_loop
                        state = run_test_feedback_loop(
                            state,
                            self._agents["backend_dev"],
                            self._runner,
                            max_rounds=self._feedback_rounds,
                            lint_cmd=self._lint_cmd,
                            work_dir=self._work_dir,
                        )
                except Exception as exc:
                    log.warning("SandboxRunner failed: %s", exc)
            cost = sum(
                (llm.get_usage_summary() or {}).get("total_cost_usd") or 0.0
                for llm in _llms
            )
            if self._trace_log is not None and _run_id is not None:
                self._trace_log.end_run(_run_id, cost_usd=cost)
            return RunResult(state=state, thread_id=thread_id, cost_usd=cost)
        except Exception:
            if self._trace_log is not None and _run_id is not None:
                self._trace_log.end_run(_run_id, cost_usd=0.0, status="error")
            raise
        finally:
            if self._trace_log is not None:
                for _llm in _llms:
                    _llm.trace = None
                    _llm._trace_run_id = None

    # run_interactive() and _apply_edit() come from InteractiveMixin
