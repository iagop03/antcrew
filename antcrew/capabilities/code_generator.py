from __future__ import annotations

from antcrew.engine import ArtifactId, CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class CodeGenerator(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "code_generator",
        description = "Implements the next pending task from the task graph.",
        needs       = frozenset([
            ConditionId("task_graph_exists"),
            ConditionId("architecture_exists"),
        ]),
        produces    = frozenset([ConditionId("implementation_exists")]),
        consumes    = frozenset([ArtifactId("task_graph"), ArtifactId("architecture")]),
        emits       = frozenset(["source"]),
        cost        = 2.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: read next pending task, call LLM, write source artifacts
        raise NotImplementedError
