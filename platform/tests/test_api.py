"""API smoke tests for runs, tickets, and health endpoints."""
from __future__ import annotations

import pytest

from app.models.run import Run, Ticket


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_runs_empty(client):
    r = await client.get("/runs/")
    assert r.status_code == 200
    assert r.json() == []


async def test_runs_with_data(client, session):
    run = Run(
        run_id="abc123def456",
        thread_id="t1",
        team="DevTeam",
        request="Build auth",
        status="success",
        cost_usd=0.05,
    )
    session.add(run)
    await session.commit()

    r = await client.get("/runs/")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["run_id"] == "abc123def456"
    assert data[0]["team"] == "DevTeam"


async def test_run_detail_not_found(client):
    r = await client.get("/runs/nonexistent")
    assert r.status_code == 404


async def test_run_detail_found(client, session):
    run = Run(run_id="r1", team="MinimalPipeline", request="Write docs", status="success")
    session.add(run)
    await session.commit()

    r = await client.get("/runs/r1")
    assert r.status_code == 200
    assert r.json()["run_id"] == "r1"


async def test_run_events_empty(client, session):
    run = Run(run_id="r2", team="ResearchTeam", request="Research JWT", status="running")
    session.add(run)
    await session.commit()

    r = await client.get("/runs/r2/events")
    assert r.status_code == 200
    assert r.json() == []


async def test_tickets_empty(client):
    r = await client.get("/tickets/")
    assert r.status_code == 200
    assert r.json() == []


async def test_tickets_with_data(client, session):
    t = Ticket(
        ticket_id="TICKET-03CE2BBF",
        run_id="r1",
        title="Create login endpoint",
        description="POST /login",
        priority="high",
        status="open",
        prd_title="Auth Module",
    )
    session.add(t)
    await session.commit()

    r = await client.get("/tickets/")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["ticket_id"] == "TICKET-03CE2BBF"


async def test_ticket_status_update(client, session):
    t = Ticket(
        ticket_id="TICKET-AABBCCDD",
        run_id="r1",
        title="Create register endpoint",
        status="open",
    )
    session.add(t)
    await session.commit()

    r = await client.patch(
        "/tickets/TICKET-AABBCCDD/status",
        json={"status": "in_progress"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"


async def test_ticket_status_invalid(client, session):
    t = Ticket(ticket_id="TICKET-XYZ", run_id="r1", title="x", status="open")
    session.add(t)
    await session.commit()

    r = await client.patch("/tickets/TICKET-XYZ/status", json={"status": "invalid"})
    assert r.status_code == 422


async def test_runs_filter_by_team(client, session):
    session.add(Run(run_id="r1", team="DevTeam", request="x", status="success"))
    session.add(Run(run_id="r2", team="ResearchTeam", request="y", status="success"))
    await session.commit()

    r = await client.get("/runs/?team=DevTeam")
    assert r.status_code == 200
    data = r.json()
    assert all(d["team"] == "DevTeam" for d in data)


async def test_runs_filter_by_status(client, session):
    session.add(Run(run_id="r1", team="DevTeam", request="x", status="success"))
    session.add(Run(run_id="r2", team="DevTeam", request="y", status="running"))
    await session.commit()

    r = await client.get("/runs/?status=running")
    data = r.json()
    assert all(d["status"] == "running" for d in data)


# ---------------------------------------------------------------------------
# PL-P01 — Pagination: GET /runs/?limit=&offset=
# ---------------------------------------------------------------------------

async def test_runs_pagination_limit(client, session):
    """GET /runs/?limit=2 returns exactly 2 runs when more exist."""
    for i in range(5):
        session.add(Run(run_id=f"pg-r{i}", team="DevTeam", request="x", status="success"))
    await session.commit()

    r = await client.get("/runs/?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_runs_pagination_offset_disjoint(client, session):
    """Consecutive pages must contain disjoint sets of run IDs."""
    for i in range(6):
        session.add(Run(run_id=f"off-r{i}", team="DevTeam", request="x", status="success"))
    await session.commit()

    page1 = await client.get("/runs/?limit=3&offset=0")
    page2 = await client.get("/runs/?limit=3&offset=3")
    assert page1.status_code == 200
    assert page2.status_code == 200

    ids1 = {d["run_id"] for d in page1.json()}
    ids2 = {d["run_id"] for d in page2.json()}
    assert ids1.isdisjoint(ids2), "Paginated pages must not overlap"


# ---------------------------------------------------------------------------
# PL-P02 — Run events: chronological ordering
# ---------------------------------------------------------------------------

async def test_run_events_chronological_order(client, session):
    """Events for a run must be returned oldest-first (chronological)."""
    from datetime import datetime, timedelta, timezone

    try:
        from app.models.run import RunEvent
    except ImportError:
        pytest.skip("RunEvent model not available")

    run = Run(run_id="ev-order-r", team="DevTeam", request="x", status="running")
    session.add(run)
    await session.commit()

    base = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(4):
        session.add(RunEvent(
            run_id="ev-order-r",
            agent="pm",
            event_type="started",
            data="{}",
            created_at=base + timedelta(seconds=i),
        ))
    await session.commit()

    r = await client.get("/runs/ev-order-r/events")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 4

    timestamps = [e.get("created_at") for e in events if e.get("created_at")]
    for i in range(len(timestamps) - 1):
        assert timestamps[i] <= timestamps[i + 1], "Events must be in chronological order"
