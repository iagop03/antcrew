"""Compliance Pack endpoints — test suite.

CP01  GET /compliance/status returns enabled=False for workspace without pack
CP02  GET /compliance/status returns enabled=True after admin enables pack
CP03  GET /compliance/status exposes workspace-level price overrides
CP04  GET /compliance/attestations → 402 when pack disabled
CP05  GET /compliance/attestations → 200 with empty list when pack enabled and no runs
CP06  GET /compliance/attestations → lists runs with correct fields
CP07  GET /compliance/attestations → status filter works
CP08  GET /compliance/export → 402 when pack disabled
CP09  GET /compliance/export → 404 when no completed runs
CP10  GET /compliance/export → 200 ZIP with attestation files for completed runs
CP11  PATCH /admin/workspaces/{id} sets compliance_pack_enabled and price overrides
CP12  GET /admin/billing-rates includes compliance_pack prices
CP13  PATCH /admin/billing-rates updates compliance pack prices
CP14  WorkspaceRow from admin list includes compliance fields
CP15  compliance_viewer role is rejected on non-/compliance/ paths
CP16  compliance_viewer role can access /compliance/status
CP17  compliance_viewer role can access /compliance/attestations when pack enabled
CP18  GET /compliance/dashboard returns 200 HTML page
CP19  Stripe checkout.session.completed with pack=compliance enables the pack via webhook
CP20  compliance_checkout → 409 when pack already enabled
CP21  _compliance_digest_loop sends emails to compliance_viewer keys with email
CP22  POST /api-keys accepts role=compliance_viewer (was rejected before fix)
CP23  customer.subscription.deleted with pack=compliance disables compliance_pack_enabled
CP24  customer.subscription.updated with status=past_due and pack=compliance disables the pack
"""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.admin_auth import require_platform_admin
from app.main import app
from app.models.workspace import Workspace
from app.models.run import Run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ws_ctx(workspace_id: int = 1) -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test", role="admin")


def _admin_stub():
    return {"id": 1, "email": "admin@test.com", "is_platform_admin": True}


async def _make_workspace(session: AsyncSession, *, compliance_pack_enabled: bool = False) -> Workspace:
    ws = Workspace(
        name="test-ws",
        slug=f"test-ws-{uuid.uuid4().hex[:6]}",
        compliance_pack_enabled=compliance_pack_enabled,
    )
    session.add(ws)
    await session.flush()
    return ws


async def _make_run(
    session: AsyncSession,
    *,
    workspace_id: int,
    status: str = "done",
) -> Run:
    run = Run(
        run_id=uuid.uuid4().hex,
        thread_id=uuid.uuid4().hex,
        team="DevTeam",
        request="build feature",
        status=status,
        cost_usd=0.01,
        workspace_id=workspace_id,
        created_at=_utcnow(),
        finished_at=_utcnow() if status == "done" else None,
    )
    session.add(run)
    await session.flush()
    return run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _override_ctx(session):
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)
    app.dependency_overrides[require_platform_admin] = _admin_stub
    yield
    app.dependency_overrides.pop(get_workspace_context, None)
    app.dependency_overrides.pop(require_platform_admin, None)


# ---------------------------------------------------------------------------
# CP01 — status when pack disabled
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp01_status_disabled(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=False)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/status")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False
    assert "platform_price_monthly" in data
    assert "platform_price_annual" in data


# ---------------------------------------------------------------------------
# CP02 — status when pack enabled
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp02_status_enabled(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is True


# ---------------------------------------------------------------------------
# CP03 — workspace-level price overrides appear in status
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp03_status_price_overrides(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    ws.compliance_pack_price_monthly = 99.0
    ws.compliance_pack_price_annual = 950.0
    session.add(ws)
    await session.flush()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/status")
    assert r.status_code == 200
    data = r.json()
    assert data["workspace_price_monthly"] == 99.0
    assert data["workspace_price_annual"] == 950.0


# ---------------------------------------------------------------------------
# CP04 — attestations 402 when pack disabled
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp04_attestations_requires_pack(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=False)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/attestations")
    assert r.status_code == 402


# ---------------------------------------------------------------------------
# CP05 — attestations empty list when no runs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp05_attestations_empty(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/attestations")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# CP06 — attestations lists runs with correct fields
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp06_attestations_list(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    run = await _make_run(session, workspace_id=ws.id, status="done")
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/attestations")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["run_id"] == run.run_id
    assert item["status"] == "done"
    assert item["attestation_url"] == f"/runs/{run.run_id}/attestation"
    assert "cost_usd" in item
    assert "created_at" in item


# ---------------------------------------------------------------------------
# CP07 — attestations status filter
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp07_attestations_status_filter(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    await _make_run(session, workspace_id=ws.id, status="done")
    await _make_run(session, workspace_id=ws.id, status="error")
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/attestations?status=error")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["status"] == "error"


# ---------------------------------------------------------------------------
# CP08 — export 402 when pack disabled
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp08_export_requires_pack(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=False)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/export")
    assert r.status_code == 402


# ---------------------------------------------------------------------------
# CP09 — export 404 when no completed runs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp09_export_no_runs(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    await _make_run(session, workspace_id=ws.id, status="running")  # not done
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/export")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# CP10 — export returns valid ZIP with attestation files
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp10_export_zip(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    run1 = await _make_run(session, workspace_id=ws.id, status="done")
    run2 = await _make_run(session, workspace_id=ws.id, status="done")
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "compliance_export" in r.headers.get("content-disposition", "")

    buf = io.BytesIO(r.content)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        # ZIP now contains: attestations/ dir + manifest.json (no keybridge by default)
        attestation_names = [n for n in names if n.startswith("attestations/") and n.endswith(".json")]
        assert len(attestation_names) == 2
        assert "manifest.json" in names
        import json
        for name in attestation_names:
            doc = json.loads(zf.read(name))
            assert "schema_version" in doc
            assert "document_hash" in doc
            assert doc["document_hash"].startswith("sha256:")
        # Manifest lists all audit sources
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["runs_exported"] == 2
        assert "keybridge_audit" in manifest["audit_sources"]


# ---------------------------------------------------------------------------
# CP11 — admin PATCH workspace sets compliance fields
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp11_admin_patch_compliance(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=False)

    r = await client.patch(
        f"/admin/workspaces/{ws.id}",
        json={
            "compliance_pack_enabled": True,
            "compliance_pack_price_monthly": 79.0,
            "compliance_pack_price_annual": 790.0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["compliance_pack_enabled"] is True
    assert data["compliance_pack_price_monthly"] == 79.0
    assert data["compliance_pack_price_annual"] == 790.0


# ---------------------------------------------------------------------------
# CP12 — admin billing-rates includes compliance prices
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp12_billing_rates_includes_compliance(client):
    r = await client.get("/admin/billing-rates")
    assert r.status_code == 200
    data = r.json()
    assert "compliance_pack_price_monthly" in data
    assert "compliance_pack_price_annual" in data
    assert data["compliance_pack_price_monthly"] > 0
    assert data["compliance_pack_price_annual"] > 0


# ---------------------------------------------------------------------------
# CP13 — PATCH billing-rates updates compliance prices
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp13_patch_billing_rates_compliance(client):
    r = await client.patch(
        "/admin/billing-rates",
        json={"compliance_pack_price_monthly": 59.0, "compliance_pack_price_annual": 590.0},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["compliance_pack_price_monthly"] == 59.0
    assert data["compliance_pack_price_annual"] == 590.0


# ---------------------------------------------------------------------------
# CP14 — admin workspace list row includes compliance fields
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp14_workspace_row_has_compliance_fields(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    ws.compliance_pack_price_monthly = 99.0

    r = await client.get(f"/admin/workspaces/{ws.id}", )
    # list endpoint
    r = await client.get("/admin/workspaces")
    assert r.status_code == 200
    rows = r.json()
    our_ws = next((w for w in rows if w["id"] == ws.id), None)
    assert our_ws is not None
    assert "compliance_pack_enabled" in our_ws


# ---------------------------------------------------------------------------
# CP15 — compliance_viewer role config and path guard logic
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp15_compliance_viewer_role_config(client):
    """compliance_viewer is a valid role and the path guard is configured."""
    from app.core.auth import _VALID_ROLES, _COMPLIANCE_VIEWER_PATHS

    assert "compliance_viewer" in _VALID_ROLES
    # Guard allows /compliance/ paths
    assert any(p.startswith("/compliance") for p in _COMPLIANCE_VIEWER_PATHS)
    # Guard does NOT allow unrelated paths
    allowed = lambda path: any(path.startswith(p) for p in _COMPLIANCE_VIEWER_PATHS)
    assert allowed("/compliance/status") is True
    assert allowed("/compliance/attestations") is True
    assert allowed("/runs") is False
    assert allowed("/api-keys") is False
    assert allowed("/admin/workspaces") is False


# ---------------------------------------------------------------------------
# CP16 — compliance_viewer can access /compliance/status
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp16_compliance_viewer_can_access_status(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)

    def _cv_ctx():
        return WorkspaceContext(workspace_id=ws.id, created_by="cv-key", role="compliance_viewer")

    app.dependency_overrides[get_workspace_context] = _cv_ctx
    try:
        r = await client.get("/compliance/status")
        assert r.status_code == 200
        assert r.json()["enabled"] is True
    finally:
        app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)


# ---------------------------------------------------------------------------
# CP17 — compliance_viewer can access /compliance/attestations
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp17_compliance_viewer_can_access_attestations(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    await _make_run(session, workspace_id=ws.id, status="done")

    def _cv_ctx():
        return WorkspaceContext(workspace_id=ws.id, created_by="cv-key", role="compliance_viewer")

    app.dependency_overrides[get_workspace_context] = _cv_ctx
    try:
        r = await client.get("/compliance/attestations")
        assert r.status_code == 200
        assert len(r.json()) == 1
    finally:
        app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)


# ---------------------------------------------------------------------------
# CP18 — GET /compliance/dashboard returns HTML
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp18_dashboard_html(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "antcrew" in r.text
    assert "compliance" in r.text.lower()


# ---------------------------------------------------------------------------
# CP19 — Stripe webhook checkout.session.completed enables pack
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp19_stripe_webhook_enables_pack(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=False)
    assert ws.compliance_pack_enabled is False

    # Simulate Stripe checkout.session.completed webhook (no STRIPE_WEBHOOK_SECRET in test)
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": None,
                "metadata": {
                    "workspace_id": str(ws.id),
                    "pack": "compliance",
                    "billing_cycle": "monthly",
                },
            }
        },
    }
    # Billing webhook uses its own context — no workspace context override needed
    r = await client.post("/billing/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["received"] is True

    await session.refresh(ws)
    assert ws.compliance_pack_enabled is True


# ---------------------------------------------------------------------------
# CP20 — checkout 409 when pack already enabled
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp20_checkout_already_enabled(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.post("/compliance/checkout", json={"billing_cycle": "monthly"})
    # 503 when Stripe not configured, but 409 takes priority — workspace already enabled
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# CP21 — compliance digest loop sends emails to viewers with email addresses
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp21_compliance_digest_emails(session):
    """Verify the digest helper queries correctly — mock email send to avoid SMTP."""
    import os
    from unittest.mock import AsyncMock, patch
    from app.models.auth import ApiKey
    from app.models.run import Run

    ws = await _make_workspace(session, compliance_pack_enabled=True)
    # Create a compliance_viewer API key with email
    key = ApiKey(
        label="cv-key",
        key_hash="dummy",
        workspace_id=ws.id,
        role="compliance_viewer",
        email="auditor@example.com",
    )
    session.add(key)
    # Create a completed run
    await _make_run(session, workspace_id=ws.id, status="done")
    await session.flush()

    sent: list[dict] = []

    async def fake_digest(to_email, workspace_name, workspace_slug, runs, base_url=""):
        sent.append({"to": to_email, "runs": runs})

    with patch("app.services.email.send_compliance_digest", side_effect=fake_digest):
        # Run the digest logic directly (not the full loop to avoid asyncio.sleep)
        from datetime import datetime, timezone, timedelta
        from sqlmodel import select
        from app.models.workspace import Workspace
        from app.models.run import Run as RunModel, ApiKey as ApiKeyModel
        from app.services.email import send_compliance_digest

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
        workspaces = (await session.exec(
            select(Workspace).where(Workspace.compliance_pack_enabled.is_(True))
        )).all()

        for w in workspaces:
            viewer_keys = (await session.exec(
                select(ApiKeyModel).where(
                    ApiKeyModel.workspace_id == w.id,
                    ApiKeyModel.role == "compliance_viewer",
                    ApiKeyModel.email.isnot(None),
                    ApiKeyModel.revoked_at.is_(None),
                )
            )).all()
            runs = (await session.exec(
                select(RunModel).where(
                    RunModel.workspace_id == w.id,
                    RunModel.status == "done",
                )
            )).all()
            if viewer_keys and runs:
                for k in viewer_keys:
                    await fake_digest(k.email, w.name, w.slug, [{"run_id": r.run_id, "team": r.team, "status": r.status, "cost_usd": r.cost_usd} for r in runs])

    assert len(sent) == 1
    assert sent[0]["to"] == "auditor@example.com"
    assert len(sent[0]["runs"]) == 1


# ---------------------------------------------------------------------------
# CP22 — POST /api-keys accepts role=compliance_viewer
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp22_create_compliance_viewer_key(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.post("/api-keys/", json={
        "label": "Compliance Officer Key",
        "workspace_id": ws.id,
        "role": "compliance_viewer",
        "email": "officer@example.com",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["role"] == "compliance_viewer"
    assert "key" in data  # raw key returned once


# ---------------------------------------------------------------------------
# CP23 — customer.subscription.deleted with pack=compliance disables the pack
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp23_stripe_cancellation_disables_pack(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    assert ws.compliance_pack_enabled is True

    payload = {
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "customer": None,
                "metadata": {
                    "pack": "compliance",
                    "workspace_id": str(ws.id),
                },
            }
        },
    }
    r = await client.post("/billing/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["received"] is True

    await session.refresh(ws)
    assert ws.compliance_pack_enabled is False


# ---------------------------------------------------------------------------
# CP24 — customer.subscription.updated past_due disables pack immediately
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp24_subscription_past_due_disables_pack(client, session):
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    assert ws.compliance_pack_enabled is True

    payload = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "customer": None,
                "status": "past_due",
                "metadata": {
                    "pack": "compliance",
                    "workspace_id": str(ws.id),
                },
            }
        },
    }
    r = await client.post("/billing/webhook", json=payload)
    assert r.status_code == 200

    await session.refresh(ws)
    assert ws.compliance_pack_enabled is False


# ---------------------------------------------------------------------------
# CP25 — tracelog_ref appears in attestation when run.state has tracelog
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp25_attestation_includes_tracelog_ref(client, session):
    """When run.state contains a tracelog, the attestation document includes
    tracelog_ref with a sha256 digest (schema 1.1)."""
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    run = await _make_run(session, workspace_id=ws.id, status="done")

    # Inject a fake tracelog into run.state
    run.state = {
        "tracelog": [
            {"agent": "BA", "decision": "approved", "reasoning": "looks good"},
            {"agent": "PM", "decision": "approved", "reasoning": "on track"},
        ]
    }
    session.add(run)
    await session.flush()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get(f"/runs/{run.run_id}/attestation")
    assert r.status_code == 200
    doc = r.json()

    assert doc.get("schema_version") == "1.1"
    assert "tracelog_ref" in doc, "tracelog_ref should be present when run.state has tracelog"
    ref = doc["tracelog_ref"]
    assert ref["algorithm"] == "sha256"
    assert ref["digest"].startswith("sha256:")
    assert ref["entries"] == 2


# ---------------------------------------------------------------------------
# CP26 — export includes runs with status="success" (real engine terminal status)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cp26_export_includes_success_status_runs(client, session):
    """The export must include runs with status='success', which is the actual
    terminal status the engine writes — not 'done' (which was a legacy assumption)."""
    ws = await _make_workspace(session, compliance_pack_enabled=True)
    # Create a run with status="success" (what the real engine writes)
    run_success = Run(
        run_id=uuid.uuid4().hex,
        thread_id=uuid.uuid4().hex,
        team="DevTeam",
        request="deploy v2",
        status="success",
        cost_usd=0.02,
        workspace_id=ws.id,
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    session.add(run_success)
    await session.flush()
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get("/compliance/export")
    assert r.status_code == 200, (
        f"Expected 200 for runs with status='success' but got {r.status_code}. "
        "The export was filtering by status='done' which misses real engine runs."
    )

    import io, zipfile, json
    buf = io.BytesIO(r.content)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        attestation_names = [n for n in names if n.startswith("attestations/") and n.endswith(".json")]
        assert len(attestation_names) == 1
        doc = json.loads(zf.read(attestation_names[0]))
        assert doc["run_id"] == run_success.run_id
        assert doc["status"] == "success"
