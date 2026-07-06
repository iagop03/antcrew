from __future__ import annotations

from antcrew.engine import CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class TestRunner(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "test_runner",
        description = "Runs the test suite and produces a test report.",
        needs       = frozenset([ConditionId("tests_exist")]),
        produces    = frozenset([ConditionId("tests_pass")]),
        consumes    = frozenset(),
        emits       = frozenset(["report"]),
        cost        = 0.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: subprocess pytest, parse output, write test_report artifact
        raise NotImplementedError
