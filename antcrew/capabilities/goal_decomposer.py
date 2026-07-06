from __future__ import annotations

from antcrew.engine import CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class GoalDecomposer(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "goal_decomposer",
        description = "Translates a natural-language goal into a structured DesiredProjectState.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("goal_decomposed")]),
        consumes    = frozenset(),
        emits       = frozenset(["generic"]),
        cost        = 1.0,
        tags        = frozenset(["planning"]),
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: call LLM to decompose goal.description into conditions + constraints
        raise NotImplementedError
