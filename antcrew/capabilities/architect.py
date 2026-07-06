from __future__ import annotations

from antcrew.engine import ArtifactId, CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class Architect(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "architect",
        description = "Produces architecture artifact from requirements.",
        needs       = frozenset([ConditionId("requirements_exists")]),
        produces    = frozenset([ConditionId("architecture_exists")]),
        consumes    = frozenset([ArtifactId("requirements")]),
        emits       = frozenset(["architecture"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: read requirements artifact, call LLM, write architecture artifact
        raise NotImplementedError
