"""BaseExecutor: LLM-aware base class for capability implementations.

Subclass this to implement a Capability that needs an LLM.
The base class handles timing and error capture — it never calls an LLM
directly; subclasses decide which provider to use in _run().
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from antcrew.engine import CapabilityResult, EMPTY_DELTA

if TYPE_CHECKING:
    from antcrew.engine import ArtifactStore, Goal


class BaseExecutor:
    """Convenience base for capability executors.

    Subclasses must define:
        descriptor: CapabilityDescriptor   (class attribute)
        _run(store, goal) -> CapabilityResult   (override, may raise)
    """

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        raise NotImplementedError(f"{type(self).__name__}._run() not implemented")

    def execute(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        t0 = time.monotonic()
        try:
            result = self._run(store, goal)
            result.execution_time = time.monotonic() - t0
            return result
        except Exception as exc:
            return CapabilityResult(
                delta=EMPTY_DELTA,
                errors=[f"{type(exc).__name__}: {exc}"],
                execution_time=time.monotonic() - t0,
            )
