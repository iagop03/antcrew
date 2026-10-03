"""API endpoints for PM integration destinations (GitHub Issues, Linear, Jira)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    WorkspaceContext,
    get_workspace_context,
    require_api_key,
    require_role,
)
from app.core.database import get_session
from app.models.integrations import TicketDestination

router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_api_key)],
)

_VALID_PROVIDERS = frozenset({"github", "linear", "jira"})

_REQUIRED_CONFIG_KEYS: dict[str, list[str]] = {
    "github": ["token", "owner", "repo"],
    "linear": ["api_key", "team_id"],
    "jira": ["base_url", "email", "api_token", "project_key"],
}


class _DestCreate(BaseModel):
    provider: str
    label: str
    config_json: dict
    team_filter: Optional[str] = None
    enabled: bool = True


class _DestPatch(BaseModel):
    label: Optional[str] = None
    config_json: Optional[dict] = None
    team_filter: Optional[str] = None
    enabled: Optional[bool] = None


def _mask(dest: TicketDestination) -> dict:
    """Return the destination dict with credential values partially redacted."""
    cfg = dict(dest.config_json or {})
    for key in ("token", "api_key", "api_token"):
        if key in cfg:
            val = str(cfg[key])
            cfg[key] = val[:4] + "***" if len(val) > 4 else "***"
    return {
        "id": dest.id,
        "workspace_id": dest.workspace_id,
        "provider": dest.provider,
        "label": dest.label,
        "config": cfg,
        "team_filter": dest.team_filter,
        "enabled": dest.enabled,
        "created_at": dest.created_at.isoformat() if dest.created_at else None,
    }


@router.get("/")
async def list_destinations(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[dict]:
    """List all configured PM integration destinations for the workspace."""
    if ctx.workspace_id is None:
        return []
    dests = (await session.exec(
        select(TicketDestination)
        .where(TicketDestination.workspace_id == ctx.workspace_id)
        .order_by(TicketDestination.id)
    )).all()
    return [_mask(d) for d in dests]


@router.post("/", status_code=201,
             dependencies=[Depends(require_role("admin", "write"))])
async def create_destination(
    body: _DestCreate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Add a new PM integration destination for the workspace."""
    if body.provider not in _VALID_PROVIDERS:
        raise HTTPException(422, f"provider must be one of: {sorted(_VALID_PROVIDERS)}")
    if not body.label.strip():
        raise HTTPException(422, "label must not be empty")
    if ctx.workspace_id is None:
        raise HTTPException(403, "Workspace-scoped API key required")
    required = _REQUIRED_CONFIG_KEYS.get(body.provider, [])
    missing = [k for k in required if not body.config_json.get(k)]
    if missing:
        raise HTTPException(422, f"config_json missing required keys for {body.provider}: {missing}")

    dest = TicketDestination(
        workspace_id=ctx.workspace_id,
        provider=body.provider,
        label=body.label.strip(),
        config_json=body.config_json,
        team_filter=body.team_filter,
        enabled=body.enabled,
    )
    session.add(dest)
    await session.commit()
    await session.refresh(dest)
    return _mask(dest)


@router.patch("/{dest_id}",
              dependencies=[Depends(require_role("admin", "write"))])
async def update_destination(
    dest_id: int,
    body: _DestPatch,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Update an existing PM integration destination (partial update)."""
    dest = await session.get(TicketDestination, dest_id)
    if not dest or dest.workspace_id != ctx.workspace_id:
        raise HTTPException(404, f"Integration {dest_id} not found")
    if body.label is not None:
        dest.label = body.label.strip()
    if body.config_json is not None:
        dest.config_json = body.config_json
    if body.team_filter is not None:
        dest.team_filter = body.team_filter
    if body.enabled is not None:
        dest.enabled = body.enabled
    session.add(dest)
    await session.commit()
    await session.refresh(dest)
    return _mask(dest)


@router.delete("/{dest_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_destination(
    dest_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Response:
    """Remove a PM integration destination."""
    dest = await session.get(TicketDestination, dest_id)
    if not dest or dest.workspace_id != ctx.workspace_id:
        raise HTTPException(404, f"Integration {dest_id} not found")
    await session.delete(dest)
    await session.commit()
    return Response(status_code=204)


@router.post("/{dest_id}/test",
             dependencies=[Depends(require_role("admin", "write"))])
async def test_destination(
    dest_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Send a test ticket to verify the integration credentials are valid."""
    dest = await session.get(TicketDestination, dest_id)
    if not dest or dest.workspace_id != ctx.workspace_id:
        raise HTTPException(404, f"Integration {dest_id} not found")

    from app.services.ticket_sync import _PROVIDER_ADAPTERS
    adapter = _PROVIDER_ADAPTERS.get(dest.provider)
    if not adapter:
        raise HTTPException(422, f"Unknown provider: {dest.provider!r}")

    test_ticket = {
        "ticket_id": "TEST-001",
        "title": "[AntCrew] Integration health check",
        "description": "Test ticket from AntCrew to verify the integration is working.",
        "acceptance_criteria": "Ticket is visible in the target system.",
        "priority": "low",
        "ticket_type": "task",
    }
    result = await adapter(test_ticket, dest.config_json or {})
    if result:
        return {"success": True, "provider": dest.provider, "url": result.get("url"), "id": result.get("id")}
    raise HTTPException(
        502,
        f"Integration test failed for {dest.provider!r} — check credentials and config",
    )
