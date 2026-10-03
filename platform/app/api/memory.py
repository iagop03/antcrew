"""Persistent KV memory for agent teams.

Each workspace+team pair has one memory row (a JSON dict).  Agents read and
write arbitrary keys; the platform persists them between runs.

Routes
------
GET  /memory/{team}           → full memory dict for the team
PUT  /memory/{team}           → merge-upsert key-value pairs
DELETE /memory/{team}/{key}   → remove one key
DELETE /memory/{team}         → clear all memory for the team
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.database import get_session

router = APIRouter(prefix="/memory", tags=["memory"])


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create(session, workspace_id, team_name):
    from app.models._utils import _utcnow
    from app.models.memory import RunMemory

    row = (await session.exec(
        select(RunMemory)
        .where(RunMemory.workspace_id == workspace_id)
        .where(RunMemory.team_name == team_name)
    )).first()
    if row is None:
        row = RunMemory(workspace_id=workspace_id, team_name=team_name)
        session.add(row)
        await session.flush()
    return row


# ── schemas ───────────────────────────────────────────────────────────────────

class MemoryPutBody(BaseModel):
    data: dict[str, Any]


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/{team}")
async def get_memory(
    team: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the full KV memory dict for *team* in this workspace."""
    from app.models.memory import RunMemory

    row = (await session.exec(
        select(RunMemory)
        .where(RunMemory.workspace_id == ctx.workspace_id)
        .where(RunMemory.team_name == team)
    )).first()
    if row is None:
        return {}
    return row.memory_json or {}


@router.put("/{team}", status_code=200)
async def put_memory(
    team: str,
    body: MemoryPutBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Merge-upsert key-value pairs into the team's memory.

    Existing keys are overwritten; keys not present in *data* are kept.
    """
    from app.models._utils import _utcnow

    row = await _get_or_create(session, ctx.workspace_id, team)
    merged = dict(row.memory_json or {})
    merged.update(body.data)
    row.memory_json = merged
    row.updated_at = _utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.memory_json


@router.delete("/{team}/{key}", status_code=200)
async def delete_memory_key(
    team: str,
    key: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a single key from the team's memory."""
    from app.models._utils import _utcnow
    from app.models.memory import RunMemory

    row = (await session.exec(
        select(RunMemory)
        .where(RunMemory.workspace_id == ctx.workspace_id)
        .where(RunMemory.team_name == team)
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No memory found for this team")
    mem = dict(row.memory_json or {})
    if key not in mem:
        raise HTTPException(status_code=404, detail=f"Key {key!r} not found in team memory")
    del mem[key]
    row.memory_json = mem
    row.updated_at = _utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.memory_json


@router.delete("/{team}", status_code=204)
async def clear_memory(
    team: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Clear all memory for the team."""
    from app.models.memory import RunMemory

    row = (await session.exec(
        select(RunMemory)
        .where(RunMemory.workspace_id == ctx.workspace_id)
        .where(RunMemory.team_name == team)
    )).first()
    if row:
        await session.delete(row)
        await session.commit()
