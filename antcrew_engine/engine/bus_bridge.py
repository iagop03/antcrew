"""EventBusBridge: forward engine EventLog events to an external event bus.

Mapping:
    capability_dispatched -> agent.start  {agent_name}
    capability_completed  -> agent.end    {agent_name, duration_s, produced_keys}
    capability_progress   -> agent.token  {agent_name, chunk}

pipeline.start and pipeline.end are intentionally NOT mapped here — the caller
emits those before/after EngineLoop.run() so they carry the real run_id and cost.

Dependency injection
--------------------
Always pass ``on_event`` so antcrew_engine has no upward dependency on antcrew.
The caller is responsible for wiring the bridge to its own event bus:

    from antcrew.core.events import Event as BusEvent, bus
    bridge = EventBusBridge(event_log, run_id=rid,
        on_event=lambda t, d, **kw: bus.emit(BusEvent(t, d, **kw)))

Events are silently dropped when ``on_event`` is None (no-op).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .events import EventLog

if TYPE_CHECKING:
    pass


class EventBusBridge:
    """Subscribes to an engine EventLog and forwards capability events.

    Args:
        event_log: Engine EventLog to subscribe to.
        run_id: Platform run identifier forwarded in every event payload.
        thread_id: Platform thread identifier (default ``"default"``).
        on_event: Optional ``(event_type: str, data: dict, **kwargs) -> None``
            callable.  If omitted, falls back to the lazy ``antcrew.core.events``
            import (backward-compatible when antcrew is installed).
    """

    def __init__(
        self,
        event_log: EventLog,
        *,
        run_id: str,
        thread_id: str = "default",
        on_event: "Callable[[str, dict], None] | None" = None,
    ) -> None:
        self._run_id = run_id
        self._thread_id = thread_id
        self._on_event = on_event
        event_log.subscribe(self._handle)

    def _handle(self, event) -> None:
        on_event = self._on_event
        if on_event is None:
            return  # no bus wired — events are intentionally dropped

        kind = event.kind
        rid = self._run_id
        tid = self._thread_id

        if kind == "capability_dispatched":
            on_event(
                "agent.start",
                {"agent_name": event.capability_name, "run_id": rid, "thread_id": tid},
                run_id=rid,
                thread_id=tid,
            )

        elif kind == "capability_completed":
            result = event.result
            duration = round(result.execution_time, 3) if result else 0.0
            produced = (
                [str(a.id) for a in result.delta.created]
                if (result and result.delta) else []
            )
            on_event(
                "agent.end",
                {
                    "agent_name":         event.capability_name,
                    "duration_s":         duration,
                    "duration_ms":        int(duration * 1000),
                    "cost_usd":           result.cost_usd            if result else 0.0,
                    "tokens_in":          result.tokens_in            if result else 0,
                    "tokens_out":         result.tokens_out           if result else 0,
                    "cache_read_tokens":  result.cache_read_tokens    if result else 0,
                    "cache_write_tokens": result.cache_write_tokens   if result else 0,
                    "produced_keys":      produced,
                    "succeeded":          result.succeeded             if result else False,
                    "errors":             list(result.errors)          if result else [],
                    "run_id":             rid,
                    "thread_id":          tid,
                },
                run_id=rid,
                thread_id=tid,
            )

        elif kind == "capability_progress":
            on_event(
                "agent.token",
                {
                    "agent_name": event.capability_name,
                    "chunk":      event.chunk,
                    "run_id":     rid,
                    "thread_id":  tid,
                },
                run_id=rid,
                thread_id=tid,
            )

        elif kind == "hitl_requested":
            on_event(
                "hitl.review_required",
                {
                    "review_id":            event.review_id,
                    "run_id":               rid,
                    "thread_id":            tid,
                    "reviewed_capability":  event.reviewed_capability,
                },
                run_id=rid,
                thread_id=tid,
            )

        elif kind == "hitl_resolved":
            on_event(
                "hitl.resolved",
                {
                    "review_id":  event.review_id,
                    "run_id":     rid,
                    "thread_id":  tid,
                    "verdict":    event.verdict,
                },
                run_id=rid,
                thread_id=tid,
            )
