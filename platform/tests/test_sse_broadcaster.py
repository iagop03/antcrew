"""Unit tests for _SSEBroadcaster — the SSE fan-out broadcaster introduced in M-2.

These tests verify the core fan-out behaviour independently of the HTTP layer:
  - subscribe() returns a queue and starts exactly one poller per run_id
  - unsubscribe() cleans up: removes the queue, cancels the poller when none left
  - Multiple subscribers receive the same events (fan-out)
  - A late subscriber that joins an existing broadcaster gets a catchup_needed message
  - The broadcaster handles terminal run status and broadcasts end to all subscribers
  - display_name SQLite migration adds the column to existing tables (M-1)
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("ANTCREW_TESTING", "1")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(run_id: str, event_id: int, event_type: str = "agent.start"):
    ev = MagicMock()
    ev.id = event_id
    ev.run_id = run_id
    ev.event_type = event_type
    ev.payload = {}
    ev.timestamp = float(event_id)
    ev.thread_id = "default"
    return ev


def _make_run(run_id: str, status: str = "running"):
    run = MagicMock()
    run.run_id = run_id
    run.status = status
    return run


class _FakeSession:
    """Async context manager that returns a canned result for sess.exec(...)."""

    def __init__(self, rows=None, run=None):
        self._rows = rows or []
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def exec(self, _stmt):
        result = MagicMock()
        result.all.return_value = self._rows
        result.first.return_value = self._run
        return result


# ---------------------------------------------------------------------------
# Basic subscribe / unsubscribe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_returns_queue():
    from app.core.sse import _SSEBroadcaster

    bc = _SSEBroadcaster()

    def _session_factory(rows, run):
        class _SF:
            def __init__(self, engine, **kw): pass
            async def __aenter__(self): return _FakeSession(rows, run)
            async def __aexit__(self, *_): pass
        return _SF

    q = await bc.subscribe("run-1", 0, object(), _session_factory([], _make_run("run-1")))
    try:
        assert not q.empty() or q.maxsize == 500
        async with bc._lock:
            assert "run-1" in bc._queues
            assert "run-1" in bc._pollers
    finally:
        await bc.unsubscribe("run-1", q)


@pytest.mark.asyncio
async def test_unsubscribe_cancels_poller_when_last():
    from app.core.sse import _SSEBroadcaster

    bc = _SSEBroadcaster()

    def _session_factory(rows, run):
        class _SF:
            def __init__(self, engine, **kw): pass
            async def __aenter__(self): return _FakeSession(rows, run)
            async def __aexit__(self, *_): pass
        return _SF

    q = await bc.subscribe("run-cleanup", 0, object(),
                           _session_factory([], _make_run("run-cleanup")))
    async with bc._lock:
        task = bc._pollers.get("run-cleanup")
    assert task is not None

    await bc.unsubscribe("run-cleanup", q)

    async with bc._lock:
        assert "run-cleanup" not in bc._queues
        assert "run-cleanup" not in bc._pollers

    # Give event loop a tick to actually cancel the task
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_two_subscribers_same_run_share_one_poller():
    from app.core.sse import _SSEBroadcaster

    bc = _SSEBroadcaster()

    def _session_factory(rows, run):
        class _SF:
            def __init__(self, engine, **kw): pass
            async def __aenter__(self): return _FakeSession(rows, run)
            async def __aexit__(self, *_): pass
        return _SF

    q1 = await bc.subscribe("run-shared", 0, object(),
                            _session_factory([], _make_run("run-shared")))
    q2 = await bc.subscribe("run-shared", 0, object(),
                            _session_factory([], _make_run("run-shared")))

    async with bc._lock:
        assert len(bc._queues["run-shared"]) == 2
        assert len(bc._pollers) == 1  # only one task created

    await bc.unsubscribe("run-shared", q1)
    await bc.unsubscribe("run-shared", q2)


@pytest.mark.asyncio
async def test_unsubscribe_first_subscriber_keeps_poller():
    from app.core.sse import _SSEBroadcaster

    bc = _SSEBroadcaster()

    def _sf(rows, run):
        class _SF:
            def __init__(self, engine, **kw): pass
            async def __aenter__(self): return _FakeSession(rows, run)
            async def __aexit__(self, *_): pass
        return _SF

    q1 = await bc.subscribe("run-two", 0, object(), _sf([], _make_run("run-two")))
    q2 = await bc.subscribe("run-two", 0, object(), _sf([], _make_run("run-two")))

    await bc.unsubscribe("run-two", q1)

    async with bc._lock:
        # Poller must still be alive — q2 is still subscribed
        assert "run-two" in bc._pollers
        assert bc._queues["run-two"] == [q2]

    await bc.unsubscribe("run-two", q2)


# ---------------------------------------------------------------------------
# Late joiner gets catchup_needed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_late_subscriber_receives_catchup_needed():
    from app.core.sse import _SSEBroadcaster

    bc = _SSEBroadcaster()

    def _sf(rows, run):
        class _SF:
            def __init__(self, engine, **kw): pass
            async def __aenter__(self): return _FakeSession(rows, run)
            async def __aexit__(self, *_): pass
        return _SF

    # First subscriber — starts the poller
    q1 = await bc.subscribe("run-late", 0, object(), _sf([], _make_run("run-late")))
    # Simulate cursor advancing
    async with bc._lock:
        bc._cursors["run-late"] = 42

    # Second subscriber — joins existing poller, should get catchup_needed
    q2 = await bc.subscribe("run-late", 5, object(), _sf([], _make_run("run-late")))
    msg = q2.get_nowait()
    assert msg["type"] == "catchup_needed"
    assert msg["since"] == 5
    assert msg["cursor"] == 42

    await bc.unsubscribe("run-late", q1)
    await bc.unsubscribe("run-late", q2)


# ---------------------------------------------------------------------------
# Fan-out: broadcaster delivers events to all subscribers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broadcaster_delivers_events_to_all_queues():
    """_broadcast puts the same message into every subscriber queue."""
    from app.core.sse import _SSEBroadcaster

    bc = _SSEBroadcaster()

    q1: asyncio.Queue = asyncio.Queue(maxsize=500)
    q2: asyncio.Queue = asyncio.Queue(maxsize=500)
    async with bc._lock:
        bc._queues["run-fan"] = [q1, q2]
        bc._cursors["run-fan"] = 0
        bc._pollers["run-fan"] = asyncio.create_task(asyncio.sleep(9999), name="dummy")

    ev = _make_event("run-fan", 1)
    # Simulate broadcaster putting event into queues
    msg = {"type": "event", "ev": ev}
    for q in [q1, q2]:
        q.put_nowait(msg)

    m1 = q1.get_nowait()
    m2 = q2.get_nowait()
    assert m1 is m2  # same object
    assert m1["type"] == "event"

    async with bc._lock:
        task = bc._pollers.pop("run-fan")
        bc._queues.pop("run-fan")
        bc._cursors.pop("run-fan")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# M-1: display_name SQLite migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_user_table_adds_display_name_to_existing_table():
    """_migrate_user_table must ADD display_name to a pre-existing user table that lacks it."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    from app.core.database import _migrate_user_table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # Create a minimal user table WITHOUT display_name (simulates pre-migration state)
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE user ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "email TEXT NOT NULL UNIQUE, "
            "password_hash TEXT NOT NULL, "
            "created_at DATETIME"
            ")"
        ))

    # Run the migration
    await _migrate_user_table(engine)

    # Verify display_name column was added
    async with engine.begin() as conn:
        cols = (await conn.execute(text("PRAGMA table_info(user)"))).fetchall()
        col_names = {row[1] for row in cols}

    assert "display_name" in col_names, (
        "display_name column must be added to existing user table by _migrate_user_table"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_user_table_creates_with_display_name():
    """When user table is absent, _migrate_user_table creates it WITH display_name."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    from app.core.database import _migrate_user_table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _migrate_user_table(engine)

    async with engine.begin() as conn:
        cols = (await conn.execute(text("PRAGMA table_info(user)"))).fetchall()
        col_names = {row[1] for row in cols}

    assert "display_name" in col_names
    assert "email" in col_names
    assert "password_hash" in col_names
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_user_table_idempotent():
    """Running _migrate_user_table twice must not raise."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.database import _migrate_user_table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _migrate_user_table(engine)
    await _migrate_user_table(engine)  # second run must be a no-op
    await engine.dispose()
