"""BaseExecutor: LLM-aware base class for capability implementations.

Subclass this to implement a Capability that uses an LLM.
The base class handles:
  - LLM injection (provider-agnostic via antcrew.models.BaseLLM)
  - Execution timing (written into CapabilityResult.execution_time)
  - Error capture (exceptions → CapabilityResult.errors, never re-raised)

The public Executor Protocol never leaks model names or provider details.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from antcrew.engine import CapabilityResult, EMPTY_DELTA

if TYPE_CHECKING:
    from antcrew.engine import ArtifactStore, Goal
    from antcrew.models.base import BaseLLM


class BaseExecutor:
    """Convenience base for LLM-backed capability executors.

    Subclasses must define:
        descriptor: CapabilityDescriptor          (class attribute)
        _run(store, goal) -> CapabilityResult     (override, may raise)

    Subclasses that don't need an LLM (e.g. TestRunner) may leave
    llm=None and must not call _call().
    """

    def __init__(self, llm: "Optional[BaseLLM]" = None) -> None:
        self._llm = llm

    def _call(self, system: str, user: str) -> str:
        """Call the injected LLM with a system + user prompt pair."""
        if self._llm is None:
            raise RuntimeError(
                f"{type(self).__name__} requires an LLM but none was injected. "
                "Pass llm=... to the constructor."
            )
        return self._llm.system(system, user)

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
