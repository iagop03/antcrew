"""Admin platform analytics — velocity, main analytics, HITL resolution, churn, capabilities."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, case, extract, func
from sqlalchemy import select as sa_select
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.admin import PlatformConfig
from app.models.auth import User
from app.models.feedback import UserFeedback
from app.models.workspace import Workspace

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/analytics/velocity")
async def admin_velocity(
    window_minutes: int = Query(default=60, ge=5, le=1440),
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Per-workspace spend in the last N minutes, ordered by highest spender."""
    from datetime import timezone

    from app.models.run import Run

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=window_minutes)
    rows = (await session.exec(
        sa_select(
            Run.workspace_id,
            func.sum(Run.cost_usd).label("velocity_usd"),
            func.count(Run.run_id).label("run_count"),
        )
        .where(Run.created_at >= cutoff)
        .where(Run.cost_usd.isnot(None))
        .group_by(Run.workspace_id)
        .having(func.sum(Run.cost_usd) > 0)
        .order_by(func.sum(Run.cost_usd).desc())
    )).all()

    return [
        {
            "workspace_id": r.workspace_id,
            "velocity_usd": round(float(r.velocity_usd), 4),
            "run_count": r.run_count,
            "window_minutes": window_minutes,
        }
        for r in rows
    ]


@router.get("/analytics")
async def admin_analytics(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Time-series platform analytics for the last 12 months."""
    from app.models.run import Run, Ticket

    cutoff = datetime.utcnow() - timedelta(days=365)

    def _by_month(rows):
        return [
            {"period": f"{int(r.yr):04d}-{int(r.mo):02d}", "count": int(r.cnt)}
            for r in rows
        ]

    yr_ws = extract("year", Workspace.created_at)
    mo_ws = extract("month", Workspace.created_at)
    ws_rows = (await session.execute(
        sa_select(yr_ws.label("yr"), mo_ws.label("mo"), func.count().label("cnt"))
        .where(Workspace.created_at >= cutoff)
        .group_by(yr_ws, mo_ws)
        .order_by(yr_ws, mo_ws)
    )).all()

    yr_u = extract("year", User.created_at)
    mo_u = extract("month", User.created_at)
    user_rows = (await session.execute(
        sa_select(yr_u.label("yr"), mo_u.label("mo"), func.count().label("cnt"))
        .where(User.created_at >= cutoff)
        .group_by(yr_u, mo_u)
        .order_by(yr_u, mo_u)
    )).all()

    yr_r = extract("year", Run.created_at)
    mo_r = extract("month", Run.created_at)
    run_rows = (await session.execute(
        sa_select(
            yr_r.label("yr"), mo_r.label("mo"), func.count().label("cnt"),
            func.sum(case((Run.status == "success", 1), else_=0)).label("success"),
        )
        .where(Run.created_at >= cutoff)
        .group_by(yr_r, mo_r)
        .order_by(yr_r, mo_r)
    )).all()

    tickets_by_status_rows = (await session.execute(
        sa_select(Ticket.status, func.count().label("cnt")).group_by(Ticket.status)
    )).all()
    tickets_by_status = {r.status: int(r.cnt) for r in tickets_by_status_rows}

    use_case_rows = (await session.execute(
        sa_select(User.use_case, func.count().label("cnt"))
        .where(User.use_case.isnot(None))
        .group_by(User.use_case)
        .order_by(func.count().desc())
    )).all()

    team_size_rows = (await session.execute(
        sa_select(User.team_size, func.count().label("cnt"))
        .where(User.team_size.isnot(None))
        .group_by(User.team_size)
        .order_by(func.count().desc())
    )).all()

    fb_total = (await session.exec(select(func.count()).select_from(UserFeedback))).one()
    fb_positive = (await session.exec(
        select(func.count()).select_from(UserFeedback).where(UserFeedback.helpful.is_(True))
    )).one()
    fb_negative = (await session.exec(
        select(func.count()).select_from(UserFeedback).where(UserFeedback.helpful.is_(False))
    )).one()

    team_rows = (await session.execute(
        sa_select(
            Run.team,
            func.count().label("runs"),
            func.coalesce(func.sum(Run.cost_usd), 0.0).label("cost"),
        )
        .where(Run.created_at >= cutoff)
        .group_by(Run.team)
        .order_by(func.coalesce(func.sum(Run.cost_usd), 0.0).desc())
    )).all()

    ticket_team_rows = (await session.execute(
        sa_select(Run.team, func.count(Ticket.id).label("tickets"))
        .join(Ticket, Run.run_id == Ticket.run_id)
        .where(Run.created_at >= cutoff)
        .group_by(Run.team)
    )).all()
    tickets_by_team = {r.team: int(r.tickets) for r in ticket_team_rows}

    _effective_mode = func.coalesce(Run.llm_key_mode, Workspace.llm_key_mode).label("effective_mode")
    mode_rows = (await session.execute(
        sa_select(
            _effective_mode,
            func.count(Run.id).label("runs"),
            func.coalesce(func.sum(Run.cost_usd), 0.0).label("cost"),
        )
        .join(Workspace, Run.workspace_id == Workspace.id, isouter=True)
        .where(Run.created_at >= cutoff)
        .group_by(func.coalesce(Run.llm_key_mode, Workspace.llm_key_mode))
        .order_by(func.coalesce(func.sum(Run.cost_usd), 0.0).desc())
    )).all()

    model_rows = (await session.execute(
        sa_select(
            Run.model,
            func.count().label("runs"),
            func.coalesce(func.sum(Run.cost_usd), 0.0).label("cost"),
        )
        .where(Run.model.isnot(None))
        .where(Run.created_at >= cutoff)
        .group_by(Run.model)
        .order_by(func.coalesce(func.sum(Run.cost_usd), 0.0).desc())
    )).all()

    return {
        "workspaces_by_month": _by_month(ws_rows),
        "users_by_month": _by_month(user_rows),
        "runs_by_month": [
            {"period": f"{int(r.yr):04d}-{int(r.mo):02d}", "count": int(r.cnt), "success": int(r.success or 0)}
            for r in run_rows
        ],
        "tickets_by_status": tickets_by_status,
        "use_cases": [{"use_case": r.use_case, "count": int(r.cnt)} for r in use_case_rows],
        "team_sizes": [{"team_size": r.team_size, "count": int(r.cnt)} for r in team_size_rows],
        "feedback": {"total": int(fb_total), "positive": int(fb_positive), "negative": int(fb_negative)},
        "by_team": [
            {"team": r.team, "runs": int(r.runs), "tickets": tickets_by_team.get(r.team, 0), "cost_usd": round(float(r.cost), 4)}
            for r in team_rows
        ],
        "by_llm_mode": [
            {"mode": r.effective_mode, "runs": int(r.runs), "cost_usd": round(float(r.cost), 4)}
            for r in mode_rows
        ],
        "by_model": [
            {"model": r.model, "runs": int(r.runs), "cost_usd": round(float(r.cost), 4)}
            for r in model_rows
        ],
    }


@router.get("/analytics/hitl-resolution")
async def hitl_resolution_stats(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Average HITL review resolution time and count of resolved reviews."""
    from app.models.run import HitlReview as _HitlReview

    result = (await session.execute(
        sa_select(
            func.avg(
                extract("epoch", _HitlReview.resolved_at - _HitlReview.created_at) / 60.0
            ).label("avg_min"),
            func.count().label("resolved_count"),
        )
        .where(_HitlReview.resolved_at.isnot(None))
    )).one()

    return {
        "avg_resolution_min": round(float(result.avg_min or 0), 2),
        "resolved_count": int(result.resolved_count),
    }


@router.get("/analytics/churn")
async def workspace_churn(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    days: int = 30,
):
    """Workspaces that have run at least once but made no runs in the last *days* days."""
    from app.models.run import Run as _ChurnRun

    cutoff = datetime.utcnow() - timedelta(days=days)
    subq = (
        sa_select(
            _ChurnRun.workspace_id,
            func.max(_ChurnRun.created_at).label("last_run"),
        )
        .where(_ChurnRun.workspace_id.isnot(None))
        .group_by(_ChurnRun.workspace_id)
        .subquery()
    )
    rows = (await session.execute(
        sa_select(Workspace.id, Workspace.name, Workspace.slug, subq.c.last_run)
        .join(subq, Workspace.id == subq.c.workspace_id)
        .where(subq.c.last_run < cutoff)
        .order_by(subq.c.last_run.asc())
    )).all()

    now = datetime.utcnow()
    return {
        "window_days": days,
        "count": len(rows),
        "churned_workspaces": [
            {
                "workspace_id": r.id,
                "name": r.name,
                "slug": r.slug,
                "last_run_at": r.last_run.isoformat() if r.last_run else None,
                "days_since_last_run": (now - r.last_run).days if r.last_run else None,
            }
            for r in rows
        ],
    }


@router.get("/analytics/capabilities")
async def capability_analytics(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    days: int = Query(default=30, ge=1, le=365),
):
    """Engine capability usage analytics (PostgreSQL only)."""
    from fastapi import HTTPException
    from sqlalchemy import text as sa_text

    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))
    try:
        rows = (await session.execute(
            sa_text("""
                SELECT
                    payload->>'agent_name'                                          AS capability,
                    COUNT(*) FILTER (WHERE event_type = 'agent.start')              AS dispatched,
                    COUNT(*) FILTER (WHERE event_type = 'agent.end')                AS completed,
                    COUNT(*) FILTER (
                        WHERE event_type = 'agent.end'
                          AND (payload->>'succeeded')::boolean IS TRUE
                    )                                                               AS succeeded,
                    AVG(
                        CASE WHEN event_type = 'agent.end'
                             THEN (payload->>'duration_s')::float END
                    )                                                               AS avg_duration_s,
                    SUM(
                        CASE WHEN event_type = 'agent.end'
                             THEN (payload->>'cost_usd')::float END
                    )                                                               AS total_cost_usd
                FROM event
                WHERE event_type IN ('agent.start', 'agent.end')
                  AND payload->>'agent_name' IS NOT NULL
                  AND recorded_at >= :cutoff
                GROUP BY payload->>'agent_name'
                ORDER BY dispatched DESC
            """),
            {"cutoff": cutoff},
        )).all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="analytics query requires PostgreSQL") from exc

    capabilities = []
    for r in rows:
        dispatched = int(r.dispatched or 0)
        completed  = int(r.completed  or 0)
        succeeded  = int(r.succeeded  or 0)
        capabilities.append({
            "capability":     r.capability,
            "dispatched":     dispatched,
            "completed":      completed,
            "succeeded":      succeeded,
            "success_rate":   round(succeeded / completed, 4) if completed else None,
            "avg_duration_s": round(float(r.avg_duration_s), 2) if r.avg_duration_s is not None else None,
            "total_cost_usd": round(float(r.total_cost_usd), 6) if r.total_cost_usd is not None else 0.0,
        })

    return {"window_days": days, "capabilities": capabilities}
