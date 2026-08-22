"""CapabilityRegistry: the catalog of available Executors.

The Registry answers one question: given a gap (unsatisfied conditions),
which Executors are candidates?

It deliberately does NOT select — it returns all candidates.
The EngineLoop selects.  This decouples capability discovery from
execution policy, so the EngineLoop's decision logic can change
(cost-based, LLM-based, rule-based) without touching the registry.
"""
from __future__ import annotations

from .capability import CapabilityDescriptor, Executor
from .goal import ConditionId


class CapabilityRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, executor: Executor) -> "CapabilityRegistry":
        self._executors[executor.descriptor.name] = executor
        return self

    def candidates_for(self, gap: frozenset[ConditionId]) -> list[Executor]:
        """Return all executors that can address at least one condition in gap."""
        return [ex for ex in self._executors.values() if ex.descriptor.can_address(gap)]

    def get(self, name: str) -> Executor | None:
        return self._executors.get(name)

    def all(self) -> list[Executor]:
        return list(self._executors.values())

    def descriptors(self) -> list[CapabilityDescriptor]:
        return [ex.descriptor for ex in self._executors.values()]

    def __len__(self) -> int:
        return len(self._executors)

    def __repr__(self) -> str:
        names = list(self._executors.keys())
        return f"CapabilityRegistry({names})"
