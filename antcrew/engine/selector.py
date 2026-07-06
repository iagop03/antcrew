"""CapabilitySelector: pluggable strategy for picking an Executor from candidates.

The Operator calls selector.select() inside decide() — replacing the hard-coded
cheapest-first logic with a swappable policy.

Built-in selectors
------------------
CheapestFirst   — lowest descriptor.cost (default; deterministic)
FirstMatch      — first registered in the registry (FIFO)
MostProductive  — covers the most unsatisfied conditions in one shot
PrioritySelector — explicit name→int priority map; ties broken by cost

Custom selectors
----------------
Implement the CapabilitySelector Protocol — any object with a ``select`` method
and a ``name`` str attribute qualifies.  No inheritance required.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .capability import Executor
from .goal import Goal
from .state import ProjectState


@runtime_checkable
class CapabilitySelector(Protocol):
    name: str

    def select(
        self,
        candidates: list[Executor],
        state:      ProjectState,
        goal:       Goal,
    ) -> Executor | None: ...


class CheapestFirst:
    """Pick the executor with the lowest declared cost.  Deterministic."""
    name = "cheapest_first"

    def select(self, candidates: list[Executor], state: ProjectState, goal: Goal) -> Executor | None:
        if not candidates:
            return None
        return min(candidates, key=lambda e: e.descriptor.cost)


class FirstMatch:
    """Pick whichever candidate was registered first in the registry."""
    name = "first_match"

    def select(self, candidates: list[Executor], state: ProjectState, goal: Goal) -> Executor | None:
        return candidates[0] if candidates else None


class MostProductive:
    """Prefer the executor that satisfies the most unsatisfied conditions at once.

    Ties are broken by cost (cheaper wins).
    """
    name = "most_productive"

    def select(self, candidates: list[Executor], state: ProjectState, goal: Goal) -> Executor | None:
        if not candidates:
            return None
        gap = state.gap(goal.desired_state)
        return max(
            candidates,
            key=lambda e: (len(e.descriptor.produces & gap), -e.descriptor.cost),
        )


class PrioritySelector:
    """Pick by explicit name→priority mapping.  Higher int = preferred.

    Executors not in the map get priority 0.  Ties are broken by cost.
    """
    name = "priority"

    def __init__(self, priorities: dict[str, int]) -> None:
        self._p = priorities

    def select(self, candidates: list[Executor], state: ProjectState, goal: Goal) -> Executor | None:
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda e: (self._p.get(e.descriptor.name, 0), -e.descriptor.cost),
        )
