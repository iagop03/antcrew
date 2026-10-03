"""Compliance Pack API — workspace-scoped dashboard and attestation export.

Requires workspace.compliance_pack_enabled = True for protected endpoints.
Provides:
  GET  /compliance/status           → pack status and effective pricing (always accessible)
  GET  /compliance/dashboard        → HTML dashboard for compliance officers
  GET  /compliance/attestations     → paginated list of runs with attestation metadata
  GET  /compliance/export           → ZIP of HMAC-signed attestation JSONs
  POST /compliance/checkout         → Stripe Checkout session for the pack add-on
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.database import get_session
from app.models.compliance import ApprovedAgentHash
from app.models.workspace import Workspace

log = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ---------------------------------------------------------------------------
# Guard dependency
# ---------------------------------------------------------------------------

async def _require_compliance_pack(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
) -> Workspace:
    ws: Optional[Workspace] = await session.get(Workspace, ctx.workspace_id)
    if ws is None or not ws.compliance_pack_enabled:
        raise HTTPException(402, "Compliance Pack not enabled for this workspace")
    return ws


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ComplianceStatus(BaseModel):
    enabled: bool
    workspace_price_monthly: Optional[float]   # workspace-level override, null = uses platform default
    workspace_price_annual: Optional[float]
    platform_price_monthly: float              # effective default from PlatformConfig
    platform_price_annual: float


class AttestationSummary(BaseModel):
    run_id: str
    team: Optional[str]
    status: str
    cost_usd: Optional[float]
    created_at: Optional[datetime]
    finished_at: Optional[datetime]
    attestation_url: str                       # relative path for individual download


# ---------------------------------------------------------------------------
# Internal: build one attestation document (shared with export)
# ---------------------------------------------------------------------------

async def _build_attestation(run: Any, session: Any) -> dict:
    """Generate a signed attestation document for a single run.

    Schema 1.1 adds tracelog_ref: a SHA-256 digest of the run's TraceLog
    (the encrypted per-agent decision log stored in run.state).  The digest
    lets auditors verify that the log has not been tampered with without
    exposing the log content itself.
    """
    from antcrew import __version__ as _engine_version

    from app.models.run import AgentEvent
    from app.models.run import Event as DBEvent

    agent_rows = (await session.exec(
        select(AgentEvent).where(AgentEvent.run_id == run.run_id).order_by(AgentEvent.id)
    )).all()

    end_events = (await session.exec(
        select(DBEvent)
        .where(DBEvent.run_id == run.run_id, DBEvent.event_type == "agent.end")
        .order_by(DBEvent.id)
    )).all()

    governance_by_agent: dict[str, dict] = {}
    for ev in end_events:
        p = ev.payload or {}
        name = p.get("agent_name", "")
        if name:
            governance_by_agent[name] = {
                "governance_hash": p.get("governance_hash", ""),
                "stage": p.get("stage", ""),
            }

    agents_info = [
        {
            "agent_name": row.agent_name,
            "governance_hash": governance_by_agent.get(row.agent_name, {}).get("governance_hash", ""),
            "stage": governance_by_agent.get(row.agent_name, {}).get("stage", ""),
            "duration_s": row.duration_s,
            "tokens_in": row.tokens_in,
            "tokens_out": row.tokens_out,
            "cost_usd": row.cost_usd,
        }
        for row in agent_rows
    ]

    body: dict[str, Any] = {
        "schema_version": "1.1",
        "run_id": run.run_id,
        "team": run.team,
        "request_preview": run.request[:200] if run.request else "",
        "status": run.status,
        "cost_usd": run.cost_usd,
        "workspace_id": run.workspace_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "agents": agents_info,
        "platform_version": "antcrew-platform",
        "engine_version": _engine_version,
        "attestation_generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # TraceLog reference — include a tamper-evident digest of the engine's
    # encrypted decision log so auditors can verify integrity without seeing content.
    _state: dict = run.state or {}
    _tracelog = (
        _state.get("tracelog")
        or _state.get("trace_log")
        or _state.get("audit_log")
    )
    if _tracelog:
        _tl_serial = json.dumps(_tracelog, sort_keys=True, default=str)
        body["tracelog_ref"] = {
            "algorithm": "sha256",
            "digest": "sha256:" + hashlib.sha256(_tl_serial.encode()).hexdigest(),
            "entries": len(_tracelog) if isinstance(_tracelog, list) else 1,
        }

    # Document→code traceability: list of docs indexed during this run with content hashes
    _doc_trace = _state.get("doc_traceability")
    if _doc_trace and isinstance(_doc_trace, dict):
        body["doc_traceability"] = {
            "indexed_at": _doc_trace.get("indexed_at"),
            "document_count": len(_doc_trace.get("documents", [])),
            "documents": [
                {
                    "doc_id": d.get("doc_id"),
                    "doc_type": d.get("doc_type"),
                    "source_file": d.get("source_file"),
                    "content_hash": d.get("content_hash"),
                }
                for d in _doc_trace.get("documents", [])
            ],
        }

    body["document_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()

    _secret = os.environ.get("ATTESTATION_HMAC_SECRET", "")
    if _secret:
        body["hmac_sha256"] = "hmac-sha256:" + hmac.new(
            _secret.encode("utf-8"),
            json.dumps(body, sort_keys=True).encode("utf-8"),
            "sha256",
        ).hexdigest()

    return body


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=ComplianceStatus)
async def compliance_status(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Return Compliance Pack status and effective pricing for this workspace.

    Always accessible (does not require the pack to be enabled) so the frontend
    can show an upgrade prompt when enabled=false.
    """
    from app.models.admin import PlatformConfig

    ws: Optional[Workspace] = await session.get(Workspace, ctx.workspace_id)
    cfg: Optional[PlatformConfig] = await session.get(PlatformConfig, 1)

    platform_monthly = (cfg.compliance_pack_price_monthly if cfg else None) or 49.0
    platform_annual = (cfg.compliance_pack_price_annual if cfg else None) or 490.0

    return ComplianceStatus(
        enabled=ws.compliance_pack_enabled if ws else False,
        workspace_price_monthly=ws.compliance_pack_price_monthly if ws else None,
        workspace_price_annual=ws.compliance_pack_price_annual if ws else None,
        platform_price_monthly=platform_monthly,
        platform_price_annual=platform_annual,
    )


@router.get("/attestations", response_model=list[AttestationSummary])
async def list_attestations(
    ws: Workspace = Depends(_require_compliance_pack),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by run status (done, error, running…)"),
):
    """List all runs with attestation metadata for this workspace.

    Returns lightweight summaries. Use GET /runs/{run_id}/attestation for the
    full signed document for a specific run.
    """
    from app.models.run import Run as RunModel

    q = (
        select(RunModel)
        .where(RunModel.workspace_id.in_(ctx.workspace_ids))
        .order_by(RunModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if status:
        q = q.where(RunModel.status == status)

    runs = (await session.exec(q)).all()

    return [
        AttestationSummary(
            run_id=r.run_id,
            team=r.team,
            status=r.status,
            cost_usd=r.cost_usd,
            created_at=r.created_at,
            finished_at=r.finished_at,
            attestation_url=f"/runs/{r.run_id}/attestation",
        )
        for r in runs
    ]


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def compliance_dashboard(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Serve the Compliance Pack HTML dashboard for non-developer compliance officers."""
    ws: Optional[Workspace] = await session.get(Workspace, ctx.workspace_id)
    ws_name = ws.name if ws else "Workspace"
    ws_enabled = ws.compliance_pack_enabled if ws else False

    return HTMLResponse(_DASHBOARD_HTML.replace("{{WS_NAME}}", ws_name).replace(
        "{{PACK_ENABLED}}", "true" if ws_enabled else "false"
    ))


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Compliance · antcrew</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#080F1C;color:#E8EDF5;font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:14px;min-height:100vh}
  h1,h2,h3{font-family:'Lora',Georgia,serif;font-weight:700}
  .header{background:#0F1929;border-bottom:1px solid #1E2D42;padding:20px 32px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
  .logo{font-size:13px;font-weight:700;letter-spacing:.15em;color:#2DD4BF;text-transform:uppercase;font-family:'IBM Plex Mono',monospace}
  .logo span{color:#4E6A85;font-weight:400;margin-left:8px;font-size:12px;letter-spacing:normal;text-transform:none}
  .chip{display:inline-block;padding:3px 10px;border-radius:2px;font-size:11px;font-weight:600;font-family:'IBM Plex Mono',monospace;letter-spacing:.06em}
  .chip-on{background:rgba(13,148,136,.12);color:#2DD4BF;border:1px solid rgba(13,148,136,.3)}
  .chip-off{background:rgba(248,113,113,.1);color:#f87171;border:1px solid rgba(248,113,113,.25)}
  .container{max-width:900px;margin:0 auto;padding:32px 24px}
  .card{background:#0F1929;border:1px solid #1E2D42;border-radius:2px;padding:24px;margin-bottom:20px;border-top:2px solid #1E4D6B}
  .card-title{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:#4E6A85;margin-bottom:16px}
  .stat-row{display:flex;gap:24px;flex-wrap:wrap}
  .stat{flex:1;min-width:120px}
  .stat-val{font-size:22px;font-weight:700;color:#E8EDF5;font-family:'IBM Plex Mono',monospace}
  .stat-label{font-size:11px;color:#4E6A85;margin-top:2px;text-transform:uppercase;letter-spacing:.07em}
  .btn{display:inline-flex;align-items:center;gap:6px;padding:9px 18px;border-radius:2px;font-size:13px;font-weight:600;cursor:pointer;border:none;text-decoration:none;transition:opacity .15s;font-family:'IBM Plex Sans',sans-serif}
  .btn:hover{opacity:.85}
  .btn-primary{background:#1E4D6B;color:#fff}
  .btn-outline{background:transparent;color:#2DD4BF;border:1px solid #0D9488}
  table{width:100%;border-collapse:collapse}
  th{padding:10px 14px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:#4E6A85;border-bottom:1px solid #1E2D42}
  td{padding:11px 14px;border-bottom:1px solid #162235;font-size:13px}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#0F1929}
  .run-id{font-family:'IBM Plex Mono',monospace;font-size:12px;color:#2DD4BF}
  .status-done{color:#34D399}
  .status-error{color:#f87171}
  .status-other{color:#7A9AB5}
  .empty{text-align:center;padding:48px;color:#354E65}
  .pagination{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
  .pg-btn{padding:6px 14px;border:1px solid #1E2D42;border-radius:2px;background:none;color:#E8EDF5;cursor:pointer;font-size:12px;font-family:'IBM Plex Sans',sans-serif}
  .pg-btn:hover{border-color:#0D9488;color:#2DD4BF}
  .pg-btn:disabled{opacity:.3;cursor:default}
  .upgrade-banner{background:rgba(13,148,136,.06);border:1px solid rgba(13,148,136,.2);border-radius:2px;padding:28px;text-align:center}
  .upgrade-banner h2{color:#E8EDF5;font-size:18px;margin-bottom:8px;font-family:'Lora',serif}
  .upgrade-banner p{color:#7A9AB5;margin-bottom:16px}
  .cycle-select{background:#0F1929;border:1px solid #253650;border-radius:2px;color:#E8EDF5;padding:8px 14px;font-size:13px;margin-bottom:16px;cursor:pointer;font-family:'IBM Plex Sans',sans-serif}
  .cycle-select:focus{outline:none;border-color:#0D9488}
  .upgrade-hint{font-size:11px;color:#354E65;margin-top:12px}
  input.api-key-input{background:#0F1929;border:1px solid #253650;border-radius:2px;color:#E8EDF5;padding:10px 14px;width:100%;font-family:'IBM Plex Mono',monospace;font-size:13px;margin-bottom:12px}
  input.api-key-input:focus{outline:none;border-color:#0D9488}
  .toast{position:fixed;bottom:24px;right:24px;background:#0F1929;border:1px solid #0D9488;border-radius:2px;padding:12px 18px;font-size:13px;color:#E8EDF5;display:none;z-index:999}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="logo">antcrew <span>compliance dashboard</span></div>
  </div>
  <div id="status-chip"></div>
</div>
<div class="container">
  <div id="auth-card" class="card" style="display:none">
    <div class="card-title">Autenticación</div>
    <input class="api-key-input" id="api-key-input" type="password" placeholder="API key (compliance_viewer o admin)">
    <button class="btn btn-primary" onclick="saveKey()">Conectar</button>
  </div>

  <div id="upgrade-banner" class="upgrade-banner" style="display:none">
    <h2>Compliance Pack no activado</h2>
    <p>Este workspace no tiene el Compliance Pack habilitado.<br>Actívalo ahora o contacta con un administrador.</p>
    <div>
      <select id="billing-cycle-sel" class="cycle-select">
        <option value="monthly">Mensual — $49/mes</option>
        <option value="annual">Anual — $490/año (~$41/mes)</option>
      </select>
    </div>
    <button class="btn btn-primary" id="checkout-btn" onclick="startCheckout()">Activar Compliance Pack</button>
    <div id="checkout-msg" style="display:none;color:#f87171;font-size:12px;margin-top:10px"></div>
    <p class="upgrade-hint">El pago se procesa a través de Stripe. El pack se activa automáticamente tras confirmar.</p>
  </div>

  <div id="main-content" style="display:none">
    <div class="card">
      <div class="card-title">Resumen</div>
      <div class="stat-row">
        <div class="stat"><div class="stat-val" id="stat-total">—</div><div class="stat-label">Runs totales</div></div>
        <div class="stat"><div class="stat-val" id="stat-done">—</div><div class="stat-label">Completados</div></div>
        <div class="stat"><div class="stat-val" id="stat-cost">—</div><div class="stat-label">Coste total</div></div>
      </div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px">
        <div class="card-title" style="margin-bottom:0">Attestations</div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-outline" onclick="exportZip()">⬇ Exportar ZIP</button>
        </div>
      </div>
      <div id="table-wrapper">
        <table>
          <thead><tr>
            <th>Run ID</th><th>Equipo</th><th>Estado</th><th>Coste</th><th>Fecha</th><th></th>
          </tr></thead>
          <tbody id="runs-tbody"></tbody>
        </table>
        <div id="empty-state" class="empty" style="display:none">No hay runs en este workspace todavía.</div>
      </div>
      <div class="pagination">
        <button class="pg-btn" id="prev-btn" onclick="prevPage()" disabled>← Anterior</button>
        <span style="padding:6px 10px;font-size:12px;color:#5a5a7a" id="page-info"></span>
        <button class="pg-btn" id="next-btn" onclick="nextPage()">Siguiente →</button>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const PACK_ENABLED = {{PACK_ENABLED}};
const PAGE_SIZE = 25;
let page = 0;
let totalRuns = [];

function getKey() {
  return localStorage.getItem('antcrew_compliance_key') || '';
}
function saveKey() {
  const k = document.getElementById('api-key-input').value.trim();
  if (!k) return;
  localStorage.setItem('antcrew_compliance_key', k);
  document.getElementById('auth-card').style.display = 'none';
  init();
}
function headers() {
  const k = getKey();
  return k ? {'X-Api-Key': k} : {};
}
function toast(msg, color='#818CF8') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = color;
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 3000);
}

async function init() {
  // Pack status
  try {
    const r = await fetch('/compliance/status', {headers: headers()});
    if (r.status === 401 || r.status === 403) {
      document.getElementById('auth-card').style.display = 'block';
      return;
    }
    const d = await r.json();
    const chip = document.getElementById('status-chip');
    chip.innerHTML = d.enabled
      ? '<span class="chip chip-on">● Compliance Pack activo</span>'
      : '<span class="chip chip-off">● Compliance Pack inactivo</span>';
    if (!d.enabled) {
      document.getElementById('upgrade-banner').style.display = 'block';
      return;
    }
  } catch(e) {
    document.getElementById('auth-card').style.display = 'block';
    return;
  }

  document.getElementById('main-content').style.display = 'block';
  await loadRuns();
}

async function loadRuns() {
  try {
    const r = await fetch('/compliance/attestations?limit=200', {headers: headers()});
    if (!r.ok) { toast('Error cargando runs: ' + r.status, '#f87171'); return; }
    totalRuns = await r.json();
  } catch(e) { toast('Error de red', '#f87171'); return; }

  // Stats
  const done = totalRuns.filter(r => r.status === 'done');
  const totalCost = totalRuns.reduce((s, r) => s + (r.cost_usd || 0), 0);
  document.getElementById('stat-total').textContent = totalRuns.length;
  document.getElementById('stat-done').textContent = done.length;
  document.getElementById('stat-cost').textContent = '$' + totalCost.toFixed(4);

  renderPage();
}

function renderPage() {
  const start = page * PAGE_SIZE;
  const slice = totalRuns.slice(start, start + PAGE_SIZE);
  const tbody = document.getElementById('runs-tbody');
  const empty = document.getElementById('empty-state');

  if (totalRuns.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
  } else {
    empty.style.display = 'none';
    tbody.innerHTML = slice.map(r => {
      const statusCls = r.status === 'done' ? 'status-done' : r.status === 'error' ? 'status-error' : 'status-other';
      const date = r.created_at ? new Date(r.created_at).toLocaleString('es-ES') : '—';
      return `<tr>
        <td><span class="run-id">${r.run_id.slice(0,12)}</span></td>
        <td>${r.team || '—'}</td>
        <td><span class="${statusCls}">${r.status}</span></td>
        <td>$${(r.cost_usd || 0).toFixed(4)}</td>
        <td style="color:#8888a8;font-size:12px">${date}</td>
        <td><a href="${r.attestation_url}" target="_blank" style="color:#818CF8;font-size:12px;text-decoration:none">↓ JSON</a></td>
      </tr>`;
    }).join('');
  }

  const total = totalRuns.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  document.getElementById('page-info').textContent = `Página ${page + 1} de ${totalPages}`;
  document.getElementById('prev-btn').disabled = page === 0;
  document.getElementById('next-btn').disabled = (page + 1) >= totalPages;
}

function prevPage() { if (page > 0) { page--; renderPage(); } }
function nextPage() { page++; renderPage(); }

async function exportZip() {
  toast('Generando ZIP…');
  try {
    const r = await fetch('/compliance/export', {headers: headers()});
    if (!r.ok) { toast('Error: ' + r.status, '#f87171'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const cd = r.headers.get('content-disposition') || '';
    const m = cd.match(/filename="([^"]+)"/);
    a.download = m ? m[1] : 'compliance_export.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast('ZIP descargado ✓', '#4ade80');
  } catch(e) { toast('Error de red', '#f87171'); }
}

async function startCheckout() {
  const btn = document.getElementById('checkout-btn');
  const msg = document.getElementById('checkout-msg');
  const cycle = document.getElementById('billing-cycle-sel').value;
  btn.disabled = true;
  btn.textContent = 'Procesando…';
  msg.style.display = 'none';
  try {
    const r = await fetch('/compliance/checkout', {
      method: 'POST',
      headers: {...headers(), 'Content-Type': 'application/json'},
      body: JSON.stringify({billing_cycle: cycle}),
    });
    if (r.status === 409) {
      msg.textContent = 'El pack ya está activo. Recarga la página.';
      msg.style.display = 'block';
      return;
    }
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      msg.textContent = 'Error al iniciar el pago: ' + (e.detail || r.status);
      msg.style.display = 'block';
      return;
    }
    const d = await r.json();
    window.location.href = d.checkout_url;
  } catch(e) {
    msg.textContent = 'Error de red: ' + e.message;
    msg.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Activar Compliance Pack';
  }
}

// Init
if (!getKey()) {
  document.getElementById('auth-card').style.display = 'block';
} else {
  init();
}
</script>
</body>
</html>"""


class CheckoutIn(BaseModel):
    billing_cycle: str = "monthly"  # monthly | annual


class CheckoutOut(BaseModel):
    checkout_url: str


@router.post("/checkout", response_model=CheckoutOut)
async def compliance_checkout(
    body: CheckoutIn,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Create a Stripe Checkout Session for the Compliance Pack add-on.

    Required env vars:
      STRIPE_SECRET_KEY
      STRIPE_COMPLIANCE_PRICE_MONTHLY_ID   — Stripe price ID for monthly billing
      STRIPE_COMPLIANCE_PRICE_ANNUAL_ID    — Stripe price ID for annual billing
      STRIPE_SUCCESS_URL / STRIPE_CANCEL_URL

    On successful payment, Stripe fires ``checkout.session.completed`` with
    ``metadata.pack = "compliance"`` and ``metadata.workspace_id``.  The billing
    webhook handler picks this up and automatically enables the pack.
    """
    import os as _os

    from app.services.billing import _stripe

    if body.billing_cycle not in ("monthly", "annual"):
        raise HTTPException(422, "billing_cycle must be 'monthly' or 'annual'")

    s = _stripe()
    price_env = (
        "STRIPE_COMPLIANCE_PRICE_MONTHLY_ID"
        if body.billing_cycle == "monthly"
        else "STRIPE_COMPLIANCE_PRICE_ANNUAL_ID"
    )
    price_id = _os.environ.get(price_env)
    success_url = _os.environ.get("STRIPE_SUCCESS_URL")
    cancel_url = _os.environ.get("STRIPE_CANCEL_URL")

    ws: Optional[Workspace] = await session.get(Workspace, ctx.workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")

    # Check before querying Stripe so the 409 takes priority over 503
    if ws.compliance_pack_enabled:
        raise HTTPException(409, "Compliance Pack is already enabled for this workspace")

    if not s:
        raise HTTPException(503, "Stripe not configured. Set STRIPE_SECRET_KEY.")

    if not price_id:
        raise HTTPException(503, f"{price_env} not configured.")
    if not success_url or not cancel_url:
        raise HTTPException(503, "STRIPE_SUCCESS_URL and STRIPE_CANCEL_URL required.")

    try:
        loop = asyncio.get_running_loop()
        checkout = await loop.run_in_executor(
            None,
            lambda: s.checkout.Session.create(
                mode="subscription",
                customer=ws.stripe_customer_id or None,
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "workspace_id": str(ctx.workspace_id),
                    "slug": ws.slug,
                    "pack": "compliance",
                    "billing_cycle": body.billing_cycle,
                },
                subscription_data={
                    "metadata": {
                        "pack": "compliance",
                        "workspace_id": str(ctx.workspace_id),
                    },
                },
            ),
        )
    except Exception as exc:
        log.error("compliance checkout failed for ws %d: %s", ctx.workspace_id, exc)
        raise HTTPException(502, f"Stripe error: {exc}")

    return CheckoutOut(checkout_url=checkout.url)


@router.get("/export")
async def export_attestations(
    ws: Workspace = Depends(_require_compliance_pack),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
    limit: int = Query(100, ge=1, le=500, description="Max completed runs to include"),
    include_keybridge_audit: bool = Query(False, description="Fetch and bundle the keybridge JSONL audit log"),
):
    """Download a ZIP archive of HMAC-signed attestation JSONs for completed runs.

    Contents:
      attestation-{run_id[:12]}.json — one per run, SHA-256 + optional HMAC-signed
      manifest.json                  — audit source inventory (platform + external)
      keybridge_audit.jsonl          — keybridge SHA-256-chain audit log (opt-in)

    The ZIP can be submitted directly to a compliance auditor.
    Set include_keybridge_audit=true + configure KEYBRIDGE_URL + KEYBRIDGE_METRICS_TOKEN
    to bundle the key-proxy audit log in the same archive.
    """
    from app.models.run import Run as RunModel

    # Engine writes terminal status as "success" (not "done").
    # Accept "done" as well for backward compatibility with any legacy data.
    runs = (await session.exec(
        select(RunModel)
        .where(
            RunModel.workspace_id.in_(ctx.workspace_ids),
            RunModel.status.in_(["success", "done"]),
        )
        .order_by(RunModel.created_at.desc())
        .limit(limit)
    )).all()

    if not runs:
        raise HTTPException(404, "No completed runs found for export")

    timestamp = datetime.now(timezone.utc)
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Per-run attestation JSONs — collect all first so traceability can reference them
        attestation_docs: list[dict] = []
        for run in runs:
            doc = await _build_attestation(run, session)
            attestation_docs.append(doc)
            zf.writestr(
                f"attestations/attestation-{run.run_id[:12]}.json",
                json.dumps(doc, indent=2, default=str),
            )

        # 1b. Document traceability per run (included when attestation has doc_traceability)
        all_doc_traces = []
        for run, doc in zip(runs, attestation_docs):
            dt = doc.get("doc_traceability")
            if dt:
                all_doc_traces.append({
                    "run_id": run.run_id,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    **dt,
                })
        if all_doc_traces:
            zf.writestr(
                "doc_traceability.json",
                json.dumps(all_doc_traces, indent=2, default=str),
            )

        # 2. Keybridge audit log (opt-in)
        keybridge_audit_status = "not_requested"
        keybridge_url = os.environ.get("KEYBRIDGE_URL", "").rstrip("/")
        keybridge_token = os.environ.get("KEYBRIDGE_METRICS_TOKEN", "")
        if include_keybridge_audit:
            if keybridge_url and keybridge_token:
                try:
                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=30) as hx:
                        r = await hx.get(
                            f"{keybridge_url}/audit/export",
                            headers={"X-Api-Key": keybridge_token},
                            params={
                                "since": min(r.created_at for r in runs).isoformat()
                                if runs else "",
                            },
                        )
                    if r.status_code == 200:
                        zf.writestr("keybridge_audit.jsonl", r.content)
                        keybridge_audit_status = "included"
                        log.info("compliance export: keybridge audit included (%d bytes)", len(r.content))
                    else:
                        keybridge_audit_status = f"fetch_error_http{r.status_code}"
                        log.warning("compliance export: keybridge audit fetch failed: %s", r.status_code)
                except Exception as exc:
                    keybridge_audit_status = f"fetch_error: {exc}"
                    log.warning("compliance export: keybridge audit fetch error: %s", exc)
            else:
                keybridge_audit_status = "not_configured"

        # 3. Audit source manifest
        manifest = {
            "schema_version": "1.0",
            "generated_at": timestamp.isoformat(),
            "workspace_slug": ws.slug,
            "runs_exported": len(runs),
            "audit_sources": {
                "platform_attestations": {
                    "description": "HMAC-signed run attestations with TraceLog digest",
                    "count": len(runs),
                    "files": "attestations/attestation-*.json",
                },
                "keybridge_audit": {
                    "description": "SHA-256 hash-chain JSONL audit log from keybridge (LLM key proxy)",
                    "status": keybridge_audit_status,
                    "file": "keybridge_audit.jsonl" if keybridge_audit_status == "included" else None,
                    "export_endpoint": f"{keybridge_url}/audit/export" if keybridge_url else None,
                    "verify_endpoint": f"{keybridge_url}/audit/verify" if keybridge_url else None,
                },
                "remote_gateway_usage": {
                    "description": "Per-session token usage from Remote Gateway (local agent driver)",
                    "status": "not_bundled",
                    "export_endpoint": os.environ.get("REMOTE_GATEWAY_URL", "").rstrip("/") + "/usage"
                    if os.environ.get("REMOTE_GATEWAY_URL") else None,
                    "note": "Fetch separately from /usage endpoint using the gateway bearer token",
                },
            },
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

    buf.seek(0)
    zip_name = f"compliance_export_{ws.slug}_{timestamp_str}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ---------------------------------------------------------------------------
# Approved Agent Hash registry — UC3 Certified Agent flow
# ---------------------------------------------------------------------------

class ApprovedHashIn(BaseModel):
    team: str
    agent_name: str
    governance_hash: str
    label: str = ""


class ApprovedHashOut(BaseModel):
    id: int
    workspace_id: int
    team: str
    agent_name: str
    governance_hash: str
    label: str
    registered_at: datetime
    active: bool


@router.post("/approved-hashes", response_model=ApprovedHashOut, status_code=201)
async def register_approved_hash(
    body: ApprovedHashIn,
    ws: Workspace = Depends(_require_compliance_pack),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Register an approved governance hash for an agent.

    Once registered, any run of *team* where *agent_name* produced a different
    ``governance_hash`` will be flagged as ``drifted`` in its certificate.
    Multiple approved hashes per agent are not supported — registering a second
    hash for the same (team, agent_name) raises 409.
    """
    existing = (await session.exec(
        select(ApprovedAgentHash)
        .where(
            ApprovedAgentHash.workspace_id == ws.id,
            ApprovedAgentHash.team == body.team,
            ApprovedAgentHash.agent_name == body.agent_name,
            ApprovedAgentHash.active.is_(True),
        )
    )).first()
    if existing:
        raise HTTPException(409, f"An active approved hash already exists for {body.team}/{body.agent_name}. "
                                 "Delete it first (DELETE /compliance/approved-hashes/{id}).")

    row = ApprovedAgentHash(
        workspace_id=ws.id,
        team=body.team,
        agent_name=body.agent_name,
        governance_hash=body.governance_hash,
        label=body.label,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ApprovedHashOut(
        id=row.id,
        workspace_id=row.workspace_id,
        team=row.team,
        agent_name=row.agent_name,
        governance_hash=row.governance_hash,
        label=row.label,
        registered_at=row.registered_at,
        active=row.active,
    )


@router.get("/approved-hashes", response_model=list[ApprovedHashOut])
async def list_approved_hashes(
    ws: Workspace = Depends(_require_compliance_pack),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
    team: Optional[str] = Query(None),
):
    """List active approved governance hashes for this workspace."""
    q = select(ApprovedAgentHash).where(
        ApprovedAgentHash.workspace_id == ws.id,
        ApprovedAgentHash.active.is_(True),
    ).order_by(ApprovedAgentHash.id)
    if team:
        q = q.where(ApprovedAgentHash.team == team)
    rows = (await session.exec(q)).all()
    return [
        ApprovedHashOut(
            id=r.id,
            workspace_id=r.workspace_id,
            team=r.team,
            agent_name=r.agent_name,
            governance_hash=r.governance_hash,
            label=r.label,
            registered_at=r.registered_at,
            active=r.active,
        )
        for r in rows
    ]


@router.delete("/approved-hashes/{hash_id}", status_code=200)
async def delete_approved_hash(
    hash_id: int,
    ws: Workspace = Depends(_require_compliance_pack),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Deactivate an approved hash (soft delete).

    The hash record is retained for audit purposes — historical certificates
    generated while the hash was active remain valid.
    """
    row = await session.get(ApprovedAgentHash, hash_id)
    if not row or row.workspace_id != ws.id:
        raise HTTPException(404, "Approved hash not found")
    row.active = False
    session.add(row)
    await session.commit()
    return {"id": hash_id, "active": False}
