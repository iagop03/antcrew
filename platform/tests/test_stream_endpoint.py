"""Tests for GET /runs/{run_id}/stream — SSE event replay endpoint."""
from __future__ import annotations

import hashlib
import os

os.environ.setdefault("ANTCREW_TESTING", "1")

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.main import app
from app.core.database import get_session
from app.models.run import ApiKey, Run, Workspace
from app.models.run import Event as DBEvent


# ---------------------------------------------------------------------------
# stream_client fixture — file-based SQLite shared with SSE generator
# ---------------------------------------------------------------------------

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture
async def stream_client(tmp_path, monkeypatch):
    """AsyncClient + shared SQLite DB for SSE tests.

    The SSE generator opens its own DB connection (via _db_engine), so we need
    a file-based SQLite that all connections can access, and we monkeypatch
    app.core.database.engine to point to it.
    """
    import app.core.database as _db_mod

    db_path = tmp_path / "stream_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    monkeypatch.setattr(_db_mod, "engine", engine)

    async with AsyncSession(engine, expire_on_commit=False) as sess:
        app.dependency_overrides[get_session] = lambda: sess

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield sess, c

        app.dependency_overrides.pop(get_session, None)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


async def _seed(sess: AsyncSession, raw_key: str = "stream-test-key") -> tuple[Workspace, Run]:
    """Insert a workspace, API key, and a completed run."""
    ws = Workspace(name="stream-ws", slug="stream-ws")
    sess.add(ws)
    await sess.commit()
    await sess.refresh(ws)

    key_hash = _sha256(raw_key)
    key = ApiKey(
        label="stream-key",
        key_hash=key_hash,
        key_prefix=key_hash[:16],
        workspace_id=ws.id,
        role="admin",
    )
    sess.add(key)
    await sess.commit()

    run = Run(
        run_id="stream-run-001",
        team="DevTeam",
        request="Build auth",
        status="success",
        workspace_id=ws.id,
    )
    sess.add(run)
    await sess.commit()
    await sess.refresh(run)
    return ws, run


def _h(raw_key: str = "stream-test-key") -> dict:
    return {"X-Api-Key": raw_key}


# ---------------------------------------------------------------------------
# Error cases — no SSE body needed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_404_unknown_run(stream_client):
    sess, client = stream_client
    ws = Workspace(name="ws404", slug="ws404")
    sess.add(ws)
    await sess.commit()
    await sess.refresh(ws)
    raw = "key404"
    h = _sha256(raw)
    key = ApiKey(label="k", key_hash=h, key_prefix=h[:16], workspace_id=ws.id, role="admin")
    sess.add(key)
    await sess.commit()

    r = await client.get("/runs/does-not-exist/stream", headers={"X-Api-Key": raw})
    assert r.status_code == 404



# ---------------------------------------------------------------------------
# Content-Type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_content_type_is_sse(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)
    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert "text/event-stream" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Terminal run — run.end is emitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_emits_run_end_for_success(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)
    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert r.status_code == 200
    assert "run.end" in r.text
    assert run.run_id in r.text


@pytest.mark.asyncio
async def test_stream_run_end_includes_status(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)
    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert '"status": "success"' in r.text or '"status":"success"' in r.text


@pytest.mark.asyncio
async def test_stream_emits_run_end_for_error_status(stream_client):
    sess, client = stream_client
    ws = Workspace(name="ws-err", slug="ws-err")
    sess.add(ws)
    await sess.commit()
    await sess.refresh(ws)
    raw = "err-key"
    h = _sha256(raw)
    sess.add(ApiKey(label="k", key_hash=h, key_prefix=h[:16], workspace_id=ws.id, role="admin"))
    run = Run(run_id="err-run-001", team="DevTeam", request="Build", status="error", workspace_id=ws.id)
    sess.add(run)
    await sess.commit()

    r = await client.get("/runs/err-run-001/stream", headers={"X-Api-Key": raw})
    assert "run.end" in r.text
    assert "error" in r.text


# ---------------------------------------------------------------------------
# Events are streamed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_emits_stored_events(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)

    ev = DBEvent(
        run_id=run.run_id,
        event_type="agent.start",
        payload={"agent_name": "pm"},
        timestamp=1000.0,
        thread_id="default",
    )
    sess.add(ev)
    await sess.commit()

    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert "agent.start" in r.text
    assert "pm" in r.text


@pytest.mark.asyncio
async def test_stream_event_has_id_field(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)

    ev = DBEvent(
        run_id=run.run_id,
        event_type="agent.end",
        payload={"agent_name": "pm"},
        timestamp=1001.0,
    )
    sess.add(ev)
    await sess.commit()
    await sess.refresh(ev)

    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert f"id: {ev.id}" in r.text


@pytest.mark.asyncio
async def test_stream_multiple_events_in_order(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)

    for i, et in enumerate(["agent.start", "agent.end", "pipeline.end"]):
        sess.add(DBEvent(run_id=run.run_id, event_type=et, payload={}, timestamp=float(i)))
    await sess.commit()

    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    body = r.text
    pos_start = body.find("agent.start")
    pos_end   = body.find("agent.end")
    pos_pipe  = body.find("pipeline.end")
    assert pos_start < pos_end < pos_pipe


# ---------------------------------------------------------------------------
# Last-Event-ID replay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_last_event_id_skips_earlier_events(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)

    ev1 = DBEvent(run_id=run.run_id, event_type="agent.start", payload={"agent_name": "pm"}, timestamp=1.0)
    ev2 = DBEvent(run_id=run.run_id, event_type="agent.end",   payload={"agent_name": "pm"}, timestamp=2.0)
    sess.add(ev1)
    sess.add(ev2)
    await sess.commit()
    await sess.refresh(ev1)
    await sess.refresh(ev2)

    # Replay only from ev1 onwards → should see ev2 but NOT ev1
    r = await client.get(
        f"/runs/{run.run_id}/stream",
        headers={**_h(), "Last-Event-ID": str(ev1.id)},
    )
    body = r.text
    assert "agent.end" in body
    # ev1 was already seen; its line should not appear again
    # (events with id <= Last-Event-ID are skipped)
    assert body.count("agent.start") == 0


@pytest.mark.asyncio
async def test_stream_last_event_id_zero_returns_all_events(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)

    for et in ["agent.start", "agent.end"]:
        sess.add(DBEvent(run_id=run.run_id, event_type=et, payload={}, timestamp=1.0))
    await sess.commit()

    r = await client.get(
        f"/runs/{run.run_id}/stream",
        headers={**_h(), "Last-Event-ID": "0"},
    )
    assert "agent.start" in r.text
    assert "agent.end" in r.text


@pytest.mark.asyncio
async def test_stream_last_event_id_non_digit_is_ignored(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)

    sess.add(DBEvent(run_id=run.run_id, event_type="agent.start", payload={}, timestamp=1.0))
    await sess.commit()

    # Non-digit Last-Event-ID should be ignored (since_id stays 0)
    r = await client.get(
        f"/runs/{run.run_id}/stream",
        headers={**_h(), "Last-Event-ID": "not-a-number"},
    )
    assert r.status_code == 200
    assert "agent.start" in r.text


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_cache_control_no_cache(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)
    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert r.headers.get("cache-control") == "no-cache"


@pytest.mark.asyncio
async def test_stream_accel_buffering_header(stream_client):
    sess, client = stream_client
    _, run = await _seed(sess)
    r = await client.get(f"/runs/{run.run_id}/stream", headers=_h())
    assert r.headers.get("x-accel-buffering") == "no"
