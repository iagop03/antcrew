from __future__ import annotations

from antcrew.engine import ArtifactId, CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class TaskPlanner(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "task_planner",
        description = "Decomposes architecture into an ordered task graph.",
        needs       = frozenset([ConditionId("architecture_exists")]),
        produces    = frozenset([ConditionId("task_graph_exists")]),
        consumes    = frozenset([ArtifactId("architecture")]),
        emits       = frozenset(["task_graph"]),
        cost        = 1.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: read architecture artifact, call LLM, write task_graph artifact
        raise NotImplementedError
