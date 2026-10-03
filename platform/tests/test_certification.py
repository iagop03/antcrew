"""UC3 Certified Agent flow — test suite.

Certificate endpoint (GET /runs/{run_id}/certificate):
  UC3-C01  No approved hashes → certification_status = unchecked
  UC3-C02  Matching approved hash → certified
  UC3-C03  Mismatched approved hash → drifted
  UC3-C04  One certified + one drifted agent → overall drifted
  UC3-C05  document_hash is present and correct
  UC3-C06  Run not found → 404

Approved-hash management (POST / GET / DELETE /compliance/approved-hashes):
  UC3-H01  POST requires compliance pack → 402 when disabled
  UC3-H02  POST registers hash and returns 201
  UC3-H03  POST duplicate (team+agent) → 409
  UC3-H04  GET lists active hashes
  UC3-H05  GET team filter works
  UC3-H06  DELETE soft-deletes hash → excluded from future certs
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.compliance import ApprovedAgentHash
from app.models.run import AgentEvent, Event as DBEvent, Run
from app.models.workspace import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ws_ctx(workspace_id: int) -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test", role="admin")


async def _make_workspace(session: AsyncSession, *, compliance_pack_enabled: bool = True) -> Workspace:
    ws = Workspace(
        name="cert-ws",
        slug=f"cert-ws-{uuid.uuid4().hex[:6]}",
        compliance_pack_enabled=compliance_pack_enabled,
    )
    session.add(ws)
    await session.flush()
    return ws


async def _make_run(session: AsyncSession, *, workspace_id: int, team: str = "DevTeam") -> Run:
    run = Run(
        run_id=uuid.uuid4().hex,
        thread_id=uuid.uuid4().hex,
        team=team,
        request="Build something",
        status="success",
        workspace_id=workspace_id,
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    session.add(run)
    await session.flush()
    return run


async def _add_agent_event(
    session: AsyncSession,
    run_id: str,
    agent_name: str,
    governance_hash: str,
) -> None:
    """Add both the AgentEvent row and the agent.end DBEvent for a run."""
    import time
    session.add(AgentEvent(
        run_id=run_id,
        agent_name=agent_name,
        duration_s=1.0,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        recorded_at=_utcnow(),
    ))
    session.add(DBEvent(
        run_id=run_id,
        event_type="agent.end",
        payload={
            "agent_name": agent_name,
            "governance_hash": governance_hash,
            "stage": "test",
        },
        timestamp=time.time(),
    ))


# ---------------------------------------------------------------------------
# UC3-C01 — no approved hashes → unchecked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_c01_no_hashes_unchecked(client, session: AsyncSession):
    ws = await _make_workspace(session)
    run = await _make_run(session, workspace_id=ws.id)
    await _add_agent_event(session, run.run_id, "BusinessAnalyst", "abc123")
    await session.commit()

    r = await client.get(f"/runs/{run.run_id}/certificate")
    assert r.status_code == 200
    data = json.loads(r.content)
    assert data["certification_status"] == "unchecked"
    assert data["agents"][0]["status"] == "unchecked"
    assert data["agents"][0]["approved_hash"] is None


# ---------------------------------------------------------------------------
# UC3-C02 — matching hash → certified
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_c02_matching_hash_certified(client, session: AsyncSession):
    ws = await _make_workspace(session)
    run = await _make_run(session, workspace_id=ws.id)
    await _add_agent_event(session, run.run_id, "BusinessAnalyst", "abc123def456")
    session.add(ApprovedAgentHash(
        workspace_id=ws.id,
        team="DevTeam",
        agent_name="BusinessAnalyst",
        governance_hash="abc123def456",
        registered_at=_utcnow(),
    ))
    await session.commit()

    r = await client.get(f"/runs/{run.run_id}/certificate")
    data = json.loads(r.content)
    assert data["certification_status"] == "certified"
    assert data["agents"][0]["status"] == "certified"


# ---------------------------------------------------------------------------
# UC3-C03 — mismatched hash → drifted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_c03_mismatched_hash_drifted(client, session: AsyncSession):
    ws = await _make_workspace(session)
    run = await _make_run(session, workspace_id=ws.id)
    await _add_agent_event(session, run.run_id, "BusinessAnalyst", "abc123new")
    session.add(ApprovedAgentHash(
        workspace_id=ws.id,
        team="DevTeam",
        agent_name="BusinessAnalyst",
        governance_hash="abc123old",
        registered_at=_utcnow(),
    ))
    await session.commit()

    r = await client.get(f"/runs/{run.run_id}/certificate")
    data = json.loads(r.content)
    assert data["certification_status"] == "drifted"
    agent = data["agents"][0]
    assert agent["status"] == "drifted"
    assert agent["governance_hash"] == "abc123new"
    assert agent["approved_hash"] == "abc123old"


# ---------------------------------------------------------------------------
# UC3-C04 — one certified + one drifted → overall drifted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_c04_partial_drift_is_drifted(client, session: AsyncSession):
    ws = await _make_workspace(session)
    run = await _make_run(session, workspace_id=ws.id)
    await _add_agent_event(session, run.run_id, "BusinessAnalyst", "hash-ba-correct")
    await _add_agent_event(session, run.run_id, "BackendDev", "hash-dev-wrong")
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam",
        agent_name="BusinessAnalyst", governance_hash="hash-ba-correct",
        registered_at=_utcnow(),
    ))
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam",
        agent_name="BackendDev", governance_hash="hash-dev-approved",
        registered_at=_utcnow(),
    ))
    await session.commit()

    r = await client.get(f"/runs/{run.run_id}/certificate")
    data = json.loads(r.content)
    assert data["certification_status"] == "drifted"
    statuses = {a["agent_name"]: a["status"] for a in data["agents"]}
    assert statuses["BusinessAnalyst"] == "certified"
    assert statuses["BackendDev"] == "drifted"


# ---------------------------------------------------------------------------
# UC3-C05 — document_hash is present and verifiable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_c05_document_hash_verifiable(client, session: AsyncSession):
    ws = await _make_workspace(session)
    run = await _make_run(session, workspace_id=ws.id)
    await session.commit()

    r = await client.get(f"/runs/{run.run_id}/certificate")
    data = json.loads(r.content)
    assert "document_hash" in data

    doc_hash = data.pop("document_hash")
    recomputed = "sha256:" + hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()
    assert doc_hash == recomputed


# ---------------------------------------------------------------------------
# UC3-C06 — run not found → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_c06_run_not_found(client):
    r = await client.get("/runs/no-such-run/certificate")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# UC3-H01 — POST approved-hashes requires compliance pack
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_h01_post_requires_compliance_pack(client, session: AsyncSession):
    ws = await _make_workspace(session, compliance_pack_enabled=False)
    await session.commit()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    try:
        r = await client.post("/compliance/approved-hashes", json={
            "team": "DevTeam", "agent_name": "BA", "governance_hash": "abc"
        })
        assert r.status_code == 402
    finally:
        app.dependency_overrides.pop(get_workspace_context, None)


# ---------------------------------------------------------------------------
# UC3-H02 — POST registers hash → 201
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_h02_post_registers_hash(client, session: AsyncSession):
    ws = await _make_workspace(session)
    await session.commit()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    try:
        r = await client.post("/compliance/approved-hashes", json={
            "team": "DevTeam",
            "agent_name": "BusinessAnalyst",
            "governance_hash": "abc123",
            "label": "v1.0 approved",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["team"] == "DevTeam"
        assert data["agent_name"] == "BusinessAnalyst"
        assert data["governance_hash"] == "abc123"
        assert data["label"] == "v1.0 approved"
        assert data["active"] is True
    finally:
        app.dependency_overrides.pop(get_workspace_context, None)


# ---------------------------------------------------------------------------
# UC3-H03 — duplicate POST → 409
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_h03_duplicate_hash_409(client, session: AsyncSession):
    ws = await _make_workspace(session)
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam",
        agent_name="BA", governance_hash="existing",
        registered_at=_utcnow(),
    ))
    await session.commit()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    try:
        r = await client.post("/compliance/approved-hashes", json={
            "team": "DevTeam", "agent_name": "BA", "governance_hash": "new"
        })
        assert r.status_code == 409
    finally:
        app.dependency_overrides.pop(get_workspace_context, None)


# ---------------------------------------------------------------------------
# UC3-H04 — GET lists active hashes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_h04_get_lists_active_hashes(client, session: AsyncSession):
    ws = await _make_workspace(session)
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam", agent_name="BA",
        governance_hash="h1", registered_at=_utcnow(),
    ))
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam", agent_name="Dev",
        governance_hash="h2", active=False, registered_at=_utcnow(),
    ))
    await session.commit()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    try:
        r = await client.get("/compliance/approved-hashes")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["agent_name"] == "BA"
    finally:
        app.dependency_overrides.pop(get_workspace_context, None)


# ---------------------------------------------------------------------------
# UC3-H05 — GET team filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_h05_get_team_filter(client, session: AsyncSession):
    ws = await _make_workspace(session)
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam", agent_name="BA",
        governance_hash="h1", registered_at=_utcnow(),
    ))
    session.add(ApprovedAgentHash(
        workspace_id=ws.id, team="FullStackTeam", agent_name="BA",
        governance_hash="h2", registered_at=_utcnow(),
    ))
    await session.commit()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    try:
        r = await client.get("/compliance/approved-hashes?team=DevTeam")
        data = r.json()
        assert all(h["team"] == "DevTeam" for h in data)
        assert len(data) == 1
    finally:
        app.dependency_overrides.pop(get_workspace_context, None)


# ---------------------------------------------------------------------------
# UC3-H06 — DELETE soft-deletes; deleted hash excluded from certificate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uc3_h06_delete_excluded_from_cert(client, session: AsyncSession):
    ws = await _make_workspace(session)
    run = await _make_run(session, workspace_id=ws.id)
    await _add_agent_event(session, run.run_id, "BA", "hash-ba")
    row = ApprovedAgentHash(
        workspace_id=ws.id, team="DevTeam", agent_name="BA",
        governance_hash="hash-ba", registered_at=_utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    row_id = row.id

    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    try:
        # Before delete: certified
        r = await client.get(f"/runs/{run.run_id}/certificate")
        assert json.loads(r.content)["certification_status"] == "certified"

        # Delete the hash
        r = await client.delete(f"/compliance/approved-hashes/{row_id}")
        assert r.status_code == 200
        assert r.json()["active"] is False

        # After delete: unchecked (no active approved hashes)
        r = await client.get(f"/runs/{run.run_id}/certificate")
        assert json.loads(r.content)["certification_status"] == "unchecked"
    finally:
        app.dependency_overrides.pop(get_workspace_context, None)
