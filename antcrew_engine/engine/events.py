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
from datetime import UTC, datetime
from typing import Callable

from .capability import CapabilityResult
from .goal import ConditionId
from .state import ProjectState


@dataclass(frozen=True)
class Event:
    kind:      str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


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
class EngineDecision(Event):
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
    iterations:     int   = 0
    success:        bool  = False
    total_cost_usd: float = 0.0
    kind:           str   = "engine_finished"


@dataclass(frozen=True)
class EngineError(Event):
    error_kind: str = ""
    message:    str = ""
    kind:       str = "engine_error"


@dataclass(frozen=True)
class CapabilityProgress(Event):
    """Emitted for each streaming token chunk from an LLM-backed capability."""
    capability_name: str = ""
    chunk:           str = ""
    kind:            str = "capability_progress"


@dataclass(frozen=True)
class HitlRequested(Event):
    review_id:           str = ""
    reviewed_capability: str = ""
    kind:                str = "hitl_requested"


@dataclass(frozen=True)
class HitlResolved(Event):
    review_id: str = ""
    verdict:   str = ""
    kind:      str = "hitl_resolved"


Handler = Callable[[Event], None]


class EventLog:
    """Ordered, appendable log of engine events.  Supports live subscribers."""

    def __init__(self) -> None:
        self._events:   list[Event]   = []
        self._handlers: list[Handler] = []

    def emit(self, event: Event) -> None:
        # CapabilityProgress events are fire-and-forget: dispatched to subscribers
        # but never stored in _events (prevents unbounded memory growth from streaming).
        if event.kind != "capability_progress":
            self._events.append(event)
        for handler in self._handlers:
            handler(event)

    def subscribe(self, kind_or_handler, handler: Handler | None = None) -> None:
        """Subscribe to events.

        subscribe(handler)            -- called for every event
        subscribe("event_kind", fn)   -- called only when event.kind matches
        """
        if handler is None:
            self._handlers.append(kind_or_handler)
        else:
            kind = kind_or_handler
            self._handlers.append(lambda e: handler(e) if e.kind == kind else None)

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
