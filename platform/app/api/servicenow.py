"""ServiceNow integration — create incidents and change requests from antcrew runs.

Feature gate: servicenow (regulated tier).

Credentials are stored per-workspace using the existing TicketDestination table
(provider="servicenow") with an EncryptedJSON config column. The password is
encrypted with SERVICENOW_ENCRYPTION_KEY (Fernet); falls back to plaintext in dev.

Endpoints:
  GET  /servicenow/config                  — workspace ServiceNow configuration
  PUT  /servicenow/config                  — upsert configuration
  POST /servicenow/test-connection         — verify credentials against the instance
  POST /servicenow/runs/{run_id}/incident  — open an incident / change request from a run
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.database import get_session
from app.core.license_gate import require_feature
from app.models.integrations import TicketDestination

log = logging.getLogger(__name__)

_PROVIDER = "servicenow"

router = APIRouter(
    prefix="/servicenow",
    tags=["servicenow"],
    dependencies=[Depends(require_feature("servicenow"))],
)


# ---------------------------------------------------------------------------
# Credential encryption helpers
# ---------------------------------------------------------------------------

def _encrypt(secret: str) -> str:
    """Encrypt a credential using SERVICENOW_ENCRYPTION_KEY (Fernet).

    Falls back to plaintext when the key is absent (dev mode).
    """
    key = os.environ.get("SERVICENOW_ENCRYPTION_KEY", "")
    if not key:
        log.warning(
            "servicenow: SERVICENOW_ENCRYPTION_KEY not set — "
            "storing credential in plain text (not suitable for production)"
        )
        return secret
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode()).encrypt(secret.encode()).decode()
    except Exception as exc:
        log.warning("servicenow: encryption failed (%s) — storing plain text", exc)
        return secret


def _decrypt(stored: str) -> str:
    key = os.environ.get("SERVICENOW_ENCRYPTION_KEY", "")
    if not key:
        return stored
    try:
        from cryptography.fernet import Fernet, InvalidToken
        return Fernet(key.encode()).decrypt(stored.encode()).decode()
    except (ImportError, Exception):
        return stored


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_config(workspace_id: int, session) -> Optional[TicketDestination]:
    return (await session.exec(
        select(TicketDestination).where(
            TicketDestination.workspace_id == workspace_id,
            TicketDestination.provider == _PROVIDER,
        )
    )).first()


async def _snow_get(cfg: TicketDestination, path: str) -> dict:
    data = cfg.config_json or {}
    instance_url = data.get("instance_url", "").rstrip("/")
    username = data.get("username", "")
    password = _decrypt(data.get("password_enc", ""))
    url = f"{instance_url}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            url,
            auth=(username, password),
            headers={"Accept": "application/json"},
        )
    if not r.is_success:
        raise HTTPException(502, f"ServiceNow API error: HTTP {r.status_code}")
    return r.json()


async def _snow_post(cfg: TicketDestination, path: str, payload: dict) -> dict:
    data = cfg.config_json or {}
    instance_url = data.get("instance_url", "").rstrip("/")
    username = data.get("username", "")
    password = _decrypt(data.get("password_enc", ""))
    url = f"{instance_url}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            url,
            auth=(username, password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload,
        )
    if not r.is_success:
        raise HTTPException(502, f"ServiceNow API error: HTTP {r.status_code} — {r.text[:200]}")
    return r.json()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ServiceNowConfigOut(BaseModel):
    workspace_id: int
    instance_url: Optional[str] = None
    username: Optional[str] = None
    table: str = "incident"
    configured: bool


class ServiceNowConfigIn(BaseModel):
    instance_url: str
    username: str
    password: str
    table: str = "incident"  # incident | change_request | problem


class IncidentCreated(BaseModel):
    sys_id: str
    number: str
    url: str
    short_description: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config", response_model=ServiceNowConfigOut)
async def get_servicenow_config(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Return ServiceNow integration config for this workspace (password masked)."""
    cfg = await _get_config(ctx.workspace_id, session)
    if cfg is None:
        return ServiceNowConfigOut(workspace_id=ctx.workspace_id, configured=False)
    data = cfg.config_json or {}
    return ServiceNowConfigOut(
        workspace_id=ctx.workspace_id,
        instance_url=data.get("instance_url"),
        username=data.get("username"),
        table=data.get("table", "incident"),
        configured=bool(data.get("instance_url")),
    )


@router.put("/config", response_model=ServiceNowConfigOut)
async def set_servicenow_config(
    body: ServiceNowConfigIn,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Configure ServiceNow credentials for this workspace.

    Credentials are encrypted at rest using Fernet (SERVICENOW_ENCRYPTION_KEY).
    Set table to 'incident', 'change_request', or 'problem' depending on your
    ServiceNow workflow.
    """
    if ctx.role != "admin":
        raise HTTPException(403, "Admin role required")
    cfg = await _get_config(ctx.workspace_id, session)
    if cfg is None:
        cfg = TicketDestination(
            workspace_id=ctx.workspace_id,
            provider=_PROVIDER,
            label="ServiceNow",
            config_json={},
        )
    cfg.config_json = {
        "instance_url": body.instance_url.rstrip("/"),
        "username": body.username,
        "password_enc": _encrypt(body.password),
        "table": body.table,
    }
    cfg.enabled = True
    session.add(cfg)
    await session.commit()
    return ServiceNowConfigOut(
        workspace_id=ctx.workspace_id,
        instance_url=body.instance_url.rstrip("/"),
        username=body.username,
        table=body.table,
        configured=True,
    )


@router.post("/test-connection")
async def test_servicenow_connection(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Verify connectivity and authentication against the configured ServiceNow instance."""
    cfg = await _get_config(ctx.workspace_id, session)
    if not cfg or not (cfg.config_json or {}).get("instance_url"):
        raise HTTPException(400, "ServiceNow not configured for this workspace")
    try:
        result = await _snow_get(
            cfg, "/api/now/table/sys_user?sysparm_limit=1&sysparm_fields=user_name"
        )
        return {
            "ok": True,
            "instance": (cfg.config_json or {}).get("instance_url"),
            "detail": "Authentication successful",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"ServiceNow connection failed: {exc}")


@router.post("/runs/{run_id}/incident", response_model=IncidentCreated, status_code=201)
async def create_incident_from_run(
    run_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Open a ServiceNow incident (or change request) linked to a completed run.

    Uses the table configured in the workspace ServiceNow settings.
    The run must belong to this workspace.
    """
    from app.models.run import Run as RunModel

    run = (await session.exec(
        select(RunModel).where(
            RunModel.run_id == run_id,
            RunModel.workspace_id.in_(ctx.workspace_ids),
        )
    )).first()
    if not run:
        raise HTTPException(404, "Run not found")

    cfg = await _get_config(ctx.workspace_id, session)
    data = cfg.config_json or {} if cfg else {}
    if not data.get("instance_url"):
        raise HTTPException(400, "ServiceNow not configured for this workspace")

    table = data.get("table", "incident")
    team_str = f"{run.team} " if getattr(run, "team", None) else ""
    short_desc = f"[antcrew] {team_str}run {run.run_id[:12]} — {run.status}"

    lines = [
        "antcrew run report",
        f"Run ID:    {run.run_id}",
        f"Team:      {getattr(run, 'team', 'N/A') or 'N/A'}",
        f"Status:    {run.status}",
        f"Created:   {run.created_at}",
        f"Finished:  {getattr(run, 'finished_at', None) or 'N/A'}",
    ]
    if getattr(run, "cost_usd", None) is not None:
        lines.append(f"Cost:      ${run.cost_usd:.4f}")
    base_url = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    if base_url:
        lines.append(f"\nReview at: {base_url}/run/{run.run_id}")

    result = await _snow_post(cfg, f"/api/now/table/{table}", {
        "short_description": short_desc,
        "description": "\n".join(lines),
        "category": "software",
        "subcategory": "ai_automation",
        "caller_id": data.get("username", ""),
        "urgency": "3",
        "impact": "3",
    })
    record = result.get("result", {})
    sys_id = record.get("sys_id", "")
    number = record.get("number", "")
    instance_url = data.get("instance_url", "")
    return IncidentCreated(
        sys_id=sys_id,
        number=number,
        url=f"{instance_url}/nav_to.do?uri={table}.do%3Fsys_id%3D{sys_id}",
        short_description=short_desc,
    )
