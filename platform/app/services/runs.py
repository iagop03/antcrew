"""Run persistence and query helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import case, func, or_, text
from sqlalchemy import select as sa_select
from sqlmodel import desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import Event as DBEvent
from app.models.run import HitlReview, Run, Ticket


async def _claim_display_id(session: AsyncSession, workspace_id: int) -> str:
    """Atomically increment workspace ticket_counter and return the display_id string."""
    result = await session.execute(
        text(
            "UPDATE workspace SET ticket_counter = ticket_counter + 1 "
            "WHERE id = :wid RETURNING ticket_prefix, ticket_counter"
        ),
        {"wid": workspace_id},
    )
    row = result.first()
    if not row:
        raise ValueError(f"workspace {workspace_id} not found")
    return f"{row.ticket_prefix}-{row.ticket_counter:05d}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def list_runs(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    team: Optional[str] = None,
    team_prefix: Optional[str] = None,
    status: Optional[str] = None,
    since_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    workspace_ids: Optional[list[int]] = None,
    client_label: Optional[str] = None,
) -> list[Run]:
    stmt = select(Run).order_by(desc(Run.id)).limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    if since_id is not None:
        stmt = stmt.where(Run.id < since_id)
    if team:
        stmt = stmt.where(Run.team == team)
    elif team_prefix:
        stmt = stmt.where(Run.team.startswith(team_prefix))
    if status:
        stmt = stmt.where(Run.status == status)
    if workspace_ids is not None:
        stmt = stmt.where(Run.workspace_id.in_(workspace_ids)) if len(workspace_ids) != 1 else stmt.where(Run.workspace_id == workspace_ids[0])
    elif workspace_id is not None:
        stmt = stmt.where(Run.workspace_id == workspace_id)
    if client_label is not None:
        stmt = stmt.where(Run.client_label == client_label)
    result = await session.exec(stmt)
    return list(result.all())


async def get_run(session: AsyncSession, run_id: str) -> Optional[Run]:
    result = await session.exec(select(Run).where(Run.run_id == run_id))
    return result.first()


async def get_run_events(session: AsyncSession, run_id: str) -> list[DBEvent]:
    result = await session.exec(
        select(DBEvent)
        .where(DBEvent.run_id == run_id)
        .order_by(DBEvent.timestamp)
    )
    return list(result.all())


async def get_run_stats(session: AsyncSession, workspace_id: Optional[int] = None, workspace_ids: Optional[list[int]] = None) -> dict:
    """Return aggregate counts, cost and monthly quota. Uses SQL aggregates — O(1) regardless of table size."""
    _month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt = sa_select(
        func.count(Run.id).label("total"),
        func.sum(case((Run.status == "running", 1), else_=0)).label("running"),
        func.sum(case((Run.status == "success", 1), else_=0)).label("success"),
        func.sum(case((Run.status == "error", 1), else_=0)).label("error"),
        func.sum(case((Run.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.coalesce(func.sum(Run.cost_usd), 0.0).label("total_cost_usd"),
        func.avg(Run.duration_s).label("avg_duration_s"),
        func.sum(case((Run.created_at >= _month_start, 1), else_=0)).label("runs_this_month"),
    ).select_from(Run)
    if workspace_ids is not None:
        stmt = stmt.where(Run.workspace_id.in_(workspace_ids)) if len(workspace_ids) != 1 else stmt.where(Run.workspace_id == workspace_ids[0])
    elif workspace_id is not None:
        stmt = stmt.where(Run.workspace_id == workspace_id)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = await session.execute(stmt)
    row = result.one()

    from app.core.license import get_license
    lic = get_license()

    return {
        "total": row.total or 0,
        "running": row.running or 0,
        "success": row.success or 0,
        "error": row.error or 0,
        "cancelled": row.cancelled or 0,
        "total_cost_usd": round(float(row.total_cost_usd or 0), 6),
        "avg_duration_s": round(float(row.avg_duration_s), 2) if row.avg_duration_s else None,
        "runs_this_month": int(row.runs_this_month or 0),
        "run_limit": lic.max_runs_per_month,
    }


async def cancel_run(session: AsyncSession, run_id: str) -> Optional[Run]:
    """Mark a running run as cancelled and resolve any pending HITL reviews with reject.

    The executor thread eventually terminates: HITL agents receive a reject decision
    and the pipeline finishes with an error. Non-HITL runs drain naturally.
    """
    from app.core.channel import resolve_review
    from app.models.run import HitlReview

    result = await session.exec(select(Run).where(Run.run_id == run_id))
    run = result.first()
    if not run or run.status != "running":
        return None
    run.status = "cancelled"
    run.finished_at = _utcnow()
    if run.created_at:
        ca = run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=timezone.utc)
        run.duration_s = (run.finished_at - ca).total_seconds()
    session.add(run)

    # Resolve pending HITL reviews so the executor thread can unblock and exit
    pending = await session.exec(
        select(HitlReview).where(
            HitlReview.run_id == run_id,
            HitlReview.status == "pending",
        )
    )
    for review in pending.all():
        resolve_review(review.review_id, {"decision": "reject", "cancelled": True})
        review.status = "cancelled"
        session.add(review)

    await session.commit()
    await session.refresh(run)
    return run


def _extract_ticket_fields(t) -> dict:
    if isinstance(t, dict):
        return {
            "tid": t.get("id", ""),
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "priority": t.get("priority", "medium"),
            "status": t.get("status", "open"),
            "ac": t.get("acceptance_criteria", ""),
            "deps": t.get("dependencies", []),
        }
    return {
        "tid": getattr(t, "id", ""),
        "title": getattr(t, "title", ""),
        "description": getattr(t, "description", ""),
        "priority": str(getattr(t, "priority", "medium")),
        "status": str(getattr(t, "status", "open")),
        "ac": getattr(t, "acceptance_criteria", ""),
        "deps": getattr(t, "dependencies", []),
    }


async def upsert_tickets_from_run(
    session: AsyncSession,
    run_id: str,
    run_state: dict,
    workspace_id: Optional[int] = None,
) -> int:
    """Extract tickets from a RunResult state and upsert by ticket_id.

    Uses a single batch SELECT to avoid N+1 queries — one query per run
    regardless of ticket count.
    """
    raw_tickets = run_state.get("tickets") or []
    prd = run_state.get("prd") or {}
    prd_title = (prd.get("title") or "") if isinstance(prd, dict) else getattr(prd, "title", "")

    # Parse all tickets and skip empties up front
    parsed = [_extract_ticket_fields(t) for t in raw_tickets]
    parsed = [p for p in parsed if p["tid"]]
    if not parsed:
        return 0

    # Single batch SELECT for all ticket_ids in this run
    ticket_ids = [p["tid"] for p in parsed]
    title_to_id = {p["title"].strip().lower(): p["tid"] for p in parsed}
    existing_rows = await session.exec(
        select(Ticket).where(Ticket.ticket_id.in_(ticket_ids))
    )
    existing_map: dict[str, Ticket] = {t.ticket_id: t for t in existing_rows.all()}

    for p in parsed:
        tid = p["tid"]
        ac_str = p["ac"] if isinstance(p["ac"], str) else json.dumps(p["ac"])
        deps = p["deps"]
        deps_str = json.dumps(deps) if isinstance(deps, list) else (deps or "")

        # Resolve depends_on: PM emits titles; map to stable IDs within this batch.
        # Falls back to ID-based match for compatibility with future format changes.
        raw_deps = deps if isinstance(deps, list) else (json.loads(deps) if deps else [])
        batch_deps = [
            title_to_id[d.strip().lower()]
            for d in raw_deps
            if isinstance(d, str)
            and d.strip().lower() in title_to_id
            and title_to_id[d.strip().lower()] != tid
        ] or [d for d in raw_deps if d and d in ticket_ids and d != tid]

        existing = existing_map.get(tid)
        if existing:
            existing.title = p["title"]
            existing.description = p["description"]
            existing.priority = p["priority"]
            existing.status = p["status"]
            existing.run_id = run_id
            existing.acceptance_criteria = ac_str
            existing.dependencies = deps_str
            if batch_deps:  # only overwrite if PM provided deps; preserve manual deps otherwise
                existing.depends_on = batch_deps
            existing.updated_at = _utcnow()
            session.add(existing)
        else:
            display_id: Optional[str] = None
            if workspace_id is not None:
                display_id = await _claim_display_id(session, workspace_id)
            session.add(Ticket(
                ticket_id=tid, run_id=run_id,
                title=p["title"], description=p["description"],
                priority=p["priority"], status=p["status"],
                prd_title=prd_title,
                acceptance_criteria=ac_str,
                dependencies=deps_str,
                depends_on=batch_deps or None,
                workspace_id=workspace_id,
                display_id=display_id,
            ))

    return len(parsed)


async def get_run_tickets(session: AsyncSession, run_id: str) -> list[Ticket]:
    result = await session.exec(
        select(Ticket).where(Ticket.run_id == run_id).order_by(Ticket.created_at)
    )
    return list(result.all())


async def list_reviews(
    session: AsyncSession,
    *,
    status: Optional[str] = "pending",
    run_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    workspace_id: Optional[int] = None,
    workspace_ids: Optional[list[int]] = None,
    assignee_label: Optional[str] = None,
) -> list[HitlReview]:
    from app.models.run import HitlReviewAssignee
    stmt = select(HitlReview).order_by(desc(HitlReview.created_at)).limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    if status:
        stmt = stmt.where(HitlReview.status == status)
    if run_id:
        stmt = stmt.where(HitlReview.run_id == run_id)
    if workspace_ids is not None:
        subq = sa_select(Run.run_id).where(Run.workspace_id.in_(workspace_ids)) if len(workspace_ids) != 1 else sa_select(Run.run_id).where(Run.workspace_id == workspace_ids[0])
        stmt = stmt.where(HitlReview.run_id.in_(subq))
    elif workspace_id is not None:
        stmt = stmt.where(
            HitlReview.run_id.in_(
                sa_select(Run.run_id).where(Run.workspace_id == workspace_id)
            )
        )
    if assignee_label is not None:
        subq_a = sa_select(HitlReviewAssignee.review_id).where(
            HitlReviewAssignee.assignee_label == assignee_label
        )
        stmt = stmt.where(HitlReview.review_id.in_(subq_a))
    result = await session.exec(stmt)
    return list(result.all())


async def list_tickets(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    workspace_id: Optional[int] = None,
    workspace_ids: Optional[list[int]] = None,
) -> list[Ticket]:
    stmt = select(Ticket).order_by(desc(Ticket.updated_at)).limit(limit).offset(offset)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if run_id:
        stmt = stmt.where(Ticket.run_id == run_id)
    if workspace_ids is not None:
        subq = sa_select(Run.run_id).where(Run.workspace_id.in_(workspace_ids)) if len(workspace_ids) != 1 else sa_select(Run.run_id).where(Run.workspace_id == workspace_ids[0])
        stmt = stmt.where(Ticket.run_id.in_(subq))
    elif workspace_id is not None:
        stmt = stmt.where(
            Ticket.run_id.in_(
                sa_select(Run.run_id).where(Run.workspace_id == workspace_id)
            )
        )
    if search:
        q = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Ticket.title).like(q),
                func.lower(Ticket.description).like(q),
                func.lower(Ticket.prd_title).like(q),
                func.lower(Ticket.ticket_id).like(q),
            )
        )
    result = await session.exec(stmt)
    return list(result.all())
