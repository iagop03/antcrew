"""Sprint management endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.models.run import Sprint, Ticket

router = APIRouter(prefix="/sprints", tags=["sprints"], dependencies=[Depends(require_api_key)])

_VALID_STATUSES = ("planning", "active", "done")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SprintCreate(BaseModel):
    name: str
    status: str = "planning"
    default_team: Optional[str] = None  # team used when dispatching tickets; inherits workspace default if None


class SprintUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    backlog_order: Optional[int] = None
    default_team: Optional[str] = None  # null clears the override (falls back to workspace default)


@router.post("/", status_code=201, response_model=Sprint, dependencies=[Depends(require_role("admin", "write"))])
async def create_sprint(
    body: SprintCreate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Sprint:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")

    workspace_id = ctx.workspace_ids[0] if ctx.workspace_ids else None

    # Set backlog_order to one beyond the current max for this workspace
    existing = (await session.exec(
        select(Sprint).where(Sprint.workspace_id == workspace_id).order_by(Sprint.backlog_order.desc())  # type: ignore[arg-type]
    )).first()
    next_order = (existing.backlog_order + 1) if existing else 0

    sprint = Sprint(
        sprint_id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        name=body.name,
        status=body.status,
        backlog_order=next_order,
        default_team=body.default_team,
        created_at=_utcnow(),
    )
    session.add(sprint)
    await session.commit()
    await session.refresh(sprint)
    return sprint


@router.get("/", response_model=list[Sprint])
async def list_sprints(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[Sprint]:
    workspace_id = ctx.workspace_ids[0] if ctx.workspace_ids else None
    q = select(Sprint)
    if workspace_id is not None:
        q = q.where(Sprint.workspace_id == workspace_id)
    if status:
        q = q.where(Sprint.status == status)
    q = q.order_by(Sprint.backlog_order).limit(limit).offset(offset)
    results = await session.exec(q)
    return list(results.all())


@router.get("/{sprint_id_param}", response_model=Sprint)
async def get_sprint(
    sprint_id_param: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Sprint:
    sprint = (await session.exec(select(Sprint).where(Sprint.sprint_id == sprint_id_param))).first()
    if not sprint:
        raise HTTPException(404, f"Sprint {sprint_id_param!r} not found")
    return sprint


@router.get("/{sprint_id_param}/tickets", response_model=list[Ticket])
async def get_sprint_tickets(
    sprint_id_param: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[Ticket]:
    sprint = (await session.exec(select(Sprint).where(Sprint.sprint_id == sprint_id_param))).first()
    if not sprint:
        raise HTTPException(404, f"Sprint {sprint_id_param!r} not found")
    q = select(Ticket).where(Ticket.sprint_id == sprint_id_param).order_by(Ticket.backlog_order)
    results = await session.exec(q)
    return list(results.all())


@router.patch("/{sprint_id_param}", response_model=Sprint, dependencies=[Depends(require_role("admin", "write"))])
async def update_sprint(
    sprint_id_param: str,
    body: SprintUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Sprint:
    sprint = (await session.exec(select(Sprint).where(Sprint.sprint_id == sprint_id_param))).first()
    if not sprint:
        raise HTTPException(404, f"Sprint {sprint_id_param!r} not found")
    if body.name is not None:
        sprint.name = body.name
    if body.status is not None:
        if body.status not in _VALID_STATUSES:
            raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")
        sprint.status = body.status
    if body.backlog_order is not None:
        sprint.backlog_order = body.backlog_order
    if "default_team" in body.model_fields_set:
        sprint.default_team = body.default_team  # None clears the override
    session.add(sprint)
    await session.commit()
    await session.refresh(sprint)
    return sprint


class SprintRunResult(BaseModel):
    sprint_id: str
    team: str
    dispatched: list[dict]   # [{ticket_id, run_id, title}]
    waiting: list[str]       # ticket_ids with unfinished deps
    already_done: list[str]  # ticket_ids already done
    blocked: list[str]       # ticket_ids whose deps failed


@router.post("/{sprint_id_param}/run", response_model=SprintRunResult,
             dependencies=[Depends(require_role("admin", "write"))])
async def run_sprint(
    sprint_id_param: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> SprintRunResult:
    """Dispatch the next wave of ready tickets in a sprint (DAG order).

    Tickets with no unfinished dependencies are dispatched in parallel.
    Call again after each wave completes to advance to the next wave.
    Returns which tickets were dispatched, which are waiting, and which are already done.
    """
    import asyncio as _asyncio

    from app.models.run import Ticket, Workspace
    from app.services.runner import AVAILABLE_TEAMS, dispatch

    sprint = (await session.exec(select(Sprint).where(Sprint.sprint_id == sprint_id_param))).first()
    if not sprint:
        raise HTTPException(404, f"Sprint {sprint_id_param!r} not found")

    # Resolve team: sprint.default_team → workspace.default_team → "FullStackTeam"
    team = sprint.default_team
    if not team and sprint.workspace_id is not None:
        ws = (await session.exec(select(Workspace).where(Workspace.id == sprint.workspace_id))).first()
        if ws:
            team = ws.default_team
    team = team or "FullStackTeam"

    if team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Resolved team {team!r} is not available. Available: {AVAILABLE_TEAMS}")

    tickets = list((await session.exec(
        select(Ticket).where(Ticket.sprint_id == sprint_id_param).order_by(Ticket.backlog_order)
    )).all())

    all_ids = {t.ticket_id for t in tickets}

    # Classify each ticket
    done_ids = {t.ticket_id for t in tickets if t.status == "done"}
    failed_ids = {t.ticket_id for t in tickets if t.status == "blocked"}

    ready, waiting, already_done, blocked_out = [], [], list(done_ids), []

    for t in tickets:
        if t.ticket_id in done_ids:
            continue
        deps = set(t.depends_on or []) & all_ids
        failed_deps = deps & failed_ids
        unfinished_deps = deps - done_ids - failed_ids
        if failed_deps:
            blocked_out.append(t.ticket_id)
        elif unfinished_deps:
            waiting.append(t.ticket_id)
        else:
            ready.append(t)

    if not ready:
        return SprintRunResult(
            sprint_id=sprint_id_param, team=team,
            dispatched=[], waiting=waiting, already_done=already_done, blocked=blocked_out,
        )

    # Build a map of ticket_id → implementing_run_id for context passing
    impl_map = {t.ticket_id: t.implementing_run_id for t in tickets if t.implementing_run_id}

    async def _dispatch_one(ticket: Ticket):
        # Pass the implementing run of the last direct dependency as replay context
        dep_run_id: Optional[str] = None
        if ticket.depends_on:
            for dep_id in reversed(ticket.depends_on):
                if dep_id in impl_map:
                    dep_run_id = impl_map[dep_id]
                    break

        request = ticket.title
        if ticket.description:
            request += f"\n\n{ticket.description}"
        if ticket.acceptance_criteria:
            request += f"\n\nAcceptance criteria:\n{ticket.acceptance_criteria}"

        run_id = await dispatch(
            team, request,
            workspace_id=sprint.workspace_id,
            created_by=ctx.created_by or f"sprint:{sprint_id_param}",
            client_label=f"sprint:{sprint_id_param}:ticket:{ticket.ticket_id}",
            replay_run_id=dep_run_id,
        )

        # Store implementing_run_id on the ticket
        ticket.implementing_run_id = run_id
        session.add(ticket)

        return {"ticket_id": ticket.ticket_id, "run_id": run_id, "title": ticket.title}

    results = await _asyncio.gather(*[_dispatch_one(t) for t in ready], return_exceptions=True)

    dispatched = []
    for item in results:
        if isinstance(item, Exception):
            continue
        dispatched.append(item)

    await session.commit()  # persist implementing_run_id on dispatched tickets

    return SprintRunResult(
        sprint_id=sprint_id_param,
        team=team,
        dispatched=dispatched,
        waiting=waiting,
        already_done=already_done,
        blocked=blocked_out,
    )
