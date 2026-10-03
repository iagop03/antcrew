"""A2A (Agent-to-Agent) protocol endpoints — expose AntCrew teams as A2A agents.

Implements the Google A2A spec v0.2 (JSON-RPC 2.0 over HTTP + SSE streaming).
Reference: https://google.github.io/A2A/

Routes
------
GET  /a2a/{team}                → AgentCard descriptor (well-known identity)
POST /a2a/{team}                → JSON-RPC 2.0: tasks/send → dispatches a run
GET  /a2a/{team}/runs/{run_id}  → Task status polling (maps A2A state to run status)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.database import get_session
from app.services.runner import AVAILABLE_TEAMS, dispatch

router = APIRouter(prefix="/a2a", tags=["a2a"])

_PLATFORM_URL = "https://antcrew.org"  # overridable via ANTCREW_PLATFORM_URL env var

import os as _os

_PLATFORM_URL = _os.environ.get("ANTCREW_PLATFORM_URL", _PLATFORM_URL).rstrip("/")


# ── AgentCard ─────────────────────────────────────────────────────────────────

def _agent_card(team: str) -> dict:
    """Build an A2A AgentCard for the given team name."""
    return {
        "name": team,
        "description": f"AntCrew {team} — AI agent pipeline. POST a task to run it.",
        "url": f"{_PLATFORM_URL}/a2a/{team}",
        "version": "1.0",
        "provider": {
            "organization": "AntCrew",
            "url": _PLATFORM_URL,
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": "run",
                "name": "Run team",
                "description": f"Execute the {team} pipeline with a goal or request.",
                "inputModes": ["text/plain"],
                "outputModes": ["application/json"],
            }
        ],
    }


# ── JSON-RPC helpers ──────────────────────────────────────────────────────────

def _rpc_ok(rpc_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_err(rpc_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    }


def _extract_text(message: dict) -> str:
    """Extract plain text from an A2A message object."""
    parts = message.get("parts") or []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            return part.get("text", "")
    # Fallback: if message itself has a text field (non-spec clients)
    if isinstance(message.get("text"), str):
        return message["text"]
    return ""


def _run_state_to_a2a(status: str) -> str:
    """Map AntCrew run status to A2A task state."""
    return {
        "running": "working",
        "success": "completed",
        "error": "failed",
        "cancelled": "canceled",
    }.get(status, "working")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{team}")
async def agent_card(team: str) -> JSONResponse:
    """Return the A2A AgentCard descriptor for *team*.

    This is the well-known endpoint external orchestrators use to discover
    what the agent can do and how to call it.
    """
    if team not in AVAILABLE_TEAMS:
        raise HTTPException(
            status_code=404,
            detail=f"Team {team!r} not found. Available: {AVAILABLE_TEAMS}",
        )
    return JSONResponse(_agent_card(team))


@router.post("/{team}")
async def a2a_task(
    team: str,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse:
    """Accept an A2A JSON-RPC 2.0 request and dispatch it as a platform run.

    Supported methods:
    - ``tasks/send``   → dispatch a new run, return task ID (= run_id)
    - ``tasks/get``    → look up run status by task ID
    - ``tasks/cancel`` → cancel a running task (best-effort)
    """
    if team not in AVAILABLE_TEAMS:
        raise HTTPException(
            status_code=404,
            detail=f"Team {team!r} not found. Available: {AVAILABLE_TEAMS}",
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _rpc_err(None, -32700, "Parse error — request body must be JSON"),
            status_code=400,
        )

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    # ── tasks/send ────────────────────────────────────────────────────────────
    if method == "tasks/send":
        message = params.get("message") or {}
        task_id = params.get("id") or uuid.uuid4().hex

        request_text = _extract_text(message)
        if not request_text:
            return JSONResponse(
                _rpc_err(rpc_id, -32602, "message must contain at least one text part"),
                status_code=422,
            )

        run_id = await dispatch(
            team_name=team,
            request=request_text,
            thread_id=task_id,
            workspace_id=ctx.workspace_id,
            created_by=ctx.created_by,
        )

        if not run_id:
            return JSONResponse(
                _rpc_err(rpc_id, -32603, "Run dispatch timed out — team may be overloaded"),
                status_code=503,
            )

        return JSONResponse(_rpc_ok(rpc_id, {
            "id": run_id,
            "sessionId": task_id,
            "status": {
                "state": "submitted",
                "timestamp": None,
            },
            "artifacts": [],
            "metadata": {
                "antcrew_run_id": run_id,
                "team": team,
                "stream_url": f"{_PLATFORM_URL}/stream/{run_id}",
            },
        }), status_code=202)

    # ── tasks/get ─────────────────────────────────────────────────────────────
    if method == "tasks/get":
        run_id = params.get("id") or params.get("taskId")
        if not run_id:
            return JSONResponse(
                _rpc_err(rpc_id, -32602, "params.id (run_id) is required"),
                status_code=422,
            )
        return await _get_task(rpc_id, run_id, ctx)

    # ── tasks/cancel ──────────────────────────────────────────────────────────
    if method == "tasks/cancel":
        run_id = params.get("id") or params.get("taskId")
        if not run_id:
            return JSONResponse(
                _rpc_err(rpc_id, -32602, "params.id (run_id) is required"),
                status_code=422,
            )
        # Best-effort: mark as cancelled in DB
        await _cancel_run(run_id, ctx.workspace_id)
        return JSONResponse(_rpc_ok(rpc_id, {"id": run_id, "status": {"state": "canceled"}}))

    return JSONResponse(
        _rpc_err(rpc_id, -32601, f"Method not found: {method!r}"),
        status_code=404,
    )


@router.get("/{team}/runs/{run_id}")
async def task_status(
    team: str,
    run_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse:
    """Poll the status of a previously submitted A2A task by its run_id."""
    return await _get_task(None, run_id, ctx)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_task(rpc_id: Any, run_id: str, ctx: WorkspaceContext) -> JSONResponse:
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine
    from app.models.run import Run

    async with AsyncSession(engine, expire_on_commit=False) as sess:
        stmt = select(Run).where(Run.run_id == run_id)
        if ctx.workspace_id is not None:
            stmt = stmt.where(Run.workspace_id == ctx.workspace_id)
        run = (await sess.exec(stmt)).first()

    if not run:
        return JSONResponse(
            _rpc_err(rpc_id, -32001, f"Task {run_id!r} not found"),
            status_code=404,
        )

    a2a_state = _run_state_to_a2a(run.status)
    artifacts = []
    if run.status == "success" and run.state:
        result_text = _state_to_text(run.state)
        if result_text:
            artifacts.append({
                "name": "result",
                "parts": [{"type": "text", "text": result_text}],
            })

    return JSONResponse(_rpc_ok(rpc_id, {
        "id": run_id,
        "status": {
            "state": a2a_state,
            "timestamp": run.finished_at.isoformat() if run.finished_at else None,
        },
        "artifacts": artifacts,
        "metadata": {
            "antcrew_run_id": run_id,
            "team": run.team,
            "cost_usd": run.cost_usd,
            "duration_s": run.duration_s,
        },
    }))


async def _cancel_run(run_id: str, workspace_id: Optional[int]) -> None:
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine
    from app.models.run import Run

    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            stmt = select(Run).where(Run.run_id == run_id)
            if workspace_id is not None:
                stmt = stmt.where(Run.workspace_id == workspace_id)
            run = (await sess.exec(stmt)).first()
            if run and run.status == "running":
                run.status = "cancelled"
                sess.add(run)
                await sess.commit()
    except Exception as _cancel_exc:
        log.warning("a2a: failed to mark run %s as cancelled: %s", run_id, _cancel_exc)


def _state_to_text(state: dict) -> str:
    """Extract a human-readable result from a team state dict."""
    for key in ("result", "summary", "output", "report", "response"):
        val = state.get(key)
        if isinstance(val, str) and val.strip():
            return val[:4000]
    # Fallback: the request and any top-level string values
    fragments = []
    for k, v in state.items():
        if k.startswith("_") or not isinstance(v, str):
            continue
        fragments.append(f"**{k}**:\n{v[:500]}")
    return "\n\n".join(fragments[:3])
