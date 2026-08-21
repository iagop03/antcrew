"""Tests for UC5: WebhookSink (thread-safe event collector for outbound webhooks)."""
from __future__ import annotations

import threading
import time

from antcrew.core.events import Event, EventBus, WebhookSink


# ---------------------------------------------------------------------------
# Basic interface
# ---------------------------------------------------------------------------

def test_webhook_sink_starts_empty():
    sink = WebhookSink()
    assert len(sink) == 0


def test_webhook_sink_run_id_default():
    sink = WebhookSink()
    assert sink.run_id == ""


def test_webhook_sink_run_id_passed():
    sink = WebhookSink(run_id="abc123")
    assert sink.run_id == "abc123"


def test_webhook_sink_handle_stores_events():
    sink = WebhookSink(run_id="r1")
    e = Event("pipeline.end", {"cost_usd": 0.01}, run_id="r1")
    sink.handle(e)
    assert len(sink) == 1


def test_webhook_sink_drain_returns_events():
    sink = WebhookSink(run_id="r1")
    sink.handle(Event("pipeline.start", {"team": "dev"}, run_id="r1"))
    sink.handle(Event("agent.end", {"agent_name": "pm"}, run_id="r1"))
    captured = sink.drain()
    assert len(captured) == 2
    assert captured[0][0] == "pipeline.start"
    assert captured[1][0] == "agent.end"


def test_webhook_sink_drain_clears_buffer():
    sink = WebhookSink()
    sink.handle(Event("test.event", {}, run_id="r"))
    sink.drain()
    assert len(sink) == 0


def test_webhook_sink_drain_payload_is_copy():
    """Payload dict returned by drain should not be the event's live dict."""
    sink = WebhookSink()
    original = {"key": "value"}
    sink.handle(Event("test.event", original, run_id="r"))
    captured = sink.drain()
    original["key"] = "mutated"
    assert captured[0][1]["key"] == "value"


def test_webhook_sink_multiple_drains():
    sink = WebhookSink()
    sink.handle(Event("e1", {}, run_id="r"))
    sink.drain()
    sink.handle(Event("e2", {}, run_id="r"))
    second = sink.drain()
    assert len(second) == 1
    assert second[0][0] == "e2"


# ---------------------------------------------------------------------------
# Integration with EventBus
# ---------------------------------------------------------------------------

def test_webhook_sink_bus_subscription():
    local_bus = EventBus()
    sink = WebhookSink()
    local_bus.subscribe("*", sink.handle)
    local_bus.emit("pipeline.start", {"team": "dev"}, run_id="r")
    local_bus.emit("agent.end", {"agent_name": "pm"}, run_id="r")
    local_bus.unsubscribe("*", sink.handle)
    captured = sink.drain()
    assert len(captured) == 2


def test_webhook_sink_unsubscribe_stops_capture():
    local_bus = EventBus()
    sink = WebhookSink()
    local_bus.subscribe("*", sink.handle)
    local_bus.emit("e1", {}, run_id="r")
    local_bus.unsubscribe("*", sink.handle)
    local_bus.emit("e2", {}, run_id="r")
    assert len(sink) == 1  # only e1 captured


def test_webhook_sink_event_type_preserved():
    local_bus = EventBus()
    sink = WebhookSink()
    local_bus.subscribe("*", sink.handle)
    local_bus.emit("llm.fallback", {"failed_model": "gpt-4", "next_model": "claude"}, run_id="r")
    local_bus.unsubscribe("*", sink.handle)
    captured = sink.drain()
    assert captured[0][0] == "llm.fallback"
    assert captured[0][1]["failed_model"] == "gpt-4"


def test_webhook_sink_payload_fields_preserved():
    local_bus = EventBus()
    sink = WebhookSink()
    local_bus.subscribe("*", sink.handle)
    local_bus.emit("pipeline.end", {"cost_usd": 0.042, "success": True}, run_id="test")
    local_bus.unsubscribe("*", sink.handle)
    ev_type, payload = sink.drain()[0]
    assert ev_type == "pipeline.end"
    assert payload["cost_usd"] == 0.042
    assert payload["success"] is True


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

def test_webhook_sink_thread_safe_concurrent_handles():
    """Multiple threads writing concurrently — no corruption."""
    sink = WebhookSink()
    errors: list = []

    def writer(n: int) -> None:
        for i in range(50):
            try:
                sink.handle(Event(f"ev.{n}.{i}", {"n": n, "i": i}, run_id="r"))
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(sink) == 250  # 5 threads × 50 events


def test_webhook_sink_thread_safe_drain_during_write():
    """Drain while another thread is writing — no deadlock, no lost data."""
    sink = WebhookSink()
    stop = threading.Event()
    total_written: list[int] = [0]

    def writer():
        for _ in range(100):
            sink.handle(Event("e", {}, run_id="r"))
            total_written[0] += 1
            time.sleep(0.0001)
        stop.set()

    t = threading.Thread(target=writer)
    t.start()

    drained = 0
    while not stop.is_set():
        drained += len(sink.drain())
        time.sleep(0.001)
    drained += len(sink.drain())  # final drain

    t.join()
    assert drained == 100


# ---------------------------------------------------------------------------
# Import surface
# ---------------------------------------------------------------------------

def test_webhook_sink_importable_from_antcrew():
    from antcrew import WebhookSink as WS  # noqa: F401
    assert WS is WebhookSink
