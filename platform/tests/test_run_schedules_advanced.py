"""Advanced Run Schedule tests — gaps not covered by test_run_schedules.py.

Covers:
  RS01 — Invalid cron expression rejected with 422
  RS02 — Cron field boundary validation (invalid day/month/etc)
  RS03 — Cross-workspace isolation: key B cannot read/write/delete workspace A schedules
  RS04 — Update partial fields; untouched fields preserved
  RS05 — Enable/disable toggle via PATCH
  RS06 — Delete non-existent schedule returns 404
  RS07 — Schedule list filtered to own workspace only
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, Workspace


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _prefix(s: str) -> str:
    return _sha256(s)[:16]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _make_ws_key(session: AsyncSession, slug: str, raw_key: str) -> tuple:
    ws = Workspace(name=slug, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    key = ApiKey(
        label=f"key-{slug}",
        key_hash=_sha256(raw_key),
        key_prefix=_prefix(raw_key),
        workspace_id=ws.id,
        role="admin",
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ws, key


_DEFAULT_KEY = "schedadv-default-" + secrets.token_hex(4)
_BASE_BODY = {
    "name": "Nightly Build",
    "goal": "run regression suite",
    "cron_expr": "0 2 * * *",
}


@pytest.fixture
async def sched_ws(session):
    raw = _DEFAULT_KEY
    ws, key = await _make_ws_key(session, "sched-adv-ws", raw)
    return ws, raw


def _h(raw_key: str = _DEFAULT_KEY) -> dict:
    return {"X-Api-Key": raw_key}


# ---------------------------------------------------------------------------
# RS01 — Invalid cron expression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_cron", [
    "",           # empty
    "not-a-cron",
    "60 * * * *", # minute=60 out of range
    "* * * * * *", # too many fields (6 without seconds support)
    "abc def ghi jkl mno",
])
async def test_rs01_invalid_cron_rejected(client, sched_ws, bad_cron):
    ws, raw = sched_ws
    r = await client.post(
        "/run-schedules/",
        json={**_BASE_BODY, "cron_expr": bad_cron},
        headers=_h(raw),
    )
    assert r.status_code == 422, f"Expected 422 for cron {bad_cron!r}, got {r.status_code}"


# ---------------------------------------------------------------------------
# RS02 — Valid cron expressions accepted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("good_cron", [
    "0 * * * *",      # every hour
    "*/15 * * * *",   # every 15 min
    "0 9 * * 1",      # every Monday 9am
    "0 0 1 * *",      # first of month
])
async def test_rs02_valid_cron_accepted(client, sched_ws, good_cron):
    ws, raw = sched_ws
    r = await client.post(
        "/run-schedules/",
        json={**_BASE_BODY, "cron_expr": good_cron},
        headers=_h(raw),
    )
    assert r.status_code == 201, f"Expected 201 for cron {good_cron!r}, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# RS03 — Cross-workspace isolation
# ---------------------------------------------------------------------------

@pytest.fixture
async def two_sched_workspaces(session):
    raw_a = "sched-iso-a-" + secrets.token_hex(6)
    raw_b = "sched-iso-b-" + secrets.token_hex(6)
    ws_a, _ = await _make_ws_key(session, "sched-iso-a", raw_a)
    ws_b, _ = await _make_ws_key(session, "sched-iso-b", raw_b)
    return ws_a, raw_a, ws_b, raw_b


@pytest.mark.asyncio
async def test_rs03_key_b_cannot_list_workspace_a_schedules(client, two_sched_workspaces):
    ws_a, raw_a, ws_b, raw_b = two_sched_workspaces

    # Create a schedule in workspace A
    await client.post("/run-schedules/", json=_BASE_BODY, headers=_h(raw_a))

    # Workspace B lists schedules — must not see workspace A's
    r = await client.get("/run-schedules/", headers=_h(raw_b))
    assert r.status_code == 200
    ids = [s.get("workspace_id") for s in r.json()]
    assert ws_a.id not in ids, "Workspace B must not see workspace A schedules"


@pytest.mark.asyncio
async def test_rs03_key_b_cannot_delete_workspace_a_schedule(client, two_sched_workspaces):
    ws_a, raw_a, ws_b, raw_b = two_sched_workspaces

    r_create = await client.post("/run-schedules/", json=_BASE_BODY, headers=_h(raw_a))
    assert r_create.status_code == 201
    sched_id = r_create.json()["id"]

    r_del = await client.delete(f"/run-schedules/{sched_id}", headers=_h(raw_b))
    assert r_del.status_code in (403, 404), (
        f"Key B must not delete workspace A's schedule, got {r_del.status_code}"
    )


@pytest.mark.asyncio
async def test_rs03_key_b_cannot_patch_workspace_a_schedule(client, two_sched_workspaces):
    ws_a, raw_a, ws_b, raw_b = two_sched_workspaces

    r_create = await client.post("/run-schedules/", json=_BASE_BODY, headers=_h(raw_a))
    assert r_create.status_code == 201
    sched_id = r_create.json()["id"]

    r_patch = await client.patch(
        f"/run-schedules/{sched_id}",
        json={"name": "Hijacked"},
        headers=_h(raw_b),
    )
    assert r_patch.status_code in (403, 404)


# ---------------------------------------------------------------------------
# RS04 — Partial update preserves untouched fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rs04_partial_patch_preserves_other_fields(client, sched_ws):
    ws, raw = sched_ws
    r_create = await client.post(
        "/run-schedules/",
        json={**_BASE_BODY, "goal": "original goal", "cron_expr": "0 1 * * *"},
        headers=_h(raw),
    )
    assert r_create.status_code == 201
    sched_id = r_create.json()["id"]

    r_patch = await client.patch(
        f"/run-schedules/{sched_id}",
        json={"name": "New Name"},
        headers=_h(raw),
    )
    assert r_patch.status_code == 200
    body = r_patch.json()
    assert body["name"] == "New Name"
    assert body["goal"] == "original goal"
    assert body["cron_expr"] == "0 1 * * *"


# ---------------------------------------------------------------------------
# RS05 — Enable/disable toggle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rs05_disable_schedule(client, sched_ws):
    ws, raw = sched_ws
    r_create = await client.post("/run-schedules/", json=_BASE_BODY, headers=_h(raw))
    assert r_create.status_code == 201
    sched_id = r_create.json()["id"]
    assert r_create.json()["enabled"] is True

    r_patch = await client.patch(
        f"/run-schedules/{sched_id}",
        json={"enabled": False},
        headers=_h(raw),
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["enabled"] is False


@pytest.mark.asyncio
async def test_rs05_re_enable_schedule(client, sched_ws):
    ws, raw = sched_ws
    r_create = await client.post("/run-schedules/", json=_BASE_BODY, headers=_h(raw))
    sched_id = r_create.json()["id"]

    await client.patch(f"/run-schedules/{sched_id}", json={"enabled": False}, headers=_h(raw))
    r_patch = await client.patch(f"/run-schedules/{sched_id}", json={"enabled": True}, headers=_h(raw))
    assert r_patch.status_code == 200
    assert r_patch.json()["enabled"] is True


# ---------------------------------------------------------------------------
# RS06 — Delete non-existent schedule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rs06_delete_nonexistent_returns_404(client, sched_ws):
    _, raw = sched_ws
    r = await client.delete("/run-schedules/99999999", headers=_h(raw))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# RS07 — Schedule list is workspace-scoped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rs07_list_only_shows_own_schedules(client, two_sched_workspaces):
    ws_a, raw_a, ws_b, raw_b = two_sched_workspaces

    # Create 2 schedules in A, 1 in B
    await client.post("/run-schedules/", json={**_BASE_BODY, "name": "A1"}, headers=_h(raw_a))
    await client.post("/run-schedules/", json={**_BASE_BODY, "name": "A2"}, headers=_h(raw_a))
    await client.post("/run-schedules/", json={**_BASE_BODY, "name": "B1"}, headers=_h(raw_b))

    r_a = await client.get("/run-schedules/", headers=_h(raw_a))
    r_b = await client.get("/run-schedules/", headers=_h(raw_b))

    names_a = {s["name"] for s in r_a.json()}
    names_b = {s["name"] for s in r_b.json()}

    assert "A1" in names_a
    assert "A2" in names_a
    assert "B1" not in names_a

    assert "B1" in names_b
    assert "A1" not in names_b
    assert "A2" not in names_b
