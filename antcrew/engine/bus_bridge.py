"""EventBusBridge: forward engine EventLog events to antcrew.core.events.bus.

Lets antcrew-platform's listener observe engine runs as regular pipeline events,
without any changes to the platform's DB persistence or WebSocket streaming code.

Mapping:
    capability_dispatched -> agent.start  {agent_name}
    capability_completed  -> agent.end    {agent_name, duration_s, produced_keys}

pipeline.start and pipeline.end are intentionally NOT mapped here:
  - pipeline.start is emitted by the caller before spawning the executor thread
    so the DB row exists immediately before any agent.start fires.
  - pipeline.end is emitted by the caller after Operator.run() completes so it
    can carry the real cost_usd from llm.get_usage_summary().
"""
from __future__ import annotations

from .events import EventLog


class EventBusBridge:
    """Subscribes to an engine EventLog and forwards capability events to the global antcrew bus.

    Usage::

        log = EventLog()
        bridge = EventBusBridge(log, run_id="abc123")
        # now run Operator.run() — the platform listener sees agent.start/end events
        # emit pipeline.end yourself after the run with the actual cost_usd
    """

    def __init__(
        self,
        event_log: EventLog,
        *,
        run_id: str,
        thread_id: str = "default",
    ) -> None:
        self._run_id = run_id
        self._thread_id = thread_id
        event_log.subscribe(self._handle)

    def _handle(self, event) -> None:
        from antcrew.core.events import bus, Event as BusEvent

        kind = event.kind
        rid = self._run_id
        tid = self._thread_id

        if kind == "capability_dispatched":
            bus.emit(BusEvent(
                "agent.start",
                {"agent_name": event.capability_name, "run_id": rid, "thread_id": tid},
                run_id=rid,
                thread_id=tid,
            ))

        elif kind == "capability_completed":
            result = event.result
            duration = round(result.execution_time, 3) if result else 0.0
            produced = (
                [str(a.id) for a in result.delta.created]
                if (result and result.delta) else []
            )
            bus.emit(BusEvent(
                "agent.end",
                {
                    "agent_name":   event.capability_name,
                    "duration_s":   duration,
                    "cost_usd":     result.cost_usd if result else 0.0,
                    "produced_keys": produced,
                    "run_id":       rid,
                    "thread_id":    tid,
                },
                run_id=rid,
                thread_id=tid,
            ))

        elif kind == "capability_progress":
            bus.emit(BusEvent(
                "agent.token",
                {
                    "agent_name": event.capability_name,
                    "chunk":      event.chunk,
                    "run_id":     rid,
                    "thread_id":  tid,
                },
                run_id=rid,
                thread_id=tid,
            ))
