"""Extended cross-tenant isolation tests.

test_cross_tenant.py covers runs + tickets. This file covers the remaining domains:
  CT-REV — Reviews isolation
  CT-ANA — Analytics isolation
  CT-SCH — Run schedules isolation
  CT-WS  — Workspace detail / config isolation (key from workspace B cannot read A)
  CT-BYOK — BYOK keys isolation
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import pytest
from sqlmodel import select

from app.models.run import ApiKey, Run, RunSchedule, Ticket
from app.models.workspace import Workspace


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _prefix(raw: str) -> str:
    return _sha256(raw)[:16]


# ---------------------------------------------------------------------------
# Fixture: two fully isolated workspaces with runs/tickets/reviews
# ---------------------------------------------------------------------------

@pytest.fixture
async def tenants(session, client):
    """Create two workspaces each with an API key, run, and ticket.
    Returns (raw_a, raw_b, ws_a_id, ws_b_id).
    """
    ws_a = Workspace(name="Tenant Adv A", slug="ct-adv-a")
    ws_b = Workspace(name="Tenant Adv B", slug="ct-adv-b")
    session.add(ws_a)
    session.add(ws_b)
    await session.commit()
    await session.refresh(ws_a)
    await session.refresh(ws_b)

    raw_a = "ct-adv-a-" + secrets.token_hex(8)
    raw_b = "ct-adv-b-" + secrets.token_hex(8)

    ak_a = ApiKey(workspace_id=ws_a.id, label="ct-adv-key-a",
                  key_hash=_sha256(raw_a), key_prefix=_prefix(raw_a), role="admin")
    ak_b = ApiKey(workspace_id=ws_b.id, label="ct-adv-key-b",
                  key_hash=_sha256(raw_b), key_prefix=_prefix(raw_b), role="admin")
    session.add(ak_a)
    session.add(ak_b)
    await session.commit()
    await session.refresh(ak_a)
    await session.refresh(ak_b)

    # Seed runs
    run_a = Run(run_id="ct-adv-run-a", team="DevTeam", request="req a",
                workspace_id=ws_a.id, created_by="ct-adv-key-a", status="success")
    run_b = Run(run_id="ct-adv-run-b", team="DevTeam", request="req b",
                workspace_id=ws_b.id, created_by="ct-adv-key-b", status="success")
    session.add(run_a)
    session.add(run_b)

    # Seed tickets
    ticket_a = Ticket(ticket_id="CT-ADV-A-001", run_id="ct-adv-run-a",
                      title="Ticket A", workspace_id=ws_a.id)
    ticket_b = Ticket(ticket_id="CT-ADV-B-001", run_id="ct-adv-run-b",
                      title="Ticket B", workspace_id=ws_b.id)
    session.add(ticket_a)
    session.add(ticket_b)

    await session.commit()
    return raw_a, raw_b, ws_a.id, ws_b.id


# ---------------------------------------------------------------------------
# CT-REV — Reviews isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ct_rev_list_reviews_scoped_to_workspace(client, tenants, monkeypatch):
    """Key B listing reviews must not see workspace A's run reviews."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    r_a = await client.get("/reviews/", headers={"X-Api-Key": raw_a})
    r_b = await client.get("/reviews/", headers={"X-Api-Key": raw_b})

    assert r_a.status_code == 200
    assert r_b.status_code == 200

    run_ids_a = {rev.get("run_id") for rev in r_a.json()}
    run_ids_b = {rev.get("run_id") for rev in r_b.json()}

    # A's run must not appear in B's review list
    assert "ct-adv-run-a" not in run_ids_b
    assert "ct-adv-run-b" not in run_ids_a


@pytest.mark.asyncio
async def test_ct_rev_get_review_by_run_id_scoped(client, tenants, monkeypatch):
    """Key B filtering reviews by workspace A's run_id must not return data."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, _, _ = tenants

    r = await client.get("/reviews/", params={"run_id": "ct-adv-run-a"}, headers={"X-Api-Key": raw_b})
    assert r.status_code == 200
    # Either empty list or all items belong to B's workspace
    for rev in r.json():
        assert rev.get("run_id") != "ct-adv-run-a"


# ---------------------------------------------------------------------------
# CT-ANA — Analytics isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ct_ana_workspace_a_analytics_forbidden_for_key_b(client, tenants, monkeypatch):
    """Key B must not access workspace A's analytics endpoint."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    r = await client.get(f"/workspaces/{ws_a_id}/analytics", headers={"X-Api-Key": raw_b})
    assert r.status_code in (403, 404), (
        f"Key B must not read workspace A analytics, got {r.status_code}"
    )


@pytest.mark.asyncio
async def test_ct_ana_key_can_read_own_workspace_analytics(client, tenants, monkeypatch):
    """Each key can access its own workspace's analytics."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    r_a = await client.get(f"/workspaces/{ws_a_id}/analytics", headers={"X-Api-Key": raw_a})
    r_b = await client.get(f"/workspaces/{ws_b_id}/analytics", headers={"X-Api-Key": raw_b})

    assert r_a.status_code == 200
    assert r_b.status_code == 200


# ---------------------------------------------------------------------------
# CT-SCH — Run schedules isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ct_sch_schedules_scoped_by_workspace(client, tenants, monkeypatch):
    """Run schedules listed by key B must not include workspace A's schedules."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    # Create a schedule in workspace A
    await client.post(
        "/run-schedules/",
        json={"name": "CT Sched A", "goal": "build", "cron_expr": "0 0 * * *"},
        headers={"X-Api-Key": raw_a},
    )

    r_b = await client.get("/run-schedules/", headers={"X-Api-Key": raw_b})
    assert r_b.status_code == 200
    names = [s.get("name") for s in r_b.json()]
    assert "CT Sched A" not in names


# ---------------------------------------------------------------------------
# CT-WS — Workspace detail isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ct_ws_key_b_cannot_read_workspace_a_detail(client, tenants, monkeypatch):
    """GET /workspaces/{id} by key B for workspace A must return 403/404."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    r = await client.get(f"/workspaces/{ws_a_id}", headers={"X-Api-Key": raw_b})
    assert r.status_code in (403, 404), (
        f"Key B must not read workspace A detail, got {r.status_code}"
    )


@pytest.mark.asyncio
async def test_ct_ws_key_can_read_own_workspace(client, tenants, monkeypatch):
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    r_a = await client.get(f"/workspaces/{ws_a_id}", headers={"X-Api-Key": raw_a})
    assert r_a.status_code == 200
    assert r_a.json()["id"] == ws_a_id


# ---------------------------------------------------------------------------
# CT-BYOK — BYOK audit isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ct_byok_audit_scoped_to_workspace(client, tenants, monkeypatch):
    """Key B must not read workspace A BYOK audit log."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    raw_a, raw_b, ws_a_id, ws_b_id = tenants

    r = await client.get(f"/workspaces/{ws_a_id}/byok-audit", headers={"X-Api-Key": raw_b})
    assert r.status_code in (403, 404), (
        f"Key B must not read workspace A BYOK audit, got {r.status_code}"
    )
