"""antcrew.engine — re-exports from antcrew_engine.engine (backward compatibility).

The canonical implementation lives in the ``antcrew-engine`` package
(``antcrew_engine.engine``).  This shim ensures that existing code which
imports from ``antcrew.engine`` continues to work without modification.
"""
from antcrew_engine.engine import (
    EMPTY_DELTA,
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    ArtifactStore,
    CapabilityCompleted,
    CapabilityDescriptor,
    CapabilityDispatched,
    CapabilityRegistry,
    CapabilityResult,
    CapabilitySelector,
    CheapestFirst,
    Condition,
    ConditionId,
    ConditionInvalidated,
    ConditionSatisfied,
    Constraints,
    DesiredProjectState,
    EngineError,
    EngineFinished,
    EngineStarted,
    Event,
    EventBusBridge,
    EventLog,
    Executor,
    FilesystemStore,
    FirstMatch,
    Goal,
    MemoryStore,
    MostProductive,
    MultiRepoStore,
    PrioritySelector,
    ProjectState,
    StateObserved,
    Validator,
    ValidatorResult,
)

# antcrew-engine >= 0.3.0 renamed Operator → EngineLoop, OperatorDecision →
# EngineDecision, OperatorError → EngineLoopError.  Support both to avoid a
# hard coupling to a specific antcrew-engine release.
try:
    from antcrew_engine.engine import EngineDecision, EngineLoop, EngineLoopError
except ImportError:
    from antcrew_engine.engine import (
        Operator as EngineLoop,
    )
    from antcrew_engine.engine import (  # type: ignore[no-redef]
        OperatorDecision as EngineDecision,
    )
    from antcrew_engine.engine import (
        OperatorError as EngineLoopError,
    )

# Backward-compat aliases — tests and user code may still use old names
Operator = EngineLoop
OperatorDecision = EngineDecision
OperatorError = EngineLoopError

__all__ = [
    "Artifact", "ArtifactId", "ArtifactKind", "ArtifactDelta", "EMPTY_DELTA",
    "ArtifactStore", "MemoryStore", "FilesystemStore", "MultiRepoStore",
    "Condition", "ConditionId", "DesiredProjectState", "Constraints", "Goal",
    "ProjectState",
    "CapabilityDescriptor", "CapabilityResult", "Executor",
    "Validator", "ValidatorResult",
    "CapabilityRegistry",
    "Event", "EventLog",
    "EngineStarted", "EngineFinished", "EngineError",
    "StateObserved", "CapabilityDispatched", "CapabilityCompleted",
    "ConditionSatisfied", "ConditionInvalidated",
    "EngineDecision", "EngineLoop", "EngineLoopError",
    "Operator", "OperatorDecision", "OperatorError",
    "CapabilitySelector", "CheapestFirst", "FirstMatch", "MostProductive", "PrioritySelector",
    "EventBusBridge",
]
