from __future__ import annotations

from antcrew.engine import CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class SpecExtractor(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "spec_extractor",
        description = "Writes requirements artifact from goal description and constraints.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("requirements_exists")]),
        consumes    = frozenset(),
        emits       = frozenset(["requirements"]),
        cost        = 1.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: call LLM to generate requirements from goal.description + goal.constraints
        raise NotImplementedError
