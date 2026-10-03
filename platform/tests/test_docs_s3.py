"""Tests for the Docs S3 API — /workspaces/{id}/docs/*.

Covers:
  DS01 — GET /docs/config: empty by default, 404 on unknown workspace
  DS02 — PUT /docs/config: stores bucket/prefix/region; credentials masked on read
  DS03 — DELETE /docs/config: clears all fields
  DS04 — GET /docs/schema: empty by default
  DS05 — PUT /docs/schema: valid YAML accepted; invalid YAML → 400
  DS06 — POST /docs/upload: 422 when S3 not configured
  DS07 — GET /docs: returns [] when S3 not configured
  DS08 — Role enforcement: read role can GET config, cannot PUT/DELETE
  DS09 — Cross-workspace: key from workspace B cannot access workspace A docs config
"""
from __future__ import annotations

import hashlib
import io
import secrets
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Workspace
from app.models.run import ApiKey


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _prefix(s: str) -> str:
    return _sha256(s)[:16]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _make_workspace_key(session: AsyncSession, slug: str, raw_key: str, role: str = "admin") -> tuple:
    ws = Workspace(name=slug, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    key = ApiKey(
        label=f"key-{slug}",
        key_hash=_sha256(raw_key),
        key_prefix=_prefix(raw_key),
        workspace_id=ws.id,
        role=role,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ws, key


@pytest.fixture
async def docs_ws(session):
    raw_key = "docs-admin-" + secrets.token_hex(8)
    ws, key = await _make_workspace_key(session, "docs-ws", raw_key)
    return ws, raw_key


@pytest.fixture
async def docs_ws_read_key(session, docs_ws):
    ws, _ = docs_ws
    raw_key = "docs-read-" + secrets.token_hex(8)
    key = ApiKey(
        label="key-docs-read",
        key_hash=_sha256(raw_key),
        key_prefix=_prefix(raw_key),
        workspace_id=ws.id,
        role="read",
    )
    session.add(key)
    await session.commit()
    return ws, raw_key


# ---------------------------------------------------------------------------
# DS01 — GET /docs/config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds01_config_empty_by_default(client, docs_ws):
    ws, raw_key = docs_ws
    r = await client.get(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] is None
    assert body["access_key_configured"] is False
    assert body["secret_key_configured"] is False


@pytest.mark.asyncio
async def test_ds01_config_unknown_workspace_404(client, docs_ws):
    _, raw_key = docs_ws
    r = await client.get("/workspaces/999999/docs/config", headers={"X-Api-Key": raw_key})
    assert r.status_code in (403, 404)


# ---------------------------------------------------------------------------
# DS02 — PUT /docs/config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds02_set_config_stores_fields(client, docs_ws):
    ws, raw_key = docs_ws
    r = await client.put(
        f"/workspaces/{ws.id}/docs/config",
        json={"bucket": "my-docs", "prefix": "proj/", "region": "eu-west-1"},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "my-docs"
    assert body["prefix"] == "proj/"
    assert body["region"] == "eu-west-1"


@pytest.mark.asyncio
async def test_ds02_credentials_masked_on_read(client, docs_ws):
    """Access key and secret key must NEVER be returned in plaintext."""
    ws, raw_key = docs_ws
    await client.put(
        f"/workspaces/{ws.id}/docs/config",
        json={"bucket": "masked-bucket", "access_key": "AKIAIOSFODNN7EXAMPLE", "secret_key": "super-secret"},
        headers={"X-Api-Key": raw_key},
    )
    r = await client.get(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    body = r.json()
    assert "AKIA" not in str(body)
    assert "super-secret" not in str(body)
    assert body["access_key_configured"] is True
    assert body["secret_key_configured"] is True


@pytest.mark.asyncio
async def test_ds02_partial_update_preserves_other_fields(client, docs_ws):
    """PUT with only bucket set should not wipe prefix if prefix is not in payload."""
    ws, raw_key = docs_ws
    await client.put(
        f"/workspaces/{ws.id}/docs/config",
        json={"bucket": "b1", "prefix": "keep-me/"},
        headers={"X-Api-Key": raw_key},
    )
    # Now update only the region
    await client.put(
        f"/workspaces/{ws.id}/docs/config",
        json={"region": "ap-northeast-1"},
        headers={"X-Api-Key": raw_key},
    )
    r = await client.get(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    body = r.json()
    assert body["prefix"] == "keep-me/"
    assert body["region"] == "ap-northeast-1"


# ---------------------------------------------------------------------------
# DS03 — DELETE /docs/config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds03_delete_clears_config(client, docs_ws):
    ws, raw_key = docs_ws
    await client.put(
        f"/workspaces/{ws.id}/docs/config",
        json={"bucket": "to-clear"},
        headers={"X-Api-Key": raw_key},
    )
    r_del = await client.delete(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    assert r_del.status_code == 204

    r = await client.get(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    assert r.json()["bucket"] is None


# ---------------------------------------------------------------------------
# DS04 & DS05 — Schema YAML
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds04_schema_empty_by_default(client, docs_ws):
    ws, raw_key = docs_ws
    r = await client.get(f"/workspaces/{ws.id}/docs/schema", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    assert r.json()["schema_yaml"] == ""


@pytest.mark.asyncio
async def test_ds05_valid_schema_accepted(client, docs_ws):
    ws, raw_key = docs_ws
    yaml_body = "documentation_schema:\n  org_name: TestOrg\n  document_types: []\n"
    r = await client.put(
        f"/workspaces/{ws.id}/docs/schema",
        json={"schema_yaml": yaml_body},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 200
    assert r.json()["schema_yaml"] == yaml_body


@pytest.mark.asyncio
async def test_ds05_invalid_yaml_rejected(client, docs_ws):
    ws, raw_key = docs_ws
    r = await client.put(
        f"/workspaces/{ws.id}/docs/schema",
        json={"schema_yaml": "key: [unclosed"},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ds05_schema_persisted_across_requests(client, docs_ws):
    ws, raw_key = docs_ws
    yaml_body = "documentation_schema:\n  org_name: Persist\n"
    await client.put(
        f"/workspaces/{ws.id}/docs/schema",
        json={"schema_yaml": yaml_body},
        headers={"X-Api-Key": raw_key},
    )
    r = await client.get(f"/workspaces/{ws.id}/docs/schema", headers={"X-Api-Key": raw_key})
    assert r.json()["schema_yaml"] == yaml_body


# ---------------------------------------------------------------------------
# DS06 — POST /docs/upload (no S3 configured)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds06_upload_requires_s3_config(client, docs_ws):
    """Upload must return 422 when S3 is not configured."""
    ws, raw_key = docs_ws
    r = await client.post(
        f"/workspaces/{ws.id}/docs/upload",
        params={"doc_type": "spec"},
        files={"file": ("test.md", io.BytesIO(b"# Hello"), "text/markdown")},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# DS07 — GET /docs (list)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds07_list_returns_empty_when_no_s3(client, docs_ws):
    ws, raw_key = docs_ws
    r = await client.get(f"/workspaces/{ws.id}/docs", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# DS08 — Role enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds08_read_role_can_get_config(client, docs_ws_read_key):
    ws, raw_key = docs_ws_read_key
    r = await client.get(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ds08_read_role_cannot_put_config(client, docs_ws_read_key):
    ws, raw_key = docs_ws_read_key
    r = await client.put(
        f"/workspaces/{ws.id}/docs/config",
        json={"bucket": "hack"},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ds08_read_role_cannot_delete_config(client, docs_ws_read_key):
    ws, raw_key = docs_ws_read_key
    r = await client.delete(f"/workspaces/{ws.id}/docs/config", headers={"X-Api-Key": raw_key})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ds08_read_role_cannot_put_schema(client, docs_ws_read_key):
    ws, raw_key = docs_ws_read_key
    r = await client.put(
        f"/workspaces/{ws.id}/docs/schema",
        json={"schema_yaml": "key: value"},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DS09 — Cross-workspace isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds09_workspace_b_cannot_read_workspace_a_docs_config(client, session):
    raw_a = "docs-ws-a-" + secrets.token_hex(8)
    raw_b = "docs-ws-b-" + secrets.token_hex(8)
    ws_a, _ = await _make_workspace_key(session, "docs-a", raw_a)
    ws_b, _ = await _make_workspace_key(session, "docs-b", raw_b)

    r = await client.get(f"/workspaces/{ws_a.id}/docs/config", headers={"X-Api-Key": raw_b})
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_ds09_workspace_b_cannot_write_workspace_a_docs_config(client, session):
    raw_a = "docs-iso-a-" + secrets.token_hex(8)
    raw_b = "docs-iso-b-" + secrets.token_hex(8)
    ws_a, _ = await _make_workspace_key(session, "docs-iso-a", raw_a)
    ws_b, _ = await _make_workspace_key(session, "docs-iso-b", raw_b)

    r = await client.put(
        f"/workspaces/{ws_a.id}/docs/config",
        json={"bucket": "stolen"},
        headers={"X-Api-Key": raw_b},
    )
    assert r.status_code in (403, 404)
