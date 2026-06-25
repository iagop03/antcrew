from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Optional

from antcrew.agents.business import BusinessAnalystAgent
from antcrew.agents.pm import PMAgent
from antcrew.agents.backend_dev import BackendDevAgent
from antcrew.core.artifacts import PRD, Ticket
from antcrew.core.supervisor import Supervisor
from antcrew.core.state import TeamState
from antcrew.core.run_result import RunResult
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.base import BaseLLM
from antcrew.teams.base import InteractiveMixin

if TYPE_CHECKING:
    from antcrew.core.agent import BaseAgent
    from antcrew.integrations.telegram.integration import TelegramChannel
    from antcrew.memory.store import BaseMemory
    from antcrew.memory.repo_index import RepoIndex as _RepoIndexT
    from antcrew.sandbox.runner import SandboxRunner
    from antcrew.trace import TraceLog
    from langgraph.checkpoint.base import BaseCheckpointSaver

log = logging.getLogger(__name__)

_DEFAULT_FLOW = [
    ("business_analyst", "pm"),
    ("pm", "backend_dev"),
]


class DevTeam(InteractiveMixin):
    """
    Pre-built team template with three extensibility levels:

    Level 1 — defaults, no config:
        team = DevTeam()
        team.run("Build a login module")

    Level 2 — override individual agents (model, channel, approval_required):
        team = DevTeam(
            agents={
                "backend_dev": BackendDevAgent(
                    llm=OllamaModel("llama3"),
                    channel=ConsoleChannel(),
                    approval_required=True,
                )
            }
        )

    Level 3 — custom Supervisor with your own flow:
        supervisor = Supervisor(flow=[
            ("business_analyst", "pm"),
            ("pm", "backend_dev"),
            ("pm", "frontend_dev"),
            ("backend_dev", "qa"),
            ("frontend_dev", "qa"),
            ("qa", "reviewer",    when="no_critical_bugs"),
            ("qa", "backend_dev", when="has_critical_bugs"),
        ])
        team = DevTeam(supervisor=supervisor, agents={
            "frontend_dev": FrontendDevAgent(llm=...),
            "qa":           QAAgent(llm=...),
            "reviewer":     ReviewerAgent(llm=...),
        })
    """

    def __init__(
        self,
        model: Optional[BaseLLM] = None,
        integrations: Optional[list] = None,
        agents: Optional[dict[str, "BaseAgent"]] = None,
        supervisor: Optional[Supervisor] = None,
        memory: Optional["BaseMemory"] = None,
        repo_path: Optional[str] = None,
        runner: "Optional[SandboxRunner]" = None,
        checkpointer: "Optional[BaseCheckpointSaver]" = None,
        max_cost_usd: Optional[float] = None,
        trace_log: "Optional[TraceLog]" = None,
    ) -> None:
        self.llm = model or AnthropicModel()
        self.integrations: list = integrations or []
        self.memory = memory
        self._runner = runner
        self._checkpointer = checkpointer
        self._trace_log = trace_log
        if max_cost_usd is not None:
            self.llm.max_cost_usd = max_cost_usd

        defaults: dict[str, BaseAgent] = {
            "business_analyst": BusinessAnalystAgent(self.llm),
            "pm":               PMAgent(self.llm),
            "backend_dev":      BackendDevAgent(self.llm),
        }
        if agents:
            defaults.update(agents)
        self._agents: dict[str, BaseAgent] = defaults
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

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _initial_state(self, request: str) -> TeamState:
        return {
            "request": request,
            "messages": [{"role": "user", "content": request}],
            "prd": None,
            "tickets": None,
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

    def _get_telegram(self) -> "TelegramChannel":
        from antcrew.integrations.telegram.integration import TelegramChannel
        tg = next((i for i in self.integrations if isinstance(i, TelegramChannel)), None)
        if not tg:
            raise ValueError(
                "No TelegramChannel found. "
                "Add one via DevTeam(integrations=[TelegramChannel(...)])."
            )
        return tg

    # ------------------------------------------------------------------
    # Level 1 — automated run
    # ------------------------------------------------------------------

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Execute the full pipeline without human interaction."""
        if self.llm.max_cost_usd is not None:
            self.llm._cost_limit_offset = self.llm.get_usage_summary()["total_cost_usd"]
        _run_id: Optional[str] = None
        if self._trace_log is not None:
            _run_id = self._trace_log.begin_run(
                thread_id=thread_id, request=request, team=type(self).__name__,
            )
            self.llm.trace = self._trace_log
            self.llm._trace_run_id = _run_id
        try:
            app = self._supervisor.build(self._agents, checkpointer=self._checkpointer)
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
            cost = 0.0
            try:
                cost = (self.llm.get_usage_summary() or {}).get("total_cost_usd") or 0.0
            except Exception:
                pass
            if self._trace_log is not None and _run_id is not None:
                self._trace_log.end_run(_run_id, cost_usd=cost)
            return RunResult(state=state, thread_id=thread_id, cost_usd=cost)
        except Exception:
            if self._trace_log is not None and _run_id is not None:
                self._trace_log.end_run(_run_id, cost_usd=0.0, status="error")
            raise
        finally:
            if self._trace_log is not None:
                self.llm.trace = None
                self.llm._trace_run_id = None

    # run_interactive() and _apply_edit() come from InteractiveMixin

    # ------------------------------------------------------------------
    # Level 2 — Telegram HITL
    # ------------------------------------------------------------------

    def run_telegram(self) -> None:
        """Start the Telegram bot(s) and handle requests via Telegram (blocking)."""
        asyncio.run(self._run_telegram_async())

    async def _run_telegram_async(self) -> None:
        tg = self._get_telegram()
        apps = tg.build_apps()

        async def pipeline_handler(request: str, chat_id: int) -> None:
            session_id = str(chat_id)
            if tg.mode == "single":
                tg.set_chat_id(chat_id)
            try:
                await self._run_telegram_pipeline(request, session_id, tg)
            except Exception as exc:
                log.exception("Pipeline error for session %s", session_id)
                try:
                    await tg.send_status(f"💥 Pipeline error: {exc}")
                except Exception:
                    pass

        tg.set_pipeline_handler(apps, pipeline_handler)
        print(f"🤖 AntCrew Telegram bot{'s' if len(apps) > 1 else ''} starting. Ctrl+C to stop.")

        if len(apps) == 1:
            await apps[0].run_polling(close_loop=False)
        else:
            async with asyncio.TaskGroup() as tg_group:
                for app in apps:
                    tg_group.create_task(app.run_polling(close_loop=False))

    async def _run_telegram_pipeline(
        self, request: str, session_id: str, tg: "TelegramChannel"
    ) -> None:
        loop = asyncio.get_running_loop()

        interrupt_nodes = [
            name for name, agent in self._agents.items()
            if getattr(agent, "approval_required", False)
        ] or ["pm", "backend_dev"]

        from antcrew.core.graph import build_pipeline
        app = build_pipeline(
            list(self._agents.values()),
            interrupt_before=interrupt_nodes,
        )
        config = {"configurable": {"thread_id": session_id}}

        await tg.send_status("🚀 Pipeline starting…")
        await loop.run_in_executor(
            None, lambda: app.invoke(self._initial_state(request), config=config)
        )
        state = app.get_state(config).values

        result = await tg.send_for_review(state["prd"], "business_analyst", session_id)
        if result["decision"] == "reject":
            await tg.send_status("❌ Stopped after PRD review.", "business_analyst")
            return
        if result["decision"] == "edit" and result.get("edited"):
            try:
                app.update_state(config, {"prd": PRD.model_validate(json.loads(result["edited"]))})
            except Exception as exc:
                await tg.send_status(f"⚠️ Invalid JSON — keeping original PRD. ({exc})", "business_analyst")

        await tg.send_status("⚙️ PM generating tickets…", "pm")
        await loop.run_in_executor(None, lambda: app.invoke(None, config=config))
        state = app.get_state(config).values

        result = await tg.send_for_review(state["tickets"], "pm", session_id)
        if result["decision"] == "reject":
            await tg.send_status("❌ Stopped after Tickets review.", "pm")
            return
        if result["decision"] == "edit" and result.get("edited"):
            try:
                updated = [Ticket.model_validate(t) for t in json.loads(result["edited"])]
                app.update_state(config, {"tickets": updated})
            except Exception as exc:
                await tg.send_status(f"⚠️ Invalid JSON — keeping original tickets. ({exc})", "pm")

        await tg.send_status("💻 BackendDev writing code…", "backend_dev")
        await loop.run_in_executor(None, lambda: app.invoke(None, config=config))
        state = app.get_state(config).values

        await tg.send_code_artifacts(state.get("code_artifacts") or [], "backend_dev")
        await tg.send_status("✅ Pipeline complete!", "backend_dev")
