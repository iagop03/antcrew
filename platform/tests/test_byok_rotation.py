"""BYOK key rotation and provider management tests.

Covers gaps in test_byok_audit.py:
  BK01 — Store key, overwrite without confirm_overwrite=true → 409
  BK02 — Overwrite with confirm_overwrite=true succeeds (rotation)
  BK03 — Delete key, then re-store succeeds without confirm_overwrite
  BK04 — Switch to BYOK mode without keys → 422
  BK05 — Switch to BYOK mode after storing a key → 200
  BK06 — Switch back to managed after BYOK → 200
  BK07 — Stored key is never returned in plaintext (GET /llm-keys)
  BK08 — Unknown provider rejected by validator (422)
  BK09 — Keyless provider (ollama) accepted without api_key field
  BK10 — Cross-workspace: key B cannot list workspace A's LLM keys
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, LLMProviderKey, Workspace


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


@pytest.fixture
async def byok_ws(session):
    raw = "byok-rot-" + secrets.token_hex(8)
    ws, key = await _make_ws_key(session, "byok-rot-ws", raw)
    return ws, raw


def _h(raw_key: str) -> dict:
    return {"X-Api-Key": raw_key}


# ---------------------------------------------------------------------------
# BK01 — Overwrite requires confirm_overwrite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk01_overwrite_without_confirm_rejected(client, byok_ws):
    ws, raw = byok_ws
    await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-first-key"},
        headers=_h(raw),
    )
    r = await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-second-key"},
        headers=_h(raw),
    )
    assert r.status_code == 409, f"Expected 409 for overwrite without confirm, got {r.status_code}"


# ---------------------------------------------------------------------------
# BK02 — Rotation with confirm_overwrite=true
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk02_rotation_with_confirm(client, byok_ws):
    ws, raw = byok_ws
    r1 = await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "openai", "api_key": "sk-v1"},
        headers=_h(raw),
    )
    assert r1.status_code == 201

    r2 = await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "openai", "api_key": "sk-v2", "confirm_overwrite": True},
        headers=_h(raw),
    )
    assert r2.status_code in (200, 201), f"Rotation should succeed, got {r2.status_code}: {r2.text}"


# ---------------------------------------------------------------------------
# BK03 — Delete then re-store without confirm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk03_delete_then_restore(client, byok_ws):
    ws, raw = byok_ws
    await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "groq", "api_key": "gsk-v1"},
        headers=_h(raw),
    )

    r_del = await client.delete(
        f"/workspaces/{ws.id}/llm-keys/groq",
        headers=_h(raw),
    )
    assert r_del.status_code in (200, 204), f"Delete should succeed, got {r_del.status_code}"

    # Re-store without confirm_overwrite — should work since key was deleted
    r2 = await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "groq", "api_key": "gsk-v2"},
        headers=_h(raw),
    )
    assert r2.status_code == 201, f"Re-store after delete should be 201, got {r2.status_code}: {r2.text}"


# ---------------------------------------------------------------------------
# BK04 — Switch to BYOK without keys → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk04_byok_mode_without_keys_fails(client, byok_ws):
    ws, raw = byok_ws
    r = await client.patch(
        f"/workspaces/{ws.id}/llm-mode",
        json={"mode": "byok"},
        headers=_h(raw),
    )
    assert r.status_code == 422, f"Should fail with 422 (no keys), got {r.status_code}"


# ---------------------------------------------------------------------------
# BK05 — Switch to BYOK after storing a key → 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk05_byok_mode_with_key_succeeds(client, byok_ws):
    ws, raw = byok_ws
    await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-byok-test"},
        headers=_h(raw),
    )
    r = await client.patch(
        f"/workspaces/{ws.id}/llm-mode",
        json={"mode": "byok"},
        headers=_h(raw),
    )
    assert r.status_code == 200, f"Expected 200 for BYOK switch, got {r.status_code}: {r.text}"
    assert r.json()["llm_key_mode"] == "byok"


# ---------------------------------------------------------------------------
# BK06 — Switch back to managed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk06_switch_back_to_managed(client, byok_ws):
    ws, raw = byok_ws
    await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-byok-test2"},
        headers=_h(raw),
    )
    await client.patch(f"/workspaces/{ws.id}/llm-mode", json={"mode": "byok"}, headers=_h(raw))

    r = await client.patch(
        f"/workspaces/{ws.id}/llm-mode",
        json={"mode": "managed"},
        headers=_h(raw),
    )
    assert r.status_code == 200
    assert r.json()["llm_key_mode"] == "managed"


# ---------------------------------------------------------------------------
# BK07 — API key value never returned in plaintext
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk07_key_never_returned_in_plaintext(client, byok_ws):
    ws, raw = byok_ws
    plaintext = "sk-plaintext-test-value-" + secrets.token_hex(8)
    await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "anthropic", "api_key": plaintext},
        headers=_h(raw),
    )
    r = await client.get(f"/workspaces/{ws.id}/llm-keys", headers=_h(raw))
    assert r.status_code == 200
    assert plaintext not in str(r.json()), "API key value must never appear in GET response"


# ---------------------------------------------------------------------------
# BK08 — Unknown provider rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk08_unknown_provider_rejected(client, byok_ws):
    ws, raw = byok_ws
    r = await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": "unknown-provider", "api_key": "sk-x"},
        headers=_h(raw),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# BK09 — Keyless providers (ollama, lmstudio, vllm)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "lmstudio", "vllm"])
async def test_bk09_keyless_provider_accepted_without_api_key(client, byok_ws, provider):
    ws, raw = byok_ws
    r = await client.post(
        f"/workspaces/{ws.id}/llm-keys",
        json={"provider": provider, "base_url": "http://localhost:11434"},
        headers=_h(raw),
    )
    assert r.status_code == 201, f"Keyless {provider} should be accepted, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# BK10 — Cross-workspace isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bk10_key_b_cannot_list_workspace_a_llm_keys(client, session):
    raw_a = "byok-iso-a-" + secrets.token_hex(8)
    raw_b = "byok-iso-b-" + secrets.token_hex(8)
    ws_a, _ = await _make_ws_key(session, "byok-iso-a", raw_a)
    ws_b, _ = await _make_ws_key(session, "byok-iso-b", raw_b)

    await client.post(
        f"/workspaces/{ws_a.id}/llm-keys",
        json={"provider": "anthropic", "api_key": "sk-a"},
        headers=_h(raw_a),
    )

    r = await client.get(f"/workspaces/{ws_a.id}/llm-keys", headers=_h(raw_b))
    assert r.status_code in (403, 404), (
        f"Key B must not list workspace A LLM keys, got {r.status_code}"
    )


@pytest.mark.asyncio
async def test_bk10_key_b_cannot_store_key_in_workspace_a(client, session):
    raw_a = "byok-iso-c-" + secrets.token_hex(8)
    raw_b = "byok-iso-d-" + secrets.token_hex(8)
    ws_a, _ = await _make_ws_key(session, "byok-iso-c", raw_a)
    ws_b, _ = await _make_ws_key(session, "byok-iso-d", raw_b)

    r = await client.post(
        f"/workspaces/{ws_a.id}/llm-keys",
        json={"provider": "openai", "api_key": "sk-injected"},
        headers=_h(raw_b),
    )
    assert r.status_code in (403, 404), (
        f"Key B must not store LLM key in workspace A, got {r.status_code}"
    )
