"""Capability: a unit of project transformation.

A Capability has two parts, kept deliberately separate:

  CapabilityDescriptor — the contract.
    Declares what conditions must hold before execution (needs),
    what conditions will hold after (produces), and what artifacts
    it reads/writes.  The Operator and Registry reason over descriptors.
    No implementation detail leaks here.

  Executor — the implementation.
    Runs the capability against the ArtifactStore and returns a
    CapabilityResult.  An Executor is swappable: ClaudeExecutor,
    OpenAIExecutor, LocalExecutor, TemplateExecutor can all back
    the same descriptor.

CapabilityResult wraps the ArtifactDelta (what changed) together
with execution metadata.  The Operator applies the delta to the Store;
it never interprets the delta's contents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .artifact import ArtifactDelta, EMPTY_DELTA
from .goal import ConditionId

if TYPE_CHECKING:
    from .goal import Goal
    from .store import ArtifactStore


@dataclass(frozen=True)
class CapabilityDescriptor:
    name:        str
    description: str
    needs:       frozenset[ConditionId]  # conditions that must be satisfied to run
    produces:    frozenset[ConditionId]  # conditions this capability can satisfy
    emits:       frozenset[str]          # artifact kinds written (e.g. "source", "test")
    cost:        float = 1.0             # relative cost for operator prioritization
    tags:        frozenset[str] = field(default_factory=frozenset)

    def can_address(self, gap: frozenset[ConditionId]) -> bool:
        return bool(self.produces & gap)


@dataclass
class CapabilityResult:
    delta:          ArtifactDelta        = field(default_factory=lambda: EMPTY_DELTA)
    metrics:        dict[str, float]     = field(default_factory=dict)
    warnings:       list[str]            = field(default_factory=list)
    errors:         list[str]            = field(default_factory=list)
    execution_time: float                = 0.0
    cost_usd:       float                = 0.0  # LLM cost for this capability run

    @property
    def succeeded(self) -> bool:
        return not self.errors


@runtime_checkable
class Executor(Protocol):
    """An Executor implements one CapabilityDescriptor.

    Multiple Executors can back the same descriptor (different providers).
    The engine only depends on this Protocol — never on concrete classes.
    """

    descriptor: CapabilityDescriptor

    def execute(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult: ...
