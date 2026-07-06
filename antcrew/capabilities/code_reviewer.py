from __future__ import annotations

from antcrew.engine import CapabilityDescriptor, CapabilityResult, ConditionId
from .base import BaseExecutor


class CodeReviewer(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "code_reviewer",
        description = "Reviews source artifacts and produces a structured review report.",
        needs       = frozenset([
            ConditionId("implementation_exists"),
            ConditionId("tests_pass"),
        ]),
        produces    = frozenset([ConditionId("code_reviewed")]),
        consumes    = frozenset(),
        emits       = frozenset(["report"]),
        cost        = 2.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        # TODO: read source + architecture artifacts, call LLM, write review_report artifact
        raise NotImplementedError
