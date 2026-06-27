"""CustomTeam — a sequential pipeline defined entirely in YAML/dicts.

Runs a list of :class:`~antcrew.agents.template_agent.TemplateAgent` steps in
order.  Each step's output key is written into the shared state dict and is
available to all subsequent steps via their ``input_key``.

Usage — Python::

    from antcrew.teams.custom_team import CustomTeam
    from antcrew.models.simulated import SimulatedLLM

    team = CustomTeam(
        steps=[
            {"name": "planner",  "system_prompt": "Plan the task.",  "output_key": "plan"},
            {"name": "executor", "system_prompt": "Execute: {plan}", "input_key": "plan",
             "output_key": "result"},
        ],
        llm=SimulatedLLM(),
    )
    result = team.run("Build a login module")
    print(result["result"])

Usage — YAML (agentteam.yaml)::

    team: custom
    model: claude
    steps:
      - name: planner
        system_prompt: |
          You are a project planner.  Create a numbered step-by-step plan.
        output_key: plan
      - name: executor
        system_prompt: |
          You are a senior developer.  Implement the following plan:
          {plan}
        input_key: plan
        output_key: result
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from antcrew.agents.template_agent import TemplateAgent
from antcrew.core.run_result import RunResult

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM
    from antcrew.trace import TraceLog

log = logging.getLogger(__name__)


class CustomTeam:
    """Sequential pipeline of :class:`~antcrew.agents.template_agent.TemplateAgent` steps.

    Each step runs in declaration order.  The shared *state* dict starts as
    ``{"request": request}`` and is updated after every step, so later agents
    can read outputs produced by earlier ones via their ``input_key``.

    Compatible with :class:`~antcrew.core.pipeline.Pipeline` (implements
    ``run(request, thread_id=…)`` and ``_initial_state(request)``).

    Args:
        steps:       Ordered list of TemplateAgent configs — each item may be a
                     dict, a file :class:`~pathlib.Path`, or raw YAML text.
        llm:         The LLM shared across all steps (individual steps cannot
                     override the model — use a :class:`Pipeline` of teams for
                     that level of control).
        max_cost_usd: Abort if the cumulative LLM cost exceeds this budget.
        trace_log:   Optional :class:`~antcrew.trace.TraceLog` for recording
                     per-step timing and token counts.

    Raises:
        ValueError: If *steps* is empty or any step config is invalid.
    """

    def __init__(
        self,
        steps: list[Any],
        llm: "BaseLLM",
        *,
        max_cost_usd: Optional[float] = None,
        trace_log: "Optional[TraceLog]" = None,
    ) -> None:
        if not steps:
            raise ValueError("CustomTeam requires at least one step.")

        self.llm = llm
        self._trace_log = trace_log
        self._checkpointer = None  # Pipeline carry-over compat

        if max_cost_usd is not None:
            self.llm.max_cost_usd = max_cost_usd

        self._agents: list[TemplateAgent] = [
            TemplateAgent(step, llm) for step in steps
        ]

    # ------------------------------------------------------------------
    # Pipeline / InteractiveMixin contract
    # ------------------------------------------------------------------

    def _initial_state(self, request: str) -> dict:
        return {"request": request}

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Run all steps sequentially and return the merged state.

        Args:
            request:   The user request / task description.  Written into
                       ``state["request"]`` and available to all steps.
            thread_id: Identifier for this run (used in TraceLog and for
                       compatibility with :class:`~antcrew.core.pipeline.Pipeline`).

        Returns:
            :class:`~antcrew.core.run_result.RunResult` with the final
            accumulated state, thread_id, and total LLM cost.
        """
        state: dict = self._initial_state(request)
        _run_id: Optional[str] = None

        if self._trace_log is not None:
            _run_id = self._trace_log.begin_run(
                thread_id=thread_id,
                request=request,
                team=type(self).__name__,
            )
            self.llm.trace = self._trace_log
            self.llm._trace_run_id = _run_id

        try:
            for agent in self._agents:
                log.debug("custom_team step=%s", agent.name)
                output = agent.run(state)
                state.update(output)

            cost = self.llm.get_usage_summary().get("total_cost_usd", 0.0)

            if self._trace_log is not None and _run_id:
                self._trace_log.end_run(_run_id, cost_usd=cost, status="done")

            return RunResult(state=state, thread_id=thread_id, cost_usd=cost)

        except Exception:
            if self._trace_log is not None and _run_id:
                self._trace_log.end_run(_run_id, status="error")
            raise
