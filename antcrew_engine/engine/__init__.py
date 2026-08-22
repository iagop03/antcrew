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
    EngineLoop, EngineLoopError
    HitlReviewRequest, HitlDecision, ReviewChannel  (HITL contract)

Entry point:
    EngineLoop.run(store, goal) → ProjectState
"""
from . import sandbox
from .artifact import (
    EMPTY_DELTA,
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
)
from .bus_bridge import EventBusBridge
from .capability import CapabilityDescriptor, CapabilityResult, Executor
from .events import (
    CapabilityCompleted,
    CapabilityDispatched,
    ConditionInvalidated,
    ConditionSatisfied,
    EngineDecision,
    EngineError,
    EngineFinished,
    EngineStarted,
    Event,
    EventLog,
    StateObserved,
)
from .goal import Condition, ConditionId, Constraints, DesiredProjectState, Goal
from .hitl import (
    HitlDecision,
    HitlRequestedPayload,
    HitlResolvedPayload,
    HitlReviewRequest,
    ReviewChannel,
)
from .operator import EngineLoop, EngineLoopError
from .registry import CapabilityRegistry
from .selector import (
    CapabilitySelector,
    CheapestFirst,
    FirstMatch,
    MostProductive,
    PrioritySelector,
)
from .state import ProjectState
from .store import ArtifactStore, FilesystemStore, MemoryStore, MultiRepoStore
from .validator import Validator, ValidatorResult

__all__ = [
    # artifact
    "Artifact", "ArtifactId", "ArtifactKind", "ArtifactDelta", "EMPTY_DELTA",
    # store
    "ArtifactStore", "MemoryStore", "FilesystemStore", "MultiRepoStore",
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
    "ConditionSatisfied", "ConditionInvalidated", "EngineDecision",
    # operator
    "EngineLoop", "EngineLoopError",
    # selector
    "CapabilitySelector", "CheapestFirst", "FirstMatch", "MostProductive", "PrioritySelector",
    # platform bridge
    "EventBusBridge",
    # sandbox
    "sandbox",
    # hitl contract
    "HitlReviewRequest", "HitlDecision",
    "HitlRequestedPayload", "HitlResolvedPayload",
    "ReviewChannel",
]
