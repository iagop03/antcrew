"""Workspace run presets, knowledge base, and cost-routing-policy routes."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    WorkspaceContext,
    get_workspace_context,
    require_api_key,
    require_role,
    ws_accessible,
)
from app.core.database import get_session
from app.models.run import RunPreset, Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

_KB_BASE_DIR = Path(os.environ.get("ANTCREW_KB_DIR", "./data/kb"))
_EMPTY_KB: dict = {
    "endpoints": [], "models": [], "services": [],
    "dependencies": {}, "tech_stack": [], "decisions": [],
}


class RunPresetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    name: str
    team: str
    model_overrides: Optional[dict] = None
    max_messages: Optional[int] = None
    created_at: Optional[datetime] = None


class CreateRunPreset(BaseModel):
    name: str
    team: str
    model_overrides: Optional[dict] = None
    max_messages: Optional[int] = None


class UpdateRunPreset(BaseModel):
    model_overrides: Optional[dict] = None
    max_messages: Optional[int] = None


class RoutingOut(BaseModel):
    workspace_id: int
    cost_routing_policy: str
    tier_cheap_model: str
    tier_standard_model: str
    tier_premium_model: str


class RoutingPatch(BaseModel):
    cost_routing_policy: str

    @field_validator("cost_routing_policy")
    @classmethod
    def _valid_policy(cls, v: str) -> str:
        if v not in ("none", "economy", "auto"):
            raise ValueError("cost_routing_policy must be 'none', 'economy', or 'auto'")
        return v


@router.get("/{workspace_id}/presets", response_model=list[RunPresetPublic],
            dependencies=[Depends(require_role("admin", "write"))])
async def list_presets(
    workspace_id: int,
    team: Optional[str] = Query(None),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    q = select(RunPreset).where(RunPreset.workspace_id == workspace_id)
    if team:
        q = q.where(RunPreset.team == team)
    results = (await session.exec(q.order_by(RunPreset.name))).all()
    return results


@router.post("/{workspace_id}/presets", response_model=RunPresetPublic, status_code=201,
             dependencies=[Depends(require_role("admin", "write"))])
async def create_preset(
    workspace_id: int,
    body: CreateRunPreset,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    preset = RunPreset(
        workspace_id=workspace_id,
        name=body.name,
        team=body.team,
        model_overrides=body.model_overrides or None,
        max_messages=body.max_messages,
        created_by=ctx.created_by,
    )
    session.add(preset)
    await session.commit()
    await session.refresh(preset)
    return preset


@router.patch("/{workspace_id}/presets/{preset_id}", response_model=RunPresetPublic,
              dependencies=[Depends(require_role("admin", "write"))])
async def update_preset(
    workspace_id: int,
    preset_id: int,
    body: UpdateRunPreset,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Update model_overrides or max_messages on an existing preset.

    max_messages on the preset takes precedence over workspace.max_messages when
    both are set. Pass null to remove the preset-level limit and fall back to the
    workspace default.
    """
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    preset = await session.get(RunPreset, preset_id)
    if not preset or preset.workspace_id != workspace_id:
        raise HTTPException(404, "Preset not found")
    if body.model_overrides is not None:
        preset.model_overrides = body.model_overrides
    if "max_messages" in body.model_fields_set:
        if body.max_messages is not None and body.max_messages < 1:
            raise HTTPException(422, "max_messages must be a positive integer or null")
        preset.max_messages = body.max_messages
    session.add(preset)
    await session.commit()
    await session.refresh(preset)
    return preset


@router.delete("/{workspace_id}/presets/{preset_id}", status_code=204,
               dependencies=[Depends(require_role("admin", "write"))])
async def delete_preset(
    workspace_id: int,
    preset_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    preset = await session.get(RunPreset, preset_id)
    if not preset or preset.workspace_id != workspace_id:
        raise HTTPException(404, "Preset not found")
    await session.delete(preset)
    await session.commit()


@router.get("/{workspace_id}/kb")
async def get_workspace_kb(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the ProjectKB JSON for this workspace (default: empty structure)."""
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    kb_file = _KB_BASE_DIR / str(workspace_id) / "project_kb.json"
    if not kb_file.exists():
        return _EMPTY_KB
    try:
        return json.loads(kb_file.read_text(encoding="utf-8"))
    except Exception:
        return _EMPTY_KB


@router.delete("/{workspace_id}/kb", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def reset_workspace_kb(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Delete (reset) the ProjectKB for this workspace."""
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    kb_file = _KB_BASE_DIR / str(workspace_id) / "project_kb.json"
    if kb_file.exists():
        kb_file.unlink()


@router.get("/{workspace_id}/routing", response_model=RoutingOut,
            dependencies=[Depends(require_role("admin", "write"))])
async def get_routing_policy(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Return the current cost routing policy and effective tier models."""
    from app.models.admin import PlatformConfig
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    pc = await session.get(PlatformConfig, 1)
    return RoutingOut(
        workspace_id=workspace_id,
        cost_routing_policy=getattr(ws, "cost_routing_policy", "none") or "none",
        tier_cheap_model=getattr(pc, "tier_cheap_model", "groq:llama-3.3-70b-versatile") if pc else "groq:llama-3.3-70b-versatile",
        tier_standard_model=getattr(pc, "tier_standard_model", "claude:claude-sonnet-5") if pc else "claude:claude-sonnet-5",
        tier_premium_model=getattr(pc, "tier_premium_model", "claude:claude-opus-5") if pc else "claude:claude-opus-5",
    )


@router.patch("/{workspace_id}/routing", response_model=RoutingOut,
              dependencies=[Depends(require_role("admin"))])
async def set_routing_policy(
    workspace_id: int,
    body: RoutingPatch,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set the cost routing policy: none | economy | auto."""
    from app.models.admin import PlatformConfig
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    ws.cost_routing_policy = body.cost_routing_policy
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    pc = await session.get(PlatformConfig, 1)
    return RoutingOut(
        workspace_id=workspace_id,
        cost_routing_policy=ws.cost_routing_policy,
        tier_cheap_model=getattr(pc, "tier_cheap_model", "groq:llama-3.3-70b-versatile") if pc else "groq:llama-3.3-70b-versatile",
        tier_standard_model=getattr(pc, "tier_standard_model", "claude:claude-sonnet-5") if pc else "claude:claude-sonnet-5",
        tier_premium_model=getattr(pc, "tier_premium_model", "claude:claude-opus-5") if pc else "claude:claude-opus-5",
    )


class CliWorkingDirPatch(BaseModel):
    cli_working_dir: Optional[str] = None


class CliWorkingDirOut(BaseModel):
    workspace_id: int
    cli_working_dir: Optional[str] = None


@router.get("/{workspace_id}/cli-working-dir", response_model=CliWorkingDirOut,
            dependencies=[Depends(require_role("admin", "write"))])
async def get_cli_working_dir(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Return the CLI working directory configured for this workspace."""
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    return CliWorkingDirOut(
        workspace_id=workspace_id,
        cli_working_dir=getattr(ws, "cli_working_dir", None),
    )


@router.patch("/{workspace_id}/cli-working-dir", response_model=CliWorkingDirOut,
              dependencies=[Depends(require_role("admin"))])
async def set_cli_working_dir(
    workspace_id: int,
    body: CliWorkingDirPatch,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set (or clear) the working directory sent to remote-gateway CLI drivers.

    When set, every run in this workspace passes ``working_directory`` in the
    Anthropic ``extra_body``, which remote-gateway uses to:
    - Route the call to the correct on-disk workspace for claude-code/gemini/codex
    - Serialize concurrent calls that share the same working directory
    - Attribute token usage to the directory in ``GET /usage``

    Pass ``null`` to clear the override (remote-gateway picks its default).
    """
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws or not ws_accessible(ws.id, ctx):
        raise HTTPException(404, "Workspace not found")
    ws.cli_working_dir = body.cli_working_dir
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return CliWorkingDirOut(
        workspace_id=workspace_id,
        cli_working_dir=getattr(ws, "cli_working_dir", None),
    )
