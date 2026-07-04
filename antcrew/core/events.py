"""Pipeline event bus — zero-dependency observability for antcrew pipelines.

Agents and teams emit structured events as they run. External code (dashboards,
loggers, the antcrew-platform layer) subscribes to those events without touching
pipeline internals.

Usage — subscribing::

    from antcrew.core.events import bus, Event

    def on_agent_end(event: Event) -> None:
        print(f"{event.payload['agent_name']} finished in {event.payload['duration_s']:.2f}s")

    bus.subscribe("agent.end", on_agent_end)

    # Wildcard: receive every event type
    bus.subscribe("*", lambda e: print(e.type, e.payload))

Usage — in tests::

    from antcrew.core.events import bus, capture

    with capture("agent.end", "pipeline.end") as events:
        team.run("Build JWT auth")

    assert events[0].type == "agent.end"
    assert events[0].payload["agent_name"] == "backend_dev"

Event catalogue
---------------
pipeline.start  — {request, thread_id, team, run_id}
pipeline.end    — {thread_id, run_id, cost_usd, success, artifact_summary: dict}
agent.start     — {agent_name, thread_id, run_id}
agent.end       — {agent_name, thread_id, run_id, duration_s, produced_keys: list[str]}
artifact.created— {artifact_type, count, file_paths: list[str], thread_id, run_id}
feedback.round  — {round_num, max_rounds, success, thread_id, run_id}
kb.updated      — {summary, thread_id, run_id}
coherence.run   — {files_corrected, thread_id, run_id}
"""
from __future__ import annotations

import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger(__name__)

Handler = Callable[["Event"], None]


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """A pipeline event with a type, structured payload, and correlation IDs."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    run_id: Optional[str] = None
    thread_id: Optional[str] = None

    def __repr__(self) -> str:
        keys = list(self.payload.keys())
        return f"Event({self.type!r}, run_id={self.run_id!r}, payload_keys={keys})"


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class EventBus:
    """Synchronous, in-process event bus.

    Design constraints:
    - Handler exceptions are caught and logged — never allowed to break a pipeline.
    - Dispatch is synchronous (simple; avoids threading issues in tests).
    - Wildcard handlers (subscribed with type="*") receive every event.
    - Thread-safe enough for the typical single-pipeline-thread use case.
      For concurrent pipelines, each run can use an isolated bus instance or
      the global bus with thread-safe handlers.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._wildcard: list[Handler] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register *handler* for *event_type*. Use ``"*"`` for all events."""
        if event_type == "*":
            if handler not in self._wildcard:
                self._wildcard.append(handler)
        else:
            bucket = self._handlers.setdefault(event_type, [])
            if handler not in bucket:
                bucket.append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Remove *handler* for *event_type*."""
        if event_type == "*":
            self._wildcard = [h for h in self._wildcard if h is not handler]
        else:
            self._handlers[event_type] = [
                h for h in self._handlers.get(event_type, []) if h is not handler
            ]

    def clear(self, event_type: Optional[str] = None) -> None:
        """Remove all handlers, or only handlers for *event_type*."""
        if event_type is None:
            self._handlers.clear()
            self._wildcard.clear()
        elif event_type == "*":
            self._wildcard.clear()
        else:
            self._handlers.pop(event_type, None)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def emit(self, event: Event) -> None:
        """Dispatch *event* to all matching handlers synchronously.

        Handler exceptions are caught and logged so a misbehaving subscriber
        can never abort a pipeline run.
        """
        targets = list(self._handlers.get(event.type, [])) + list(self._wildcard)
        for handler in targets:
            try:
                handler(event)
            except Exception as exc:
                log.warning(
                    "EventBus: handler %r raised for %r — %s",
                    getattr(handler, "__name__", handler),
                    event.type,
                    exc,
                )

    def __contains__(self, event_type: str) -> bool:
        """``event_type in bus`` — True if at least one handler is registered."""
        if event_type == "*":
            return bool(self._wildcard)
        return bool(self._handlers.get(event_type)) or bool(self._wildcard)


# ---------------------------------------------------------------------------
# Global bus — the primary integration point for the platform layer
# ---------------------------------------------------------------------------

bus: EventBus = EventBus()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_run_id() -> str:
    """Generate a short, URL-safe run identifier."""
    return uuid.uuid4().hex[:12]


@contextlib.contextmanager
def capture(*event_types: str) -> Iterator[list[Event]]:
    """Context manager that captures events into a list — useful in tests.

    If no *event_types* are given, captures all events (wildcard).

    Example::

        with capture("agent.end") as events:
            team.run("Build auth")
        assert events[0].payload["agent_name"] == "backend_dev"
    """
    collected: list[Event] = []

    def _handler(event: Event) -> None:
        collected.append(event)

    types = event_types or ("*",)
    for t in types:
        bus.subscribe(t, _handler)
    try:
        yield collected
    finally:
        for t in types:
            bus.unsubscribe(t, _handler)


# ---------------------------------------------------------------------------
# Node wrapper used by Supervisor.build()
# ---------------------------------------------------------------------------

def _make_evented_run(run_fn: Callable, agent_name: str) -> Callable:
    """Wrap a LangGraph node function to emit agent.start and agent.end events."""

    def _evented(state: dict) -> dict:
        run_id = state.get("_run_id")
        thread_id = state.get("_thread_id", "default")
        bus.emit(Event(
            "agent.start",
            {"agent_name": agent_name},
            run_id=run_id,
            thread_id=thread_id,
        ))
        t0 = time.monotonic()
        result = run_fn(state)
        duration = round(time.monotonic() - t0, 3)
        produced = [
            k for k, v in result.items()
            if v is not None and not k.startswith("_") and k not in ("current_agent", "errors", "messages", "metadata")
        ]
        bus.emit(Event(
            "agent.end",
            {"agent_name": agent_name, "duration_s": duration, "produced_keys": produced},
            run_id=run_id,
            thread_id=thread_id,
        ))
        return result

    _evented.__name__ = f"evented_{agent_name}"
    return _evented
