"""CustomTeam — sequential / parallel pipeline defined in YAML/dicts.

Runs a list of :class:`~antcrew.agents.template_agent.TemplateAgent` steps in
order.  Each step's output key is written into the shared state dict and is
available to all subsequent steps via their ``input_key``.

Steps can optionally be grouped under a ``parallel:`` key to run concurrently.
Individual steps can declare ``max_retries`` and ``retry_delay`` to tolerate
transient LLM failures.

Usage — Python (sequential with retries)::

    from antcrew.teams.custom_team import CustomTeam
    from antcrew.models.simulated import SimulatedLLM

    team = CustomTeam(
        steps=[
            {"name": "planner",  "system_prompt": "Plan the task.",
             "output_key": "plan", "max_retries": 2, "retry_delay": 1.0},
            {"name": "executor", "system_prompt": "Execute: {plan}",
             "input_key": "plan", "output_key": "result"},
        ],
        llm=SimulatedLLM(),
    )
    result = team.run("Build a login module")

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
        ],
        llm=SimulatedLLM(),
    )

Usage — YAML (agentteam.yaml)::

    team: custom
    model: claude
    steps:
      - name: planner
        system_prompt: |
          You are a project planner. Create a numbered plan.
        output_key: plan
        max_retries: 2       # retry up to 2 times on LLM error
        retry_delay: 1.0     # seconds between retries
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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from antcrew.agents.template_agent import TemplateAgent
from antcrew.core.run_result import RunResult

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM
    from antcrew.trace import TraceLog

log = logging.getLogger(__name__)

# Keys consumed by CustomTeam itself — not forwarded to TemplateAgent config.
_TEAM_KEYS = frozenset({"max_retries", "retry_delay", "parallel", "condition"})


@dataclass
class _Step:
    """One executable step: agent + retry policy + run condition."""
    agent: TemplateAgent
    max_retries: int = 0
    retry_delay: float = 0.0
    # condition: one state key (str) or a list of keys (all must be truthy).
    # None → always run.
    condition: "Optional[list[str]]" = None


# A step group is a list of Steps.  len==1 → sequential; len>1 → parallel.
_StepGroup = list[_Step]


def _agent_cfg(raw: dict) -> dict:
    """Strip team-level keys before passing a config dict to TemplateAgent."""
    return {k: v for k, v in raw.items() if k not in _TEAM_KEYS}


def _parse_condition(raw: "Any") -> "Optional[list[str]]":
    """Normalise the raw 'condition' value to a list of key names or None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        if not raw:
            return None
        return [str(k) for k in raw]
    raise ValueError(
        f"'condition' must be a key name (str) or a list of key names; got {type(raw).__name__}"
    )


def _make_step(raw: Any, llm: "BaseLLM") -> _Step:
    """Build one _Step from a raw config (dict / Path / YAML str)."""
    max_retries = 0
    retry_delay = 0.0
    condition = None
    if isinstance(raw, dict):
        max_retries = int(raw.get("max_retries", 0))
        retry_delay = float(raw.get("retry_delay", 0.0))
        condition = _parse_condition(raw.get("condition"))
        raw = _agent_cfg(raw)
    return _Step(
        agent=TemplateAgent(raw, llm),
        max_retries=max_retries,
        retry_delay=retry_delay,
        condition=condition,
    )


def _parse_steps(raw_steps: list[Any], llm: "BaseLLM") -> list[_StepGroup]:
    """Convert raw step configs into ordered groups of _Step instances.

    A plain dict / str / Path  →  single-step group (sequential).
    ``{"parallel": [cfg, …]}``  →  multi-step group (concurrent).
    """
    groups: list[_StepGroup] = []
    for item in raw_steps:
        if isinstance(item, dict) and "parallel" in item:
            parallel_cfgs = item["parallel"]
            if not parallel_cfgs:
                raise ValueError("A 'parallel:' group must contain at least one step.")
            groups.append([_make_step(cfg, llm) for cfg in parallel_cfgs])
        else:
            groups.append([_make_step(item, llm)])
    return groups


def _condition_met(step: _Step, state: dict) -> bool:
    """Return True if the step's condition is satisfied (or there is none)."""
    if step.condition is None:
        return True
    return all(bool(state.get(key)) for key in step.condition)


def _run_step(step: _Step, state: dict) -> dict:
    """Run *step* with its retry policy; raise on final failure."""
    attempts = step.max_retries + 1
    last_exc: Exception = RuntimeError("unreachable")
    for attempt in range(attempts):
        try:
            return step.agent.run(state)
        except Exception as exc:
            last_exc = exc
            log.warning(
                "custom_team step=%s attempt %d/%d failed: %s",
                step.agent.name, attempt + 1, attempts, exc,
            )
            if attempt < attempts - 1 and step.retry_delay > 0:
                time.sleep(step.retry_delay)
    raise last_exc


class CustomTeam:
    """Sequential / parallel pipeline of TemplateAgent steps.

    Each sequential step runs in declaration order.  A ``parallel`` group
    runs all its agents concurrently in a thread pool and merges their outputs
    before the next step begins.  Every step supports ``max_retries`` and
    ``retry_delay`` to tolerate transient LLM errors.

    Compatible with :class:`~antcrew.core.pipeline.Pipeline` (implements
    ``run(request, thread_id=…)`` and ``_initial_state(request)``).

    Args:
        steps:        Ordered list of step configs.  Each item is one of:

                      - A plain TemplateAgent config (dict / Path / YAML str).
                        Optional keys: ``max_retries`` (int, default 0),
                        ``retry_delay`` (float seconds, default 0).
                      - A dict ``{"parallel": [cfg, …]}`` — runs agents
                        concurrently.  Retry keys are per-cfg inside the list.

        llm:          The LLM shared across all steps.
        max_cost_usd: Abort if cumulative LLM cost exceeds this budget.
        max_workers:  Thread-pool size for parallel groups (default: 4).
        trace_log:    Optional :class:`~antcrew.trace.TraceLog`.

    Raises:
        ValueError: If *steps* is empty or a ``parallel:`` group is empty.
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

        # Flat list for backward compatibility and easy introspection.
        self._agents: list[TemplateAgent] = [
            s.agent for group in self._step_groups for s in group
        ]

    # ------------------------------------------------------------------
    # Pipeline / InteractiveMixin contract
    # ------------------------------------------------------------------

    def _initial_state(self, request: str) -> dict:
        return {"request": request}

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Run all step groups in order and return the merged state.

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
                    step = group[0]
                    if not _condition_met(step, state):
                        log.debug("custom_team step=%s skipped (condition not met)", step.agent.name)
                        continue
                    log.debug("custom_team step=%s", step.agent.name)
                    state.update(_run_step(step, state))
                else:
                    # For parallel groups, filter to steps whose condition is met.
                    runnable = [s for s in group if _condition_met(s, state)]
                    skipped = [s for s in group if not _condition_met(s, state)]
                    for s in skipped:
                        log.debug("custom_team parallel step=%s skipped (condition not met)", s.agent.name)
                    if runnable:
                        state.update(self._run_parallel(runnable, state))

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

    def _run_parallel(self, group: _StepGroup, state: dict) -> dict:
        """Run all steps in *group* concurrently; merge and return outputs."""
        snapshot = dict(state)
        merged: dict = {}
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(group))) as pool:
            futures = {pool.submit(_run_step, step, snapshot): step for step in group}
            for future in as_completed(futures):
                step = futures[future]
                log.debug("custom_team parallel step=%s done", step.agent.name)
                merged.update(future.result())
        return merged
