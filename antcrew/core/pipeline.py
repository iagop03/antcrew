"""
Multi-team pipeline — chains teams sequentially, carrying artifacts forward.

Each team in the pipeline runs in order.  Artifacts produced by team N are
automatically injected into team N+1's initial state so subsequent teams can
build on previous output (e.g. a ResearchTeam feeding context to a DevTeam).

Usage::

    from antcrew import Pipeline, ResearchTeam, DevTeam

    pipeline = Pipeline([
        ResearchTeam(model=llm),
        DevTeam(model=llm),
    ])
    result = pipeline.run("Build an AI analytics platform")
    print(result.cost_usd)          # total across all teams
    print(result.state["prd"])      # produced by DevTeam
    print(result.state["research_document"])  # produced by ResearchTeam

Carry-over keys
---------------
The following state keys are forwarded automatically from each team's output
to the next team's initial state (all others reset to None / default):

    prd, tickets, code_artifacts, test_artifacts, review,
    devops_artifacts, doc_artifacts, research_document, content_piece

You can override this set via the ``carry_keys`` constructor argument.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from antcrew.core.events import bus, new_run_id
from antcrew.core.run_result import RunResult

if TYPE_CHECKING:
    from antcrew.teams.base import InteractiveMixin

log = logging.getLogger(__name__)

_DEFAULT_CARRY_KEYS: frozenset[str] = frozenset({
    "prd",
    "tickets",
    "code_artifacts",
    "test_artifacts",
    "review",
    "devops_artifacts",
    "doc_artifacts",
    "research_document",
    "content_piece",
})


class Pipeline:
    """Chains multiple teams sequentially with automatic state carry-over.

    Each team runs to completion before the next one starts.  Artifact keys
    (configurable via *carry_keys*) are forwarded from each team's output
    into the next team's initial state.

    Args:
        teams:      Ordered list of team instances (any class that implements
                    ``run(request, *, thread_id) -> RunResult`` and
                    ``_initial_state(request) -> dict``).
        carry_keys: Set of state keys to forward between teams.  Defaults to
                    the standard artifact keys (PRD, tickets, code, docs, …).

    Example::

        pipeline = Pipeline([ResearchTeam(llm), DevTeam(llm)])
        result = pipeline.run("Build an AI-powered search service")
    """

    def __init__(
        self,
        teams: list["InteractiveMixin"],
        *,
        carry_keys: Optional[frozenset[str]] = None,
    ) -> None:
        if not teams:
            raise ValueError("Pipeline requires at least one team.")
        self.teams = list(teams)
        self.carry_keys = carry_keys if carry_keys is not None else _DEFAULT_CARRY_KEYS

    def run(self, request: str, *, thread_id: str = "default") -> RunResult:
        """Run all teams in order, returning the final merged state.

        ``RunResult.cost_usd`` reflects the total spend across all teams.
        ``RunResult.state`` is the output of the last team, enriched with
        artifacts produced by earlier teams.
        """
        _run_id = new_run_id()
        team_names = [type(t).__name__ for t in self.teams]
        bus.emit("pipeline.start",
                 {"team": "Pipeline", "steps": team_names, "request": request},
                 run_id=_run_id, thread_id=thread_id)

        prev_state: Optional[dict] = None
        total_cost = 0.0
        final_state: dict = {}

        try:
            for idx, team in enumerate(self.teams):
                team_thread = f"{thread_id}-p{idx}"
                log.debug("pipeline team=%s idx=%d thread=%s", type(team).__name__, idx, team_thread)

                if prev_state is not None:
                    result = self._run_with_carry(team, request, prev_state, team_thread)
                else:
                    result = team.run(request, thread_id=team_thread)

                prev_state = dict(result.state)
                total_cost += result.cost_usd
                final_state = prev_state

            bus.emit("pipeline.end",
                     {"team": "Pipeline", "cost_usd": total_cost, "success": True},
                     run_id=_run_id, thread_id=thread_id)
            return RunResult(state=final_state, thread_id=thread_id, cost_usd=total_cost)
        except Exception:
            bus.emit("pipeline.end",
                     {"team": "Pipeline", "cost_usd": total_cost, "success": False},
                     run_id=_run_id, thread_id=thread_id)
            raise

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_with_carry(
        self,
        team: "InteractiveMixin",
        request: str,
        prev_state: dict,
        thread_id: str,
    ) -> RunResult:
        """Run *team* with carry-over state injected into its initial state."""
        original_fn = team._initial_state

        def _patched(req: str) -> dict:
            base = original_fn(req)
            for key in self.carry_keys:
                value = prev_state.get(key)
                if value is not None:
                    base[key] = value
            return base

        team._initial_state = _patched  # type: ignore[method-assign]
        try:
            return team.run(request, thread_id=thread_id)
        finally:
            team._initial_state = original_fn  # type: ignore[method-assign]
