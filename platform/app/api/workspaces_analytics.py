"""Workspace analytics routes — HITL rejection stats and run/ticket trends."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy import select as sa_select
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
from app.core.exceptions import WorkspaceNotFoundError
from app.models.run import HitlReview, Run, Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{workspace_id}/hitl/analytics",
            dependencies=[Depends(require_role("admin", "write"))])
async def hitl_analytics(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Per-checkpoint and per-resolver HITL rejection analytics for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    from app.models.run import HitlAuditEntry

    rows = (await session.execute(
        sa_select(HitlReview.agent_name, HitlReview.status, HitlReview.assigned_to)
        .join(Run, Run.run_id == HitlReview.run_id)
        .where(Run.workspace_id == workspace_id)
    )).fetchall()

    total = len(rows)
    resolved = [r for r in rows if r.status != "pending"]
    rejected = [r for r in rows if r.status == "rejected"]
    approved = [r for r in rows if r.status == "approved"]

    by_agent: dict[str, dict] = {}
    for r in rows:
        key = r.agent_name or "unknown"
        if key not in by_agent:
            by_agent[key] = {"agent_name": key, "total": 0, "approved": 0, "rejected": 0, "pending": 0}
        by_agent[key]["total"] += 1
        if r.status == "approved":
            by_agent[key]["approved"] += 1
        elif r.status == "rejected":
            by_agent[key]["rejected"] += 1
        elif r.status == "pending":
            by_agent[key]["pending"] += 1

    for v in by_agent.values():
        res = v["approved"] + v["rejected"]
        v["rejection_rate"] = round(v["rejected"] / res, 3) if res else None

    audit_rows = (await session.execute(
        sa_select(HitlAuditEntry.actor_label, func.count(HitlAuditEntry.id).label("count"))
        .join(HitlReview, HitlReview.review_id == HitlAuditEntry.review_id)
        .join(Run, Run.run_id == HitlReview.run_id)
        .where(Run.workspace_id == workspace_id)
        .where(HitlAuditEntry.action.in_(["approved", "rejected"]))
        .group_by(HitlAuditEntry.actor_label)
        .order_by(func.count(HitlAuditEntry.id).desc())
    )).fetchall()

    by_resolver = [{"resolver": r.actor_label or "client", "decisions": r.count} for r in audit_rows]
    overall_rejection_rate = round(len(rejected) / len(resolved), 3) if resolved else None

    return {
        "workspace_id": workspace_id,
        "total_reviews": total,
        "pending": total - len(resolved),
        "approved": len(approved),
        "rejected": len(rejected),
        "overall_rejection_rate": overall_rejection_rate,
        "by_agent": sorted(by_agent.values(), key=lambda x: -(x["rejected"])),
        "by_resolver": by_resolver,
    }


@router.get("/{workspace_id}/analytics",
            dependencies=[Depends(require_role("admin", "write"))])
async def workspace_analytics(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Last-30-day run trend and ticket status distribution for a workspace."""
    from datetime import timedelta, timezone

    from sqlalchemy import case

    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    from app.models.run import AgentEvent, Ticket

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    day_expr = func.date(Run.created_at)

    run_rows = (await session.execute(
        sa_select(
            day_expr.label("day"),
            func.count().label("cnt"),
            func.coalesce(func.sum(Run.cost_usd), 0.0).label("cost"),
            func.sum(case((Run.status == "success", 1), else_=0)).label("success"),
        )
        .where(Run.workspace_id == workspace_id)
        .where(Run.created_at >= cutoff)
        .group_by(day_expr)
        .order_by(day_expr)
    )).all()

    ticket_rows = (await session.execute(
        sa_select(Ticket.status, func.count().label("cnt"))
        .where(Ticket.workspace_id == workspace_id)
        .group_by(Ticket.status)
    )).all()
    tickets_by_status = {r.status: int(r.cnt) for r in ticket_rows}

    team_rows = (await session.execute(
        sa_select(
            Run.team,
            func.count().label("runs"),
            func.coalesce(func.sum(Run.cost_usd), 0.0).label("cost"),
        )
        .where(Run.workspace_id == workspace_id)
        .where(Run.created_at >= cutoff)
        .group_by(Run.team)
        .order_by(func.coalesce(func.sum(Run.cost_usd), 0.0).desc())
    )).all()

    ticket_team_rows = (await session.execute(
        sa_select(Run.team, func.count(Ticket.id).label("tickets"))
        .join(Ticket, Run.run_id == Ticket.run_id)
        .where(Run.workspace_id == workspace_id)
        .where(Run.created_at >= cutoff)
        .group_by(Run.team)
    )).all()
    tickets_by_team = {r.team: int(r.tickets) for r in ticket_team_rows}

    model_rows = (await session.execute(
        sa_select(
            Run.model,
            func.count().label("runs"),
            func.coalesce(func.sum(Run.cost_usd), 0.0).label("cost"),
        )
        .where(Run.workspace_id == workspace_id)
        .where(Run.model.isnot(None))
        .where(Run.created_at >= cutoff)
        .group_by(Run.model)
        .order_by(func.coalesce(func.sum(Run.cost_usd), 0.0).desc())
    )).all()

    agent_rows = (await session.execute(
        sa_select(
            AgentEvent.agent_name,
            func.count().label("invocations"),
            func.coalesce(func.sum(AgentEvent.cost_usd), 0.0).label("cost"),
            func.coalesce(func.sum(AgentEvent.tokens_in), 0).label("tokens_in"),
            func.coalesce(func.sum(AgentEvent.tokens_out), 0).label("tokens_out"),
        )
        .join(Run, Run.run_id == AgentEvent.run_id)
        .where(Run.workspace_id == workspace_id)
        .where(Run.created_at >= cutoff)
        .group_by(AgentEvent.agent_name)
        .order_by(func.coalesce(func.sum(AgentEvent.cost_usd), 0.0).desc())
    )).all()

    return {
        "workspace_id": workspace_id,
        "runs_by_day": [
            {
                "date": str(r.day),
                "count": int(r.cnt),
                "success": int(r.success or 0),
                "failed": int(r.cnt) - int(r.success or 0),
                "cost": round(float(r.cost or 0), 4),
            }
            for r in run_rows
        ],
        "tickets_by_status": tickets_by_status,
        "total_cost_usd": round(float(ws.total_cost_usd) if ws else 0.0, 4),
        "by_team": [
            {
                "team": r.team,
                "runs": int(r.runs),
                "tickets": tickets_by_team.get(r.team, 0),
                "cost_usd": round(float(r.cost), 4),
            }
            for r in team_rows
        ],
        "by_model": [
            {
                "model": r.model,
                "runs": int(r.runs),
                "cost_usd": round(float(r.cost), 4),
            }
            for r in model_rows
        ],
        "by_agent": [
            {
                "agent_name": r.agent_name,
                "invocations": int(r.invocations),
                "tokens_in": int(r.tokens_in),
                "tokens_out": int(r.tokens_out),
                "cost_usd": round(float(r.cost), 6),
            }
            for r in agent_rows
        ],
    }
