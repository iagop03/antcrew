"""antcrew_engine — capability-driven project execution engine (Layer 2).

The engine autonomously builds software by iterating an EngineLoop loop over a
goal until the project reaches a desired state.  It has no dependency on
LangGraph or the supervised multi-agent teams in antcrew (Layer 1).

Quick start::

    from antcrew_engine.engine import EngineLoop, MemoryStore, Goal, DesiredProjectState
    from antcrew_engine.engine import Condition, ConditionId, Constraints
    from antcrew_engine.capabilities import Architect, TaskPlanner, CodeGenerator
    from antcrew_engine.config import build_llm

    llm   = build_llm("claude")
    store = MemoryStore()
    goal  = Goal("build a REST API", DesiredProjectState(...), Constraints())
    op    = EngineLoop(registry, validators, log)
    state = op.run(store, goal)

CLI entry point (installed as ``antcrew-engine``)::

    antcrew-engine "Build a REST API" --tech Python --output ./my-api
"""

from antcrew_engine.capabilities import (
    Architect,
    BugFixer,
    CodeGenerator,
    CodeRegenerator,
    CodeReviewer,
    DependencyInstaller,
    DocGenerator,
    HitlReviewer,
    ReviewFixer,
    SpecExtractor,
    TaskPlanner,
    TestGenerator,
    TestRunner,
)
from antcrew_engine.capabilities.validators import artifact_validators
from antcrew_engine.config import build_llm
from antcrew_engine.engine import (
    EMPTY_DELTA,
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    ArtifactStore,
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityResult,
    CapabilitySelector,
    CheapestFirst,
    Condition,
    ConditionId,
    Constraints,
    DesiredProjectState,
    EngineLoop,
    EngineLoopError,
    EventBusBridge,
    EventLog,
    Executor,
    FilesystemStore,
    FirstMatch,
    Goal,
    HitlDecision,
    HitlRequestedPayload,
    HitlResolvedPayload,
    HitlReviewRequest,
    MemoryStore,
    MostProductive,
    MultiRepoStore,
    PrioritySelector,
    ProjectState,
    ReviewChannel,
    Validator,
    ValidatorResult,
)
from antcrew_engine.engine.events import HitlRequested, HitlResolved

try:
    from importlib.metadata import version as _v

    __version__: str = _v("antcrew-engine")
except Exception:
    __version__ = "unknown"

__all__ = [
    # engine
    "Artifact",
    "ArtifactId",
    "ArtifactKind",
    "ArtifactDelta",
    "EMPTY_DELTA",
    "ArtifactStore",
    "MemoryStore",
    "FilesystemStore",
    "MultiRepoStore",
    "Condition",
    "ConditionId",
    "DesiredProjectState",
    "Constraints",
    "Goal",
    "ProjectState",
    "CapabilityDescriptor",
    "CapabilityResult",
    "Executor",
    "ValidatorResult",
    "Validator",
    "CapabilityRegistry",
    "EventLog",
    "EngineLoop",
    "EngineLoopError",
    "CapabilitySelector",
    "CheapestFirst",
    "FirstMatch",
    "MostProductive",
    "PrioritySelector",
    "EventBusBridge",
    "HitlReviewRequest",
    "HitlDecision",
    "HitlRequestedPayload",
    "HitlResolvedPayload",
    "HitlRequested",
    "HitlResolved",
    "ReviewChannel",
    # capabilities
    "Architect",
    "TaskPlanner",
    "CodeGenerator",
    "CodeRegenerator",
    "BugFixer",
    "CodeReviewer",
    "DependencyInstaller",
    "DocGenerator",
    "HitlReviewer",
    "ReviewFixer",
    "SpecExtractor",
    "TestGenerator",
    "TestRunner",
    # config
    "build_llm",
    # validators
    "artifact_validators",
]
