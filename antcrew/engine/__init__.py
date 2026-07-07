"""antcrew.engine — capability-driven project execution engine.

The engine builds software by iterating over a goal until the project
reaches a desired state.  It never assumes a fixed pipeline of agents
or roles — it discovers what to do next from the artifact state.

Public API
----------
Types:
    Artifact, ArtifactId, ArtifactKind, ArtifactDelta, EMPTY_DELTA
    ArtifactStore, MemoryStore
    Condition, ConditionId, DesiredProjectState, Constraints, Goal
    ProjectState
    CapabilityDescriptor, CapabilityResult, Executor
    ValidatorResult, Validator
    CapabilityRegistry
    Event, EventLog  (+ all Event subclasses)
    Operator, OperatorError

Entry point:
    Operator.run(store, goal) → ProjectState
"""
from .artifact import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    EMPTY_DELTA,
)
from .capability import CapabilityDescriptor, CapabilityResult, Executor
from .events import (
    CapabilityCompleted,
    CapabilityDispatched,
    ConditionInvalidated,
    ConditionSatisfied,
    EngineError,
    EngineFinished,
    EngineStarted,
    Event,
    EventLog,
    OperatorDecision,
    StateObserved,
)
from .goal import Condition, ConditionId, Constraints, DesiredProjectState, Goal
from .operator import Operator, OperatorError
from .registry import CapabilityRegistry
from .selector import (
    CapabilitySelector, CheapestFirst, FirstMatch, MostProductive, PrioritySelector,
)
from .state import ProjectState
from .store import ArtifactStore, MemoryStore, FilesystemStore
from .validator import Validator, ValidatorResult
from .bus_bridge import EventBusBridge

__all__ = [
    # artifact
    "Artifact", "ArtifactId", "ArtifactKind", "ArtifactDelta", "EMPTY_DELTA",
    # store
    "ArtifactStore", "MemoryStore", "FilesystemStore",
    # goal
    "Condition", "ConditionId", "DesiredProjectState", "Constraints", "Goal",
    # state
    "ProjectState",
    # capability
    "CapabilityDescriptor", "CapabilityResult", "Executor",
    # validator
    "Validator", "ValidatorResult",
    # registry
    "CapabilityRegistry",
    # events
    "Event", "EventLog",
    "EngineStarted", "EngineFinished", "EngineError",
    "StateObserved", "CapabilityDispatched", "CapabilityCompleted",
    "ConditionSatisfied", "ConditionInvalidated", "OperatorDecision",
    # operator
    "Operator", "OperatorError",
    # selector
    "CapabilitySelector", "CheapestFirst", "FirstMatch", "MostProductive", "PrioritySelector",
    # platform bridge
    "EventBusBridge",
]
