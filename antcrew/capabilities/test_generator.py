from __future__ import annotations

from antcrew.engine import CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class TestGenerator(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "test_generator",
        description = "Generates test files for existing source artifacts.",
        needs       = frozenset([ConditionId("implementation_exists")]),
        produces    = frozenset([ConditionId("tests_exist")]),
        consumes    = frozenset(),
        emits       = frozenset(["test"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: enumerate source artifacts, call LLM per file, write test artifacts
        raise NotImplementedError
