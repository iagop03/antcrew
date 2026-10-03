"""Round 24 — Attestation endpoint: integrity + HMAC signing.

ATT1  GET /runs/{run_id}/attestation → 200, valid JSON with required fields
ATT2  document_hash is verifiable (SHA-256 integrity check passes)
ATT3  Without ATTESTATION_HMAC_SECRET, hmac_sha256 is absent from the document
ATT4  With ATTESTATION_HMAC_SECRET, hmac_sha256 field is included
ATT5  hmac_sha256 value is verifiable (HMAC-SHA256 integrity + authenticity check)
ATT6  attestation for run in another workspace → 403
ATT7  attestation for unknown run → 404

WAITLIST:
WL1   POST /api/waitlist → 200 {"ok": true} for valid email
WL2   POST /api/waitlist → 422 for invalid email
WL3   POST /api/waitlist → 422 for empty email
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.run import AgentEvent, Event, Run


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ws_ctx(workspace_id: int = 1) -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test", role="admin")


@pytest.fixture(autouse=True)
def override_ws_context():
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)
    yield
    app.dependency_overrides.pop(get_workspace_context, None)


async def _make_run(
    session: AsyncSession,
    *,
    workspace_id: int = 1,
    status: str = "success",
) -> Run:
    run = Run(
        run_id=uuid.uuid4().hex,
        thread_id="t1",
        team="DevTeam",
        request="Build JWT auth",
        status=status,
        cost_usd=0.004,
        workspace_id=workspace_id,
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    session.add(run)
    await session.flush()
    return run


async def _add_agent_events(session: AsyncSession, run: Run) -> None:
    ae = AgentEvent(
        run_id=run.run_id,
        agent_name="BackendDevAgent",
        duration_s=6.84,
        tokens_in=5100,
        tokens_out=2300,
        cost_usd=0.0032,
        produced_keys='["code"]',  # column is str (JSON-encoded list)
        recorded_at=_utcnow(),
    )
    session.add(ae)

    ev = Event(
        run_id=run.run_id,
        event_type="agent.end",
        payload={
            "agent_name": "BackendDevAgent",
            "governance_hash": "9a1c3f8b2e4d6c7a",
            "stage": "implementation",
        },
        timestamp=time.time(),  # float unix time
    )
    session.add(ev)
    await session.flush()


# ── ATT1: basic structure ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_att1_attestation_returns_valid_json(client: AsyncClient, session: AsyncSession):
    run = await _make_run(session)
    resp = await client.get(f"/runs/{run.run_id}/attestation")
    assert resp.status_code == 200
    doc = json.loads(resp.content)
    assert doc["schema_version"] in ("1.0", "1.1")
    assert doc["run_id"] == run.run_id
    assert doc["team"] == "DevTeam"
    assert doc["status"] == "success"
    assert "document_hash" in doc
    assert "agents" in doc
    assert "attestation_generated_at" in doc


# ── ATT2: document_hash integrity ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_att2_document_hash_is_verifiable(client: AsyncClient, session: AsyncSession):
    run = await _make_run(session)
    await _add_agent_events(session, run)
    resp = await client.get(f"/runs/{run.run_id}/attestation")
    assert resp.status_code == 200
    doc = json.loads(resp.content)

    body = {k: v for k, v in doc.items() if k not in ("document_hash", "hmac_sha256")}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()
    assert doc["document_hash"] == expected, "document_hash integrity check failed"


# ── ATT3: no HMAC when secret not set ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_att3_hmac_absent_without_secret(client: AsyncClient, session: AsyncSession, monkeypatch):
    monkeypatch.delenv("ATTESTATION_HMAC_SECRET", raising=False)
    run = await _make_run(session)
    resp = await client.get(f"/runs/{run.run_id}/attestation")
    assert resp.status_code == 200
    doc = json.loads(resp.content)
    assert "hmac_sha256" not in doc


# ── ATT4: HMAC field present when secret is set ────────────────────────────────

@pytest.mark.asyncio
async def test_att4_hmac_present_with_secret(client: AsyncClient, session: AsyncSession, monkeypatch):
    monkeypatch.setenv("ATTESTATION_HMAC_SECRET", "test-secret-32-bytes-minimum-len")
    run = await _make_run(session)
    resp = await client.get(f"/runs/{run.run_id}/attestation")
    assert resp.status_code == 200
    doc = json.loads(resp.content)
    assert "hmac_sha256" in doc
    assert doc["hmac_sha256"].startswith("hmac-sha256:")


# ── ATT5: HMAC value is verifiable ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_att5_hmac_value_verifiable(client: AsyncClient, session: AsyncSession, monkeypatch):
    secret = "test-secret-32-bytes-minimum-len"
    monkeypatch.setenv("ATTESTATION_HMAC_SECRET", secret)
    run = await _make_run(session)
    await _add_agent_events(session, run)
    resp = await client.get(f"/runs/{run.run_id}/attestation")
    assert resp.status_code == 200
    doc = json.loads(resp.content)

    # document_hash integrity
    body_for_hash = {k: v for k, v in doc.items() if k not in ("document_hash", "hmac_sha256")}
    expected_hash = "sha256:" + hashlib.sha256(
        json.dumps(body_for_hash, sort_keys=True).encode()
    ).hexdigest()
    assert doc["document_hash"] == expected_hash

    # HMAC authenticity — covers body including document_hash
    body_for_hmac = {k: v for k, v in doc.items() if k != "hmac_sha256"}
    expected_hmac = "hmac-sha256:" + hmac.new(
        secret.encode("utf-8"),
        json.dumps(body_for_hmac, sort_keys=True).encode("utf-8"),
        "sha256",
    ).hexdigest()
    assert doc["hmac_sha256"] == expected_hmac


# ── ATT6: cross-workspace access denied ───────────────────────────────────────

@pytest.mark.asyncio
async def test_att6_attestation_other_workspace_403(client: AsyncClient, session: AsyncSession):
    run = await _make_run(session, workspace_id=99)
    resp = await client.get(f"/runs/{run.run_id}/attestation")
    assert resp.status_code == 403


# ── ATT7: unknown run → 404 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_att7_attestation_unknown_run_404(client: AsyncClient):
    resp = await client.get("/runs/nonexistentrunid123/attestation")
    assert resp.status_code == 404


# ── Waitlist ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wl1_valid_email_accepted(client: AsyncClient, tmp_path):
    with patch("app.api.waitlist._WAITLIST_PATH", tmp_path / "waitlist.jsonl"):
        resp = await client.post(
            "/api/waitlist",
            json={"email": "test@example.com"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_wl2_invalid_email_422(client: AsyncClient):
    resp = await client.post("/api/waitlist", json={"email": "notanemail"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_wl3_empty_email_422(client: AsyncClient):
    resp = await client.post("/api/waitlist", json={"email": ""})
    assert resp.status_code == 422
