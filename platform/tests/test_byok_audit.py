"""BYOK audit trail + anomaly detection tests.

AUD01  store_llm_key writes a byok_audit_event with event_type='store'
AUD02  delete_llm_key writes a byok_audit_event with event_type='delete'
AUD03  GET /byok-audit returns events newest-first, filterable by provider
AUD04  GET /byok-status shows use_count_24h=0 for a fresh key
AUD05  get_workspace_llm_key increments use_count_24h on successive calls
AUD06  get_workspace_llm_key resets counter when window expires (>24h)
AUD07  is_anomalous=true when use_count_24h >= anomaly_threshold
AUD08  GET /byok-audit returns 403 for wrong workspace
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import pytest

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.workspace import BYOKAuditEvent, LLMProviderKey


def _ws_ctx(workspace_id: int = 1, role: str = "admin") -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test-key", role=role)


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)
    yield
    app.dependency_overrides.pop(get_workspace_context, None)


async def _make_ws(session, *, name: Optional[str] = None):
    from app.models.workspace import Workspace
    ws = Workspace(
        name=name or f"WS-{uuid.uuid4().hex[:6]}",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
    )
    session.add(ws)
    await session.flush()
    return ws


# ---------------------------------------------------------------------------
# AUD01 — store writes audit event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud01_store_writes_audit_event(client, session):
    ws = await _make_ws(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    with patch("app.core.byok._encrypt", return_value="ENC_FAKE"):
        r = await client.post(
            f"/workspaces/{ws.id}/llm-keys",
            json={"provider": "openai", "api_key": "sk-test"},
        )
    assert r.status_code == 201

    from sqlmodel import select
    events = (await session.exec(
        select(BYOKAuditEvent).where(BYOKAuditEvent.workspace_id == ws.id)
    )).all()
    assert len(events) == 1
    assert events[0].event_type == "store"
    assert events[0].provider == "openai"
    assert events[0].note is not None and "actor=" in events[0].note


# ---------------------------------------------------------------------------
# AUD02 — delete writes audit event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud02_delete_writes_audit_event(client, session):
    ws = await _make_ws(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    session.add(LLMProviderKey(
        workspace_id=ws.id,
        provider="groq",
        key_enc="ENC_FAKE",
    ))
    await session.flush()

    r = await client.delete(f"/workspaces/{ws.id}/llm-keys/groq")
    assert r.status_code == 204

    from sqlmodel import select
    events = (await session.exec(
        select(BYOKAuditEvent).where(BYOKAuditEvent.workspace_id == ws.id)
    )).all()
    assert any(e.event_type == "delete" and e.provider == "groq" for e in events)


# ---------------------------------------------------------------------------
# AUD03 — GET /byok-audit returns events newest-first, filterable by provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud03_byok_audit_list(client, session):
    ws = await _make_ws(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(BYOKAuditEvent(workspace_id=ws.id, provider="anthropic", event_type="store",
                               created_at=now - timedelta(minutes=10)))
    session.add(BYOKAuditEvent(workspace_id=ws.id, provider="openai", event_type="delete",
                               created_at=now - timedelta(minutes=5)))
    session.add(BYOKAuditEvent(workspace_id=ws.id, provider="anthropic", event_type="rotate",
                               created_at=now))
    await session.flush()

    r = await client.get(f"/workspaces/{ws.id}/byok-audit")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 3
    # newest-first
    assert events[0]["event_type"] == "rotate"
    assert events[-1]["event_type"] == "store"

    # filter by provider
    r2 = await client.get(f"/workspaces/{ws.id}/byok-audit?provider=anthropic")
    assert r2.status_code == 200
    assert all(e["provider"] == "anthropic" for e in r2.json())
    assert len(r2.json()) == 2


# ---------------------------------------------------------------------------
# AUD04 — /byok-status shows use_count_24h=0 for fresh key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud04_status_fresh_key(client, session):
    ws = await _make_ws(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)
    session.add(LLMProviderKey(workspace_id=ws.id, provider="anthropic", key_enc="ENC"))
    await session.flush()

    r = await client.get(f"/workspaces/{ws.id}/byok-status")
    assert r.status_code == 200
    statuses = r.json()
    assert len(statuses) == 1
    s = statuses[0]
    assert s["provider"] == "anthropic"
    assert s["use_count_24h"] == 0
    assert s["is_anomalous"] is False


# ---------------------------------------------------------------------------
# AUD05 — get_workspace_llm_key increments use_count_24h
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud05_use_count_increments(session):
    from app.core.byok import get_workspace_llm_key

    ws_id = 9001
    session.add(LLMProviderKey(workspace_id=ws_id, provider="anthropic", key_enc="PLAIN"))
    await session.flush()

    with patch("app.core.byok._decrypt", return_value="sk-real"):
        await get_workspace_llm_key(session, ws_id, "anthropic")
        await get_workspace_llm_key(session, ws_id, "anthropic")
        await get_workspace_llm_key(session, ws_id, "anthropic")

    from sqlmodel import select
    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == ws_id)
        .where(LLMProviderKey.provider == "anthropic")
    )).first()
    assert row is not None
    assert row.use_count_24h == 3
    assert row.last_used_at is not None


# ---------------------------------------------------------------------------
# AUD06 — counter resets when window expires (>24h)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud06_counter_resets_after_24h(session):
    from app.core.byok import get_workspace_llm_key

    ws_id = 9002
    old_window = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    session.add(LLMProviderKey(
        workspace_id=ws_id,
        provider="openai",
        key_enc="PLAIN",
        use_count_24h=150,
        use_window_start=old_window,
    ))
    await session.flush()

    with patch("app.core.byok._decrypt", return_value="sk-reset"):
        await get_workspace_llm_key(session, ws_id, "openai")

    from sqlmodel import select
    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == ws_id)
        .where(LLMProviderKey.provider == "openai")
    )).first()
    assert row.use_count_24h == 1, "Counter must reset to 1 after window expires"


# ---------------------------------------------------------------------------
# AUD07 — is_anomalous=true when count >= threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud07_anomaly_flag(client, session):
    ws = await _make_ws(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    # Set count just at threshold, custom threshold=10
    session.add(LLMProviderKey(
        workspace_id=ws.id,
        provider="gemini",
        key_enc="ENC",
        use_count_24h=10,
        anomaly_threshold=10,
    ))
    await session.flush()

    r = await client.get(f"/workspaces/{ws.id}/byok-status")
    assert r.status_code == 200
    s = next(x for x in r.json() if x["provider"] == "gemini")
    assert s["is_anomalous"] is True
    assert s["use_count_24h"] == 10
    assert s["anomaly_threshold"] == 10


# ---------------------------------------------------------------------------
# AUD08 — GET /byok-audit returns 403 for wrong workspace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aud08_audit_wrong_workspace(client, session):
    ws = await _make_ws(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get(f"/workspaces/{ws.id + 999}/byok-audit")
    assert r.status_code in (403, 404)
