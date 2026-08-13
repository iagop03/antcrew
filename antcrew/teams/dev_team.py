from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from antcrew.agents.backend_dev import BackendDevAgent
from antcrew.agents.business import BusinessAnalystAgent
from antcrew.agents.coherence import CoherenceAgent
from antcrew.agents.pm import PMAgent
from antcrew.core.artifacts import PRD, Ticket
from antcrew.core.run_result import RunResult
from antcrew.core.state import TeamState
from antcrew.core.supervisor import Supervisor
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.base import BaseLLM
from antcrew.teams.base import InteractiveMixin


class _KBProxy:
    """Injects a per-agent _kb_context into state before delegating to agent.run().

    DevTeam builds one proxy per agent when a ProjectKB is configured. Each
    proxy holds the context string appropriate for that agent's role so that
    each pipeline step sees exactly the KB sections it needs — not a one-size
    "backend_dev" context shared by all.

    Exposes only the two attributes Supervisor.build() reads: ``run`` and
    ``approval_required``.
    """

    __slots__ = ("_agent", "_kb_ctx")

    def __init__(self, agent, kb_ctx: str) -> None:
        self._agent = agent
        self._kb_ctx = kb_ctx

    @property
    def approval_required(self) -> bool:
        return bool(getattr(self._agent, "approval_required", False))

    @property
    def hitl_channel(self) -> str:
        return getattr(self._agent, "hitl_channel", "default")

    @property
    def feedback_schema(self):
        return getattr(self._agent, "feedback_schema", None)

    def run(self, state: "TeamState") -> dict:
        return self._agent.run({**state, "_kb_context": self._kb_ctx})


if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from antcrew.core.agent import BaseAgent
    from antcrew.integrations.telegram.integration import TelegramChannel
    from antcrew.memory.repo_index import RepoIndex as _RepoIndexT
    from antcrew.memory.store import BaseMemory
    from antcrew.sandbox.runner import SandboxRunner
    from antcrew.trace import TraceLog

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
        self._project_kb = None
        if project_kb_path:
            from antcrew.core.project_kb import ProjectKB
            self._project_kb = ProjectKB.load(project_kb_path)
            self._project_kb._path = Path(project_kb_path)
        self._trace_log = trace_log
        if max_cost_usd is not None:
            self.llm.max_cost_usd = max_cost_usd

        _am = agent_models or {}
        defaults: dict[str, BaseAgent] = {
            "business_analyst": BusinessAnalystAgent(_am.get("business_analyst", self.llm)),
            "pm":               PMAgent(_am.get("pm", self.llm)),
            "backend_dev":      BackendDevAgent(_am.get("backend_dev", self.llm)),
            "coherence":        CoherenceAgent(_am.get("coherence", self.llm)),
        }
        if agents:
            defaults.update(agents)
        self._agents: dict[str, BaseAgent] = defaults
        self._supervisor = supervisor or Supervisor(flow=_DEFAULT_FLOW)

        if memory:
            for agent in self._agents.values():
                agent.memory = memory

        self.repo_index: "Optional[_RepoIndexT]" = None
        self.symbol_index = None
        if repo_path:
            from antcrew.core.symbol_index import SymbolIndex
            from antcrew.memory.repo_index import RepoIndex
            self.repo_index = RepoIndex(repo_path)
            self.repo_index.build()
            self.symbol_index = SymbolIndex.build([repo_path])
            for agent in self._agents.values():
                agent.repo_index = self.repo_index
                agent.symbol_index = self.symbol_index

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
            "_kb_context": "",
            "_memory_context": "",
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
    # Knowledge-base helpers
    # ------------------------------------------------------------------

    @property
    def kb(self):
        """Return the ProjectKB instance, or None if not configured."""
        return self._project_kb

    def record_decision(
        self,
        decision: str,
        *,
        rationale: str = "",
        date: str = "",
        status="accepted",
        tags=None,
    ):
        """Convenience shorthand for ``team.kb.record_decision(...); team.kb.save()``."""
        if self._project_kb is None:
            raise RuntimeError("No project_kb_path configured on this team.")
        rec = self._project_kb.record_decision(
            decision, rationale=rationale, date=date, status=status, tags=tags,
        )
        self._project_kb.save()
        return rec

    def _apply_org_context(self, ctx: dict) -> None:
        """Merge *ctx* into the live ProjectKB (does not save — run() saves at end)."""
        kb = self._project_kb
        if not kb:
            return
        for item in ctx.get("decisions") or []:
            if isinstance(item, dict) and "decision" in item:
                kb.record_decision(
                    item["decision"],
                    rationale=item.get("rationale", ""),
                    date=item.get("date", ""),
                    status=item.get("status", "accepted"),
                    tags=item.get("tags") or [],
                )
        if stack := ctx.get("tech_stack"):
            kb.set_tech_stack(list(kb.tech_stack) + list(stack))
        if deps := ctx.get("dependencies"):
            kb.update_dependencies(deps)

    # ------------------------------------------------------------------
    # Level 1 — automated run
    # ------------------------------------------------------------------

    def run(
        self,
        request: str,
        *,
        thread_id: str = "default",
        org_context: "Optional[dict]" = None,
        dry_run: bool = False,
        replay_run_id: "Optional[str]" = None,
    ) -> RunResult:
        """Execute the full pipeline without human interaction.

        *org_context* pre-populates the ProjectKB before running.  Accepted keys:
        ``"decisions"``, ``"tech_stack"``, ``"dependencies"``.

        *dry_run=True* runs all LLM agents normally but suppresses side effects:
        no file writes (``write_back``), no test execution (``SandboxRunner``),
        and no git/GitHub integration actions.  The returned state contains all
        artifacts; integrations see ``state["_dry_run"] = True`` and should
        no-op their external calls.

        *replay_run_id* injects the exact artifacts of a past run as context for
        BA and PM.  Requires ``memory`` to be configured.  Complements the
        automatic semantic search already done via ``_recall()``; use this when
        you want precision (a specific run) rather than breadth (fuzzy search).
        """
        if org_context and self._project_kb:
            self._apply_org_context(org_context)
        from antcrew.core.events import Event, bus, new_run_id
        _llms = self._unique_llms()
        if self.llm.max_cost_usd is not None:
            self.llm._cost_limit_offset = self.llm.get_usage_summary()["total_cost_usd"]
        _run_id: Optional[str] = new_run_id()
        _trace_run_id: Optional[str] = None
        if self._trace_log is not None:
            _trace_run_id = self._trace_log.begin_run(
                thread_id=thread_id, request=request, team=type(self).__name__,
            )
            for _llm in _llms:
                _llm.trace = self._trace_log
                _llm._trace_run_id = _trace_run_id
        bus.emit(Event(
            "pipeline.start",
            {"request": request, "team": type(self).__name__},
            run_id=_run_id,
            thread_id=thread_id,
        ))
        try:
            app = self._supervisor.build(self._build_agent_map(), checkpointer=self._checkpointer)
            config = {"configurable": {"thread_id": thread_id}}
            initial = self._initial_state(request)
            initial["_run_id"] = _run_id
            initial["_thread_id"] = thread_id
            if dry_run:
                initial["_dry_run"] = True
            if replay_run_id and self.memory:
                try:
                    snap = self.memory.retrieve_run(replay_run_id)
                    initial["_memory_context"] = snap.as_context()
                except Exception as exc:
                    log.warning("replay_run_id %s: retrieve failed — %s", replay_run_id, exc)
            state = app.invoke(initial, config=config)
            if self._project_kb:
                try:
                    self._project_kb.update_from_state(state)
                    # Auto-record persistent feedback failures as rejected decisions.
                    feedback_error = (state.get("_feedback_error") or "").strip()
                    if feedback_error and not state.get("feedback_ok"):
                        self._project_kb.record_decision(
                            feedback_error[:200],
                            rationale="Auto-recorded: feedback loop exhausted all retries",
                            status="rejected",
                            tags=["auto", "feedback-failure"],
                        )
                    self._project_kb.save()
                    bus.emit(Event(
                        "kb.updated",
                        {"summary": self._project_kb.summary()},
                        run_id=_run_id,
                        thread_id=thread_id,
                    ))
                except Exception as exc:
                    log.warning("ProjectKB update failed: %s", exc)
            if self._enable_coherence and "coherence" in self._agents and state.get("code_artifacts"):
                try:
                    import time as _time
                    bus.emit(Event("agent.start", {"agent_name": "coherence"},
                                  run_id=_run_id, thread_id=thread_id))
                    _t0 = _time.monotonic()
                    coherence_result = self._agents["coherence"].run(state)
                    state.update(coherence_result)
                    _dur = round(_time.monotonic() - _t0, 3)
                    files_corrected = len(state.get("coherence_issues") or [])
                    bus.emit(Event("agent.end",
                                  {"agent_name": "coherence", "duration_s": _dur,
                                   "produced_keys": ["coherence_issues"]},
                                  run_id=_run_id, thread_id=thread_id))
                    bus.emit(Event(
                        "coherence.run",
                        {"files_corrected": files_corrected},
                        run_id=_run_id,
                        thread_id=thread_id,
                    ))
                except Exception as exc:
                    log.warning("CoherenceAgent failed: %s", exc)
            if self.memory:
                self.memory.store_run(state, run_id=_run_id, project=thread_id)
            if not dry_run and self._work_dir and state.get("code_artifacts"):
                try:
                    from antcrew.core.writeback import write_back
                    wb = write_back(state, project_root=Path(self._work_dir), yes=True)
                    if wb.total_written:
                        bus.emit(Event(
                            "writeback.done",
                            {"written": wb.total_written, "project_root": str(wb.project_root)},
                            run_id=_run_id,
                            thread_id=thread_id,
                        ))
                        log.info("write_back: %d file(s) written to %s", wb.total_written, wb.project_root)
                except Exception as exc:
                    log.warning("write_back failed: %s", exc)
            if not dry_run and self._runner and state.get("test_artifacts"):
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
            if self._trace_log is not None:
                self._trace_log.end_run(_trace_run_id, cost_usd=cost)
            artifact_summary = {
                k: len(v) for k in (
                    "code_artifacts", "test_artifacts", "devops_artifacts", "doc_artifacts"
                ) if (v := state.get(k))
            }
            bus.emit(Event(
                "pipeline.end",
                {"cost_usd": cost, "success": not state.get("errors"), "artifact_summary": artifact_summary,
                 "model": getattr(self.llm, "model", None)},
                run_id=_run_id,
                thread_id=thread_id,
            ))
            return RunResult(state=state, thread_id=thread_id, cost_usd=cost)
        except Exception:
            if self._trace_log is not None:
                self._trace_log.end_run(_trace_run_id, cost_usd=0.0, status="error")
            bus.emit(Event(
                "pipeline.end",
                {"cost_usd": 0.0, "success": False, "artifact_summary": {},
                 "model": getattr(self.llm, "model", None)},
                run_id=_run_id,
                thread_id=thread_id,
            ))
            raise
        finally:
            if self._trace_log is not None:
                for _llm in _llms:
                    _llm.trace = None
                    _llm._trace_run_id = None

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
