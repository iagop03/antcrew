"""Discovery session endpoints — conversational project discovery via DiscoveryAgent.

Flow:
  POST /discovery/sessions            → create session, get first question
  POST /discovery/sessions/{id}/answer → submit answer, get next question
  GET  /discovery/sessions/{id}       → read current state
  POST /discovery/sessions/{id}/run   → finalize → PRD → dispatch pipeline run
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    WorkspaceContext,
    get_workspace_context,
    require_api_key,
    require_role,
)
from app.core.database import get_session
from app.models.discovery import DiscoverySession
from app.models.workspace import Workspace

router = APIRouter(
    prefix="/discovery",
    tags=["discovery"],
    dependencies=[Depends(require_api_key)],
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── private helpers ────────────────────────────────────────────────────────────

def _build_agent(model: str, api_key: Optional[str] = None, base_url: Optional[str] = None):
    """Instantiate a DiscoveryAgent backed by the given model name."""
    from antcrew import DiscoveryAgent, build_llm
    _kw: dict = {}
    if api_key is not None:
        _kw["api_key"] = api_key
    if base_url is not None:
        _kw["base_url"] = base_url
    llm = build_llm(model, **_kw)
    return DiscoveryAgent(llm=llm)


async def _resolve_llm_config(
    db: AsyncSession,
    workspace_id: Optional[int],
    model: str,
) -> tuple[Optional[str], Optional[str]]:
    """Return (api_key, base_url) for the workspace's LLM mode, or (None, None) if unset."""
    if not workspace_id:
        return None, None
    ws_result = await db.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.first()
    if not ws:
        return None, None
    from app.services.runner_base import resolve_workspace_llm_config
    return await resolve_workspace_llm_config(db, ws, model)


def _load_context(row: DiscoverySession):
    """Deserialise a DiscoverySession row into a DiscoveryContext object."""
    from antcrew import DiscoveryContext
    data = json.loads(row.context_json or "{}")
    return DiscoveryContext(**data)


def _dump_context(ctx) -> str:
    """Serialise a DiscoveryContext to a JSON string for DB storage."""
    return ctx.model_dump_json()


async def _fetch_session(
    session_id: str,
    db: AsyncSession,
    ctx: WorkspaceContext,
) -> DiscoverySession:
    result = await db.exec(
        select(DiscoverySession).where(DiscoverySession.session_id == session_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(404, f"Discovery session {session_id!r} not found")
    if ctx.workspace_ids is not None and row.workspace_id not in ctx.workspace_ids:
        raise HTTPException(403, "Access denied")
    return row


# ── request / response schemas ─────────────────────────────────────────────────

class _CreateBody(BaseModel):
    project_name: str = ""
    model: str = "claude"
    max_rounds: int = Field(default=7, ge=2, le=20)


class _AnswerBody(BaseModel):
    answer: str


class _RunBody(BaseModel):
    team: str = "DevTeam"


# ── endpoints ──────────────────────────────────────────────────────────────────

@router.post("/sessions", status_code=201)
async def create_session(
    body: _CreateBody,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Create a new discovery session and return the first agent question."""
    from antcrew import DiscoveryContext

    sid = str(uuid.uuid4())
    ctx_obj = DiscoveryContext(project_name=body.project_name, max_rounds=body.max_rounds)

    api_key, base_url = await _resolve_llm_config(db, ctx.workspace_id, body.model)
    agent = _build_agent(body.model, api_key=api_key, base_url=base_url)
    result: dict = await asyncio.to_thread(agent.ask_next, ctx_obj)

    row = DiscoverySession(
        session_id=sid,
        workspace_id=ctx.workspace_id,
        model=body.model,
        status="active",
        context_json=_dump_context(ctx_obj),
        current_question=result.get("next_question", ""),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {
        "session_id": sid,
        "question": result.get("next_question", ""),
        "context": json.loads(row.context_json),
    }


@router.post("/sessions/{session_id}/answer")
async def answer_question(
    session_id: str,
    body: _AnswerBody,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Submit an answer and receive the next question (or null when discovery is complete)."""
    row = await _fetch_session(session_id, db, ctx)
    if row.status == "complete":
        raise HTTPException(409, "Session is already complete")

    api_key, base_url = await _resolve_llm_config(db, row.workspace_id, row.model)
    agent = _build_agent(row.model, api_key=api_key, base_url=base_url)
    ctx_obj = _load_context(row)
    question = row.current_question

    # ingest answer → updated context
    ctx_obj = await asyncio.to_thread(agent.ingest, ctx_obj, question, body.answer)

    # ask for the next question
    result: dict = await asyncio.to_thread(agent.ask_next, ctx_obj)
    is_complete: bool = result.get("is_complete", False)
    next_q: str = result.get("next_question", "")

    row.context_json = _dump_context(ctx_obj)
    row.current_question = next_q
    row.updated_at = _utcnow()
    if is_complete:
        row.status = "complete"

    db.add(row)
    await db.commit()

    return {
        "question": next_q if not is_complete else None,
        "is_complete": is_complete,
        "context": json.loads(row.context_json),
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return the current state of a discovery session."""
    row = await _fetch_session(session_id, db, ctx)
    return {
        "session_id": row.session_id,
        "status": row.status,
        "context": json.loads(row.context_json),
        "current_question": row.current_question,
    }


@router.post(
    "/sessions/{session_id}/run",
    status_code=202,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def run_from_session(
    session_id: str,
    body: _RunBody,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Finalize the discovery context into a PRD and dispatch a pipeline run.

    The PRD summary is used as the run request.  Returns the new run_id.
    """
    from app.services.runner import AVAILABLE_TEAMS, dispatch

    row = await _fetch_session(session_id, db, ctx)
    api_key, base_url = await _resolve_llm_config(db, row.workspace_id, row.model)
    agent = _build_agent(row.model, api_key=api_key, base_url=base_url)
    ctx_obj = _load_context(row)

    # finalize → PRD
    prd = await asyncio.to_thread(agent.finalize, ctx_obj)

    # Build a human-readable request from the PRD
    prd_dict = prd.model_dump() if hasattr(prd, "model_dump") else prd.dict()
    title = prd_dict.get("title") or ctx_obj.project_name or "Untitled"
    summary = prd_dict.get("summary") or ""
    goals = prd_dict.get("goals") or []
    goals_text = "\n".join(f"- {g}" for g in goals) if goals else ""
    request_text = f"{title}\n\n{summary}"
    if goals_text:
        request_text += f"\n\nGoals:\n{goals_text}"

    team = body.team
    if team not in AVAILABLE_TEAMS:
        team = AVAILABLE_TEAMS[0]  # fall back to first available team

    try:
        run_id = await dispatch(
            team,
            request_text.strip(),
            "default",
            created_by=ctx.created_by,
            workspace_id=ctx.workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    row.status = "complete"
    row.updated_at = _utcnow()
    db.add(row)
    await db.commit()

    return {"run_id": run_id}
