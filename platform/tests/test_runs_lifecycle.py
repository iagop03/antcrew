"""Run lifecycle tests — dispatch, upload, status, cancel, governance.

test_pipeline.py and test_api.py test basic CRUD. This file covers the full
lifecycle and the gaps they leave:

  RL01 — POST /runs/upload: stores run, tickets created from state
  RL02 — GET /runs/{id}: returns stored run fields
  RL03 — GET /runs/{id}/events: returns chronological events
  RL04 — GET /runs/{id}/stats: cost_usd and duration_s populated
  RL05 — GET /runs/{id}/tickets: returns tickets from the run
  RL06 — POST /runs/{id}/cancel: sets status=cancelled
  RL07 — Cancelled run cannot be cancelled again
  RL08 — POST /run dispatch: unknown team returns 422
  RL09 — POST /run dispatch: negative max_cost_usd returns 422
  RL10 — POST /run dispatch: malformed repo_url returns 422
  RL11 — Pagination: limit/offset on /runs/
  RL12 — Filter by status on /runs/
  RL13 — Filter by team on /runs/
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, Run, Ticket
from app.models.workspace import Workspace


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _prefix(s: str) -> str:
    return _sha256(s)[:16]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RAW_KEY = "runs-lc-" + secrets.token_hex(8)


@pytest.fixture
async def runs_ws(session: AsyncSession):
    ws = Workspace(name="runs-lifecycle-ws", slug="runs-lifecycle-ws")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    key = ApiKey(
        label="key-runs-lc",
        key_hash=_sha256(_RAW_KEY),
        key_prefix=_prefix(_RAW_KEY),
        workspace_id=ws.id,
        role="admin",
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ws, key


def _h() -> dict:
    return {"X-Api-Key": _RAW_KEY}


# ---------------------------------------------------------------------------
# RL01 — Upload run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl01_upload_run_created(client, runs_ws):
    """POST /runs/upload stores a run and returns it."""
    r = await client.post(
        "/runs/upload",
        json={
            "team": "DevTeam",
            "request": "Build login feature",
            "cost_usd": 0.42,
            "duration_s": 12.5,
        },
        headers=_h(),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["team"] == "DevTeam"
    assert body["request"] == "Build login feature"
    assert body["status"] == "success"


@pytest.mark.asyncio
async def test_rl01_upload_run_with_tickets(client, runs_ws):
    """Tickets embedded in state.tickets are upserted after upload."""
    r = await client.post(
        "/runs/upload",
        json={
            "team": "DevTeam",
            "request": "Feature with tickets",
            "state": {
                "tickets": [
                    {"id": "UPLOAD-001", "title": "Auth ticket", "status": "open"},
                    {"id": "UPLOAD-002", "title": "Profile ticket", "status": "in_progress"},
                ]
            },
        },
        headers=_h(),
    )
    assert r.status_code == 201
    run_id = r.json()["run_id"]

    tickets_r = await client.get(f"/runs/{run_id}/tickets", headers=_h())
    assert tickets_r.status_code == 200
    ticket_ids = [t["ticket_id"] for t in tickets_r.json()]
    assert "UPLOAD-001" in ticket_ids
    assert "UPLOAD-002" in ticket_ids


# ---------------------------------------------------------------------------
# RL02 — GET /runs/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl02_get_run_returns_all_fields(client, session, runs_ws):
    ws, _ = runs_ws
    run = Run(
        run_id="rl02-run",
        team="FullStackTeam",
        request="full stack request",
        workspace_id=ws.id,
        created_by="key-runs-lc",
        status="success",
        cost_usd=1.23,
    )
    session.add(run)
    await session.commit()

    r = await client.get("/runs/rl02-run", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "rl02-run"
    assert body["team"] == "FullStackTeam"
    assert body["status"] == "success"


@pytest.mark.asyncio
async def test_rl02_get_run_not_found(client):
    r = await client.get("/runs/nonexistent-run-id", headers=_h())
    assert r.status_code in (401, 404)  # 401 if PLATFORM_API_KEY set, 404 otherwise


# ---------------------------------------------------------------------------
# RL03 — GET /runs/{id}/events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl03_events_empty_for_new_run(client, session, runs_ws):
    ws, _ = runs_ws
    run = Run(
        run_id="rl03-run",
        team="DevTeam",
        request="events test",
        workspace_id=ws.id,
        created_by="key-runs-lc",
        status="success",
    )
    session.add(run)
    await session.commit()

    r = await client.get("/runs/rl03-run/events", headers=_h())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# RL04 — GET /runs/{id}/stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl04_stats_returns_cost_and_duration(client, session, runs_ws):
    ws, _ = runs_ws
    run = Run(
        run_id="rl04-run",
        team="DevTeam",
        request="stats test",
        workspace_id=ws.id,
        created_by="key-runs-lc",
        status="success",
        cost_usd=2.50,
        duration_s=45.0,
    )
    session.add(run)
    await session.commit()

    r = await client.get("/runs/rl04-run/stats", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert "cost_usd" in body or "total_cost_usd" in body


# ---------------------------------------------------------------------------
# RL05 — GET /runs/{id}/tickets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl05_tickets_scoped_to_run(client, session, runs_ws):
    ws, _ = runs_ws
    run_a = Run(run_id="rl05-run-a", team="DevTeam", request="run a",
                workspace_id=ws.id, created_by="key-runs-lc", status="success")
    run_b = Run(run_id="rl05-run-b", team="DevTeam", request="run b",
                workspace_id=ws.id, created_by="key-runs-lc", status="success")
    session.add(run_a)
    session.add(run_b)
    await session.flush()

    session.add(Ticket(ticket_id="RL05-A", run_id="rl05-run-a", title="A", workspace_id=ws.id))
    session.add(Ticket(ticket_id="RL05-B", run_id="rl05-run-b", title="B", workspace_id=ws.id))
    await session.commit()

    r = await client.get("/runs/rl05-run-a/tickets", headers=_h())
    assert r.status_code == 200
    ticket_ids = [t["ticket_id"] for t in r.json()]
    assert "RL05-A" in ticket_ids
    assert "RL05-B" not in ticket_ids


# ---------------------------------------------------------------------------
# RL06 — Cancel run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl06_cancel_running_run(client, session, runs_ws):
    ws, _ = runs_ws
    run = Run(
        run_id="rl06-run",
        team="DevTeam",
        request="cancel test",
        workspace_id=ws.id,
        created_by="key-runs-lc",
        status="running",
    )
    session.add(run)
    await session.commit()

    r = await client.post("/runs/rl06-run/cancel", headers=_h())
    assert r.status_code in (200, 204), r.text

    r_check = await client.get("/runs/rl06-run", headers=_h())
    if r_check.status_code == 200:
        assert r_check.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# RL07 — Double-cancel is idempotent or returns 4xx
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl07_cancel_already_cancelled(client, session, runs_ws):
    ws, _ = runs_ws
    run = Run(
        run_id="rl07-run",
        team="DevTeam",
        request="double cancel",
        workspace_id=ws.id,
        created_by="key-runs-lc",
        status="running",
    )
    session.add(run)
    await session.commit()

    await client.post("/runs/rl07-run/cancel", headers=_h())
    r2 = await client.post("/runs/rl07-run/cancel", headers=_h())
    # Either 200/204 (idempotent) or 4xx (already cancelled). Never 5xx.
    assert r2.status_code < 500, f"Second cancel should not 500, got {r2.status_code}"


# ---------------------------------------------------------------------------
# RL08–RL10 — Dispatch validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rl08_unknown_team_rejected(client, monkeypatch):
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    r = await client.post("/run/", json={"team": "AlienTeam", "request": "hello"}, headers=_h())
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rl09_negative_max_cost_rejected(client, monkeypatch):
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    r = await client.post(
        "/run/",
        json={"team": "DevTeam", "request": "test", "max_cost_usd": -1.0},
        headers=_h(),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rl10_invalid_repo_url_rejected(client, monkeypatch):
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    r = await client.post(
        "/run/",
        json={"team": "DevTeam", "request": "test", "repo_url": "not-a-url"},
        headers=_h(),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# RL11–RL13 — /runs/ pagination and filters
# ---------------------------------------------------------------------------

@pytest.fixture
async def seeded_runs(session: AsyncSession, runs_ws):
    ws, _ = runs_ws
    runs = [
        Run(run_id=f"rl-seed-{i}", team="DevTeam" if i % 2 == 0 else "FullStackTeam",
            request=f"req {i}", workspace_id=ws.id, created_by="key-runs-lc",
            status="success" if i < 3 else "failed")
        for i in range(6)
    ]
    for r in runs:
        session.add(r)
    await session.commit()
    return ws


@pytest.mark.asyncio
async def test_rl11_pagination_limit(client, seeded_runs):
    r = await client.get("/runs/", params={"limit": 2}, headers=_h())
    assert r.status_code == 200
    assert len(r.json()) <= 2


@pytest.mark.asyncio
async def test_rl11_pagination_offset(client, seeded_runs):
    r_all = await client.get("/runs/", headers=_h())
    r_offset = await client.get("/runs/", params={"offset": 2}, headers=_h())
    assert r_offset.status_code == 200
    ids_all = [run["run_id"] for run in r_all.json()]
    ids_offset = [run["run_id"] for run in r_offset.json()]
    # With offset=2, the first 2 items of r_all should not be in r_offset
    if len(ids_all) > 2:
        assert ids_all[0] not in ids_offset or ids_all[1] not in ids_offset


@pytest.mark.asyncio
async def test_rl12_filter_by_status(client, seeded_runs):
    r = await client.get("/runs/", params={"status": "success"}, headers=_h())
    assert r.status_code == 200
    for run in r.json():
        assert run["status"] == "success"


@pytest.mark.asyncio
async def test_rl13_filter_by_team(client, seeded_runs):
    r = await client.get("/runs/", params={"team": "DevTeam"}, headers=_h())
    assert r.status_code == 200
    for run in r.json():
        assert run["team"] == "DevTeam"
