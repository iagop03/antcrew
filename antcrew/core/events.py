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
agent.end       — {agent_name, thread_id, run_id, duration_s, produced_keys: list[str], tokens_in: int, tokens_out: int, cost_usd: float}
hitl.pending    — {agent_name, channel, feedback_schema: dict|None, review_type, thread_id, run_id}
artifact.created— {artifact_type, count, file_paths: list[str], thread_id, run_id}
feedback.round  — {round_num, max_rounds, success, thread_id, run_id}
kb.updated      — {summary, thread_id, run_id}
coherence.run   — {files_corrected, thread_id, run_id}
"""
from __future__ import annotations

import contextlib
import logging
import threading
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
    - Dispatch is synchronous: handlers run in the caller's thread.
    - Wildcard handlers (subscribed with type="*") receive every event.
    - Thread-safe: a ``threading.Lock`` guards all mutations and reads of the
      handler registry so concurrent ``AsyncTeam*`` runs sharing the global bus
      cannot corrupt each other's subscription lists.

    Thread-safety model:
        subscribe/unsubscribe/clear hold the lock for the full mutation.
        emit copies the handler list under the lock and then releases before
        calling handlers, so a handler that calls subscribe/unsubscribe
        will not deadlock.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._wildcard: list[Handler] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register *handler* for *event_type*. Use ``"*"`` for all events."""
        with self._lock:
            if event_type == "*":
                if handler not in self._wildcard:
                    self._wildcard.append(handler)
            else:
                bucket = self._handlers.setdefault(event_type, [])
                if handler not in bucket:
                    bucket.append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Remove *handler* for *event_type*."""
        with self._lock:
            if event_type == "*":
                self._wildcard = [h for h in self._wildcard if h != handler]
            else:
                self._handlers[event_type] = [
                    h for h in self._handlers.get(event_type, []) if h != handler
                ]

    def clear(self, event_type: Optional[str] = None) -> None:
        """Remove all handlers, or only handlers for *event_type*."""
        with self._lock:
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

    def emit(self, event_or_type, payload: dict | None = None, *,
             run_id: str | None = None, thread_id: str | None = None) -> None:
        """Dispatch an event to all matching handlers synchronously.

        Two calling conventions are supported::

            # Explicit Event object (original API)
            bus.emit(Event("agent.start", {"agent_name": "pm"}))

            # Convenience shorthand — type + payload + optional metadata
            bus.emit("pipeline.start", {"request": req}, run_id=rid, thread_id=tid)

        Handler exceptions are caught and logged so a misbehaving subscriber
        can never abort a pipeline run.
        """
        if isinstance(event_or_type, str):
            event = Event(event_or_type, payload or {}, run_id=run_id, thread_id=thread_id)
        else:
            event = event_or_type
        # Copy handler list under lock, then dispatch without holding it.
        with self._lock:
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
        with self._lock:
            if event_type == "*":
                return bool(self._wildcard)
            return bool(self._handlers.get(event_type)) or bool(self._wildcard)


# ---------------------------------------------------------------------------
# WebhookSink — thread-safe event collector for outbound webhook delivery
# ---------------------------------------------------------------------------

class WebhookSink:
    """Thread-safe event collector that the platform layer uses for outbound webhooks.

    Subscribe to the global bus before a run, then drain after the run completes
    to create WebhookDelivery rows for each captured event.

    Usage (platform runner)::

        sink = WebhookSink(run_id="abc123")
        bus.subscribe("*", sink.handle)
        try:
            result = run_pipeline(...)
        finally:
            bus.unsubscribe("*", sink.handle)

        for event_type, payload in sink.drain():
            await fire_event_webhooks(session, workspace_id=ws_id,
                                      event_type=event_type, run_id=sink.run_id,
                                      payload=payload)
    """

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self._events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def handle(self, event: "Event") -> None:
        with self._lock:
            self._events.append((event.type, dict(event.payload)))

    def drain(self) -> list[tuple[str, dict]]:
        """Return all captured (event_type, payload) pairs and clear the internal list."""
        with self._lock:
            captured = list(self._events)
            self._events.clear()
        return captured

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


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

def _schema_json(agent) -> Optional[dict]:
    """Return feedback_schema as a JSON Schema dict, or None."""
    schema_cls = getattr(agent, "feedback_schema", None)
    if schema_cls is None:
        return None
    try:
        return schema_cls.model_json_schema()  # Pydantic v2
    except AttributeError:
        try:
            return schema_cls.schema()  # Pydantic v1
        except Exception:
            return None


def _llm_usage_totals(agent) -> dict:
    """Return current cumulative token/cost totals from the agent's LLM, or zeros.

    Resolution order: agent.llm (BaseAgent), agent._llm (legacy naming),
    agent._agent.llm (_KBProxy wrapping a BaseAgent).
    """
    try:
        llm = (
            getattr(agent, "llm", None)
            or getattr(agent, "_llm", None)
            or getattr(getattr(agent, "_agent", None), "llm", None)
        )
        if llm is None:
            return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        summary = llm.get_usage_summary()
        return {
            "tokens_in":  summary.get("total_input_tokens", 0),
            "tokens_out": summary.get("total_output_tokens", 0),
            "cost_usd":   summary.get("total_cost_usd", 0.0),
        }
    except Exception:
        return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


def _make_evented_run(run_fn: Callable, agent_name: str, agent=None) -> Callable:
    """Wrap a LangGraph node function to emit agent.start and agent.end events.

    When *agent* is provided its LLM usage is sampled before and after the call
    so per-agent token/cost deltas are included in the agent.end payload.
    """

    def _evented(state: dict) -> dict:
        run_id = state.get("_run_id")
        thread_id = state.get("_thread_id", "default")
        # Inject run context so agent._tracelog() can correlate events to the run
        if agent is not None and hasattr(agent, "bind_run"):
            agent.bind_run(run_id, thread_id)
        bus.emit(Event(
            "agent.start",
            {"agent_name": agent_name, "stage": getattr(agent, "stage", "")},
            run_id=run_id,
            thread_id=thread_id,
        ))
        t0 = time.monotonic()
        before = _llm_usage_totals(agent) if agent is not None else None
        # Snapshot fallback event count before this call so we emit only new ones.
        _fb_count = len(getattr(getattr(agent, "llm", None), "_fallback_events", ()) or ())
        result = run_fn(state)
        duration = round(time.monotonic() - t0, 3)
        produced = [
            k for k, v in result.items()
            if v is not None and not k.startswith("_") and k not in ("current_agent", "errors", "messages", "metadata")
        ]
        if before is not None:
            after = _llm_usage_totals(agent)
            usage = {
                "tokens_in":  after["tokens_in"]  - before["tokens_in"],
                "tokens_out": after["tokens_out"] - before["tokens_out"],
                "cost_usd":   round(after["cost_usd"] - before["cost_usd"], 6),
            }
        else:
            usage = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        bus.emit(Event(
            "agent.end",
            {
                "agent_name": agent_name,
                "stage": getattr(agent, "stage", ""),
                "governance_hash": getattr(agent, "governance_hash", ""),
                "duration_s": duration,
                "produced_keys": produced,
                **usage,
            },
            run_id=run_id,
            thread_id=thread_id,
        ))
        # Emit any FallbackLLM events accumulated during this agent call.
        _llm = getattr(agent, "llm", None) if agent is not None else None
        if _llm is not None:
            _fev_list = getattr(_llm, "_fallback_events", None)
            if _fev_list is not None:
                for _fe in list(_fev_list[_fb_count:]):
                    bus.emit(Event(
                        "llm.fallback",
                        {**_fe, "agent_name": agent_name},
                        run_id=run_id,
                        thread_id=thread_id,
                    ))
        if agent is not None and getattr(agent, "approval_required", False):
            bus.emit(Event(
                "hitl.pending",
                {
                    "agent_name":      agent_name,
                    "channel":         getattr(agent, "hitl_channel", "default"),
                    "feedback_schema": _schema_json(agent),
                    "review_type":     getattr(agent, "review_type", "approval"),
                },
                run_id=run_id,
                thread_id=thread_id,
            ))
        return result

    _evented.__name__ = f"evented_{agent_name}"
    return _evented
