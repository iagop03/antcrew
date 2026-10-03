"""GDPR Art. 17 erasure endpoints — test suite.

GD01  DELETE /admin/workspaces/{id} → 404 for unknown workspace
GD02  DELETE /admin/workspaces/{id} → 200 and deletes workspace row
GD03  DELETE /admin/workspaces/{id} → response includes row counts
GD04  DELETE /admin/workspaces/{id} → also deletes child runs
GD05  DELETE /admin/workspaces/{id} → also deletes api_keys for workspace
GD06  POST /admin/users/{id}/erase → 404 for unknown user
GD07  POST /admin/users/{id}/erase → 409 when already erased
GD08  POST /admin/users/{id}/erase → anonymises email and display_name
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.admin_auth import require_platform_admin
from app.main import app
from app.models.workspace import Workspace
from app.models.auth import User, ApiKey
from app.models.run import Run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _admin_stub():
    return {"id": 1, "email": "admin@test.com", "is_platform_admin": True}


async def _make_workspace(session: AsyncSession) -> Workspace:
    ws = Workspace(
        name="gdpr-ws",
        slug=f"gdpr-ws-{uuid.uuid4().hex[:6]}",
    )
    session.add(ws)
    await session.flush()
    return ws


async def _make_run(session: AsyncSession, workspace_id: int) -> Run:
    run = Run(
        run_id=uuid.uuid4().hex,
        thread_id=uuid.uuid4().hex,
        team="TestTeam",
        request="some personal data in here",
        status="done",
        cost_usd=0.01,
        workspace_id=workspace_id,
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    session.add(run)
    await session.flush()
    return run


async def _make_api_key(session: AsyncSession, workspace_id: int) -> ApiKey:
    key = ApiKey(
        key_hash=uuid.uuid4().hex,
        label="test-key",
        workspace_id=workspace_id,
        role="admin",
    )
    session.add(key)
    await session.flush()
    return key


async def _make_user(session: AsyncSession, email: str | None = None) -> User:
    user = User(
        email=email or f"user-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test User",
        password_hash="bcrypt-hash",
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _override_admin():
    app.dependency_overrides[require_platform_admin] = _admin_stub
    yield
    app.dependency_overrides.pop(require_platform_admin, None)


# ---------------------------------------------------------------------------
# GD01 — workspace not found
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd01_delete_workspace_not_found(client):
    r = await client.delete("/admin/workspaces/999999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GD02 — workspace row is deleted
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd02_delete_workspace_removes_row(client, session):
    ws = await _make_workspace(session)
    await session.commit()
    ws_id = ws.id

    r = await client.delete(f"/admin/workspaces/{ws_id}")
    assert r.status_code == 200

    session.expire_all()  # flush identity map so next get hits the DB
    remaining = await session.get(Workspace, ws_id)
    assert remaining is None


# ---------------------------------------------------------------------------
# GD03 — response includes row counts
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd03_delete_workspace_response_shape(client, session):
    ws = await _make_workspace(session)
    await session.commit()

    r = await client.delete(f"/admin/workspaces/{ws.id}")
    data = r.json()

    assert data["workspace_id"] == ws.id
    assert "deleted_at" in data
    assert "rows_deleted" in data
    assert data["rows_deleted"]["workspace"] == 1


# ---------------------------------------------------------------------------
# GD04 — child runs are deleted
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd04_delete_workspace_removes_runs(client, session):
    ws = await _make_workspace(session)
    run = await _make_run(session, ws.id)
    run_id = run.run_id
    await session.commit()

    r = await client.delete(f"/admin/workspaces/{ws.id}")
    assert r.status_code == 200
    assert r.json()["rows_deleted"]["runs"] == 1

    # Verify the run is gone
    remaining = (await session.exec(select(Run).where(Run.run_id == run_id))).first()
    assert remaining is None


# ---------------------------------------------------------------------------
# GD05 — api_keys for workspace are deleted
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd05_delete_workspace_removes_api_keys(client, session):
    ws = await _make_workspace(session)
    await _make_api_key(session, ws.id)
    await session.commit()
    ws_id = ws.id  # capture before expire invalidates the proxy

    r = await client.delete(f"/admin/workspaces/{ws_id}")
    assert r.status_code == 200
    assert r.json()["rows_deleted"]["api_keys"] == 1

    session.expire_all()
    remaining_key = (await session.exec(
        select(ApiKey).where(ApiKey.workspace_id == ws_id)
    )).first()
    assert remaining_key is None


# ---------------------------------------------------------------------------
# GD06 — user not found
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd06_erase_user_not_found(client):
    r = await client.post("/admin/users/999999/erase")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GD07 — already erased user returns 409
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd07_erase_already_erased_returns_409(client, session):
    user = await _make_user(session, email="erased_42@erased.antcrew")
    await session.commit()

    r = await client.post(f"/admin/users/{user.id}/erase")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GD08 — email and display_name are anonymised
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gd08_erase_user_anonymises_pii(client, session):
    user = await _make_user(session)
    user_id = user.id
    await session.commit()

    r = await client.post(f"/admin/users/{user_id}/erase")
    assert r.status_code == 200
    data = r.json()

    assert data["user_id"] == user_id
    assert data["email_anonymised"] == f"erased_{user_id}@erased.antcrew"

    await session.refresh(user)
    assert user.email.startswith("erased_")
    assert user.display_name == "[erased]"
