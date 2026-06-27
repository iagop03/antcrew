"""CustomTeam — a sequential (or mixed parallel) pipeline defined in YAML/dicts.

Runs a list of :class:`~antcrew.agents.template_agent.TemplateAgent` steps in
order.  Each step's output key is written into the shared state dict and is
available to all subsequent steps via their ``input_key``.

Steps can optionally be grouped under a ``parallel:`` key to run concurrently
using a thread pool — their outputs are merged into state once all complete.

Usage — Python (sequential)::

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

Usage — Python (with parallel group)::

    team = CustomTeam(
        steps=[
            {"name": "planner", "system_prompt": "Plan it.", "output_key": "plan"},
            {"parallel": [
                {"name": "backend",  "system_prompt": "Backend: {plan}",
                 "input_key": "plan", "output_key": "backend_code"},
                {"name": "frontend", "system_prompt": "Frontend: {plan}",
                 "input_key": "plan", "output_key": "frontend_code"},
            ]},
            {"name": "reviewer",
             "system_prompt": "Review: {backend_code} {frontend_code}",
             "output_key": "review"},
        ],
        llm=SimulatedLLM(),
    )

Usage — YAML (agentteam.yaml)::

    team: custom
    model: claude
    steps:
      - name: planner
        system_prompt: |
          You are a project planner.  Create a numbered step-by-step plan.
        output_key: plan
      - parallel:
        - name: backend
          system_prompt: "Write backend code for: {plan}"
          input_key: plan
          output_key: backend_code
        - name: frontend
          system_prompt: "Write frontend code for: {plan}"
          input_key: plan
          output_key: frontend_code
      - name: reviewer
        system_prompt: "Review: {backend_code} and {frontend_code}"
        output_key: review
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Optional

from antcrew.agents.template_agent import TemplateAgent
from antcrew.core.run_result import RunResult

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM
    from antcrew.trace import TraceLog

log = logging.getLogger(__name__)

# Each "step group" is a list of agents.  A group of 1 = sequential step;
# a group of N > 1 = parallel step (agents run concurrently).
_StepGroup = list[TemplateAgent]


def _parse_steps(raw_steps: list[Any], llm: "BaseLLM") -> list[_StepGroup]:
    """Convert raw step configs into ordered groups of TemplateAgent instances.

    A plain dict / str / Path → single-agent group (sequential).
    A dict with a ``"parallel"`` key → multi-agent group (concurrent).
    """
    groups: list[_StepGroup] = []
    for item in raw_steps:
        if isinstance(item, dict) and "parallel" in item:
            parallel_cfgs = item["parallel"]
            if not parallel_cfgs:
                raise ValueError("A 'parallel:' group must contain at least one step.")
            groups.append([TemplateAgent(cfg, llm) for cfg in parallel_cfgs])
        else:
            groups.append([TemplateAgent(item, llm)])
    return groups


class CustomTeam:
    """Sequential (or mixed parallel) pipeline of TemplateAgent steps.

    Each sequential step runs in declaration order.  A ``parallel`` group
    runs all its agents concurrently in a thread pool and merges their outputs
    before the next step begins.

    The shared *state* dict starts as ``{"request": request}`` and is updated
    after every step / group, so later agents can read prior outputs.

    Compatible with :class:`~antcrew.core.pipeline.Pipeline` (implements
    ``run(request, thread_id=…)`` and ``_initial_state(request)``).

    Args:
        steps:        Ordered list of step configs.  Each item is one of:

                      - A plain TemplateAgent config (dict / Path / YAML str)
                        → single sequential step.
                      - A dict ``{"parallel": [cfg, cfg, …]}``
                        → group of agents run concurrently.

        llm:          The LLM shared across all steps.
        max_cost_usd: Abort if cumulative LLM cost exceeds this budget.
        max_workers:  Thread-pool size for parallel groups (default: 4).
        trace_log:    Optional :class:`~antcrew.trace.TraceLog`.

    Raises:
        ValueError: If *steps* is empty or a parallel group is empty.
    """

    def __init__(
        self,
        steps: list[Any],
        llm: "BaseLLM",
        *,
        max_cost_usd: Optional[float] = None,
        max_workers: int = 4,
        trace_log: "Optional[TraceLog]" = None,
    ) -> None:
        if not steps:
            raise ValueError("CustomTeam requires at least one step.")

        self.llm = llm
        self._trace_log = trace_log
        self._checkpointer = None  # Pipeline carry-over compat
        self._max_workers = max_workers

        if max_cost_usd is not None:
            self.llm.max_cost_usd = max_cost_usd

        self._step_groups: list[_StepGroup] = _parse_steps(steps, llm)

        # Flat list kept for backward compatibility and easy introspection.
        self._agents: list[TemplateAgent] = [
            agent for group in self._step_groups for agent in group
        ]

    # ------------------------------------------------------------------
    # Pipeline / InteractiveMixin contract
    # ------------------------------------------------------------------

    def _initial_state(self, request: str) -> dict:
        return {"request": request}

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Run all step groups in order and return the merged state.

        Sequential groups run one after another.  Parallel groups run all
        their agents concurrently; outputs are merged once all finish.

        Args:
            request:   The task description — available to all steps as
                       ``state["request"]``.
            thread_id: Run identifier (TraceLog and Pipeline compatibility).

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
            for group in self._step_groups:
                if len(group) == 1:
                    agent = group[0]
                    log.debug("custom_team step=%s", agent.name)
                    state.update(agent.run(state))
                else:
                    state.update(self._run_parallel(group, state))

            cost = self.llm.get_usage_summary().get("total_cost_usd", 0.0)

            if self._trace_log is not None and _run_id:
                self._trace_log.end_run(_run_id, cost_usd=cost, status="done")

            return RunResult(state=state, thread_id=thread_id, cost_usd=cost)

        except Exception:
            if self._trace_log is not None and _run_id:
                self._trace_log.end_run(_run_id, status="error")
            raise

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_parallel(self, agents: list[TemplateAgent], state: dict) -> dict:
        """Run *agents* concurrently; merge and return their combined output."""
        # Each agent gets a snapshot of current state so reads don't race.
        snapshot = dict(state)
        merged: dict = {}
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(agents))) as pool:
            futures = {pool.submit(agent.run, snapshot): agent for agent in agents}
            for future in as_completed(futures):
                agent = futures[future]
                log.debug("custom_team parallel step=%s done", agent.name)
                merged.update(future.result())
        return merged
