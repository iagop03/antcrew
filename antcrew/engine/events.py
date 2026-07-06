"""EventLog: the nervous system of the engine.

Every meaningful transition in the engine emits an Event.
The EventLog is the authoritative record — it serves observability,
replay, debugging, metrics, and UI without any component coupling
to each other.

Components emit events; they never call each other directly to
communicate state changes.

Subscribers receive events synchronously in emission order.
For async delivery, wrap in an async adapter at the subscriber level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .capability import CapabilityResult
from .goal import ConditionId
from .state import ProjectState


@dataclass(frozen=True)
class Event:
    kind:      str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class StateObserved(Event):
    state:     ProjectState | None = None
    iteration: int                 = 0
    kind:      str                 = "state_observed"


@dataclass(frozen=True)
class CapabilityDispatched(Event):
    capability_name: str                  = ""
    gap:             frozenset[ConditionId] = field(default_factory=frozenset)
    kind:            str                  = "capability_dispatched"


@dataclass(frozen=True)
class CapabilityCompleted(Event):
    capability_name: str                   = ""
    result:          CapabilityResult | None = None
    kind:            str                   = "capability_completed"


@dataclass(frozen=True)
class ConditionSatisfied(Event):
    condition_id: ConditionId = ConditionId("")
    kind:         str         = "condition_satisfied"


@dataclass(frozen=True)
class ConditionInvalidated(Event):
    condition_id: ConditionId = ConditionId("")
    kind:         str         = "condition_invalidated"


@dataclass(frozen=True)
class OperatorDecision(Event):
    chosen:     str            = ""
    candidates: tuple[str, ...] = ()
    reason:     str            = ""
    kind:       str            = "operator_decision"


@dataclass(frozen=True)
class EngineStarted(Event):
    goal_description: str = ""
    kind:             str = "engine_started"


@dataclass(frozen=True)
class EngineFinished(Event):
    iterations: int  = 0
    success:    bool = False
    kind:       str  = "engine_finished"


@dataclass(frozen=True)
class EngineError(Event):
    error_kind: str = ""
    message:    str = ""
    kind:       str = "engine_error"


Handler = Callable[[Event], None]


class EventLog:
    """Ordered, appendable log of engine events.  Supports live subscribers."""

    def __init__(self) -> None:
        self._events:   list[Event]   = []
        self._handlers: list[Handler] = []

    def emit(self, event: Event) -> None:
        self._events.append(event)
        for handler in self._handlers:
            handler(event)

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        self._handlers.remove(handler)

    def events(self, kind: str | None = None) -> list[Event]:
        if kind is None:
            return list(self._events)
        return [e for e in self._events if e.kind == kind]

    def clear(self) -> None:
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)
