"""Cross-tenant isolation tests (F7A).

These tests verify that the application-layer workspace scoping (ws_filter in auth.py)
prevents workspace A's API key from reading workspace B's data and vice versa.

This is the first line of defence; PostgreSQL RLS (migration 073, F12B) is the second.
Both must hold independently — if one is bypassed, the other still enforces isolation.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import pytest
from sqlmodel import select

from app.models.run import ApiKey, Run, Ticket
from app.models.workspace import Workspace


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _key_prefix(raw: str) -> str:
    return _sha256_hash(raw)[:16]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def two_workspaces(session, client):
    """Create workspaces A and B with one API key each. Returns (key_a, key_b)."""
    # Workspaces
    ws_a = Workspace(name="Tenant A", slug="tenant-a")
    ws_b = Workspace(name="Tenant B", slug="tenant-b")
    session.add(ws_a)
    session.add(ws_b)
    await session.commit()
    await session.refresh(ws_a)
    await session.refresh(ws_b)

    # Raw keys (never stored plaintext)
    raw_a = "key-tenant-a-" + secrets.token_hex(8)
    raw_b = "key-tenant-b-" + secrets.token_hex(8)

    # Legacy sha256 hash (simpler than bcrypt in tests — auth.py accepts both)
    ak_a = ApiKey(
        workspace_id=ws_a.id,
        label="key-a",
        key_hash=_sha256_hash(raw_a),
        key_prefix=_key_prefix(raw_a),
        role="write",
    )
    ak_b = ApiKey(
        workspace_id=ws_b.id,
        label="key-b",
        key_hash=_sha256_hash(raw_b),
        key_prefix=_key_prefix(raw_b),
        role="write",
    )
    session.add(ak_a)
    session.add(ak_b)
    await session.commit()

    # Seed one run per workspace
    run_a = Run(
        run_id="run-tenant-a-001",
        team="DevTeam",
        request="build feature A",
        workspace_id=ws_a.id,
        created_by="key-a",
        status="success",
    )
    run_b = Run(
        run_id="run-tenant-b-001",
        team="DevTeam",
        request="build feature B",
        workspace_id=ws_b.id,
        created_by="key-b",
        status="success",
    )
    session.add(run_a)
    session.add(run_b)

    # Seed one ticket per workspace
    ticket_a = Ticket(
        ticket_id="TIKA-001",
        run_id="run-tenant-a-001",
        title="Ticket A",
        workspace_id=ws_a.id,
    )
    ticket_b = Ticket(
        ticket_id="TIKB-001",
        run_id="run-tenant-b-001",
        title="Ticket B",
        workspace_id=ws_b.id,
    )
    session.add(ticket_a)
    session.add(ticket_b)
    await session.commit()

    return raw_a, raw_b, ws_a.id, ws_b.id


# ---------------------------------------------------------------------------
# Run isolation
# ---------------------------------------------------------------------------

async def test_workspace_a_sees_own_runs(client, two_workspaces, monkeypatch):
    """Key A can retrieve workspace A runs."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, _, _, _ = two_workspaces
    r = await client.get("/runs/", headers={"X-Api-Key": raw_a})
    assert r.status_code == 200
    run_ids = [run["run_id"] for run in r.json()]
    assert "run-tenant-a-001" in run_ids


async def test_workspace_b_cannot_see_workspace_a_runs(client, two_workspaces, monkeypatch):
    """Key B must not see workspace A's runs."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    _, raw_b, _, _ = two_workspaces
    r = await client.get("/runs/", headers={"X-Api-Key": raw_b})
    assert r.status_code == 200
    run_ids = [run["run_id"] for run in r.json()]
    assert "run-tenant-a-001" not in run_ids


async def test_workspace_a_cannot_see_workspace_b_runs(client, two_workspaces, monkeypatch):
    """Key A must not see workspace B's runs."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, _, _, _ = two_workspaces
    r = await client.get("/runs/", headers={"X-Api-Key": raw_a})
    assert r.status_code == 200
    run_ids = [run["run_id"] for run in r.json()]
    assert "run-tenant-b-001" not in run_ids


async def test_workspace_b_sees_own_runs(client, two_workspaces, monkeypatch):
    """Key B can retrieve workspace B runs."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    _, raw_b, _, _ = two_workspaces
    r = await client.get("/runs/", headers={"X-Api-Key": raw_b})
    assert r.status_code == 200
    run_ids = [run["run_id"] for run in r.json()]
    assert "run-tenant-b-001" in run_ids


# ---------------------------------------------------------------------------
# Direct run ID access (must 404, not leak data)
# ---------------------------------------------------------------------------

async def test_workspace_b_cannot_fetch_workspace_a_run_by_id(client, two_workspaces, monkeypatch):
    """GET /runs/{run_id} by key B for a workspace-A run must not return 200."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    _, raw_b, _, _ = two_workspaces
    r = await client.get("/runs/run-tenant-a-001", headers={"X-Api-Key": raw_b})
    assert r.status_code in (403, 404)


async def test_workspace_a_cannot_fetch_workspace_b_run_by_id(client, two_workspaces, monkeypatch):
    """GET /runs/{run_id} by key A for a workspace-B run must not return 200."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, _, _, _ = two_workspaces
    r = await client.get("/runs/run-tenant-b-001", headers={"X-Api-Key": raw_a})
    assert r.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Ticket isolation
# ---------------------------------------------------------------------------

async def test_workspace_b_cannot_see_workspace_a_tickets(client, two_workspaces, monkeypatch):
    """GET /tickets/ by key B must not include workspace A tickets."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    _, raw_b, _, _ = two_workspaces
    r = await client.get("/tickets/", headers={"X-Api-Key": raw_b})
    assert r.status_code == 200
    ticket_ids = [t["ticket_id"] for t in r.json()]
    assert "TIKA-001" not in ticket_ids


async def test_workspace_a_cannot_see_workspace_b_tickets(client, two_workspaces, monkeypatch):
    """GET /tickets/ by key A must not include workspace B tickets."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, _, _, _ = two_workspaces
    r = await client.get("/tickets/", headers={"X-Api-Key": raw_a})
    assert r.status_code == 200
    ticket_ids = [t["ticket_id"] for t in r.json()]
    assert "TIKB-001" not in ticket_ids
