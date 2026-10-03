"""Platform admin API — bootstrap, stats, and workspace management.

Campaigns → admin_campaigns.py
Billing rates → admin_billing.py
Analytics → admin_analytics.py
Users, feedback, GDPR, BYOK → admin_users.py
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.auth import User
from app.models.workspace import Workspace

router = APIRouter(prefix="/admin", tags=["admin"])


class WorkspacePatch(BaseModel):
    cost_multiplier_override: Optional[float] = None
    multiplier_locked: Optional[bool] = None
    is_trial: Optional[bool] = None
    max_cost_usd: Optional[float] = None
    is_blocked: Optional[bool] = None
    compliance_pack_enabled: Optional[bool] = None
    compliance_pack_price_monthly: Optional[float] = None
    compliance_pack_price_annual: Optional[float] = None
    cost_routing_policy: Optional[str] = None


class WorkspaceRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    slug: str
    llm_key_mode: str
    is_trial: bool
    cost_multiplier_override: Optional[float]
    multiplier_locked: bool
    total_cost_usd: float
    max_cost_usd: Optional[float]
    subscription_status: Optional[str]
    is_blocked: bool
    compliance_pack_enabled: bool = False
    compliance_pack_price_monthly: Optional[float] = None
    compliance_pack_price_annual: Optional[float] = None
    cost_routing_policy: str = "none"
    created_at: datetime


class MakeAdminRequest(BaseModel):
    email: str
    token: str


@router.post("/make-admin")
async def make_admin(
    body: MakeAdminRequest,
    session=Depends(get_session),
):
    """Grant is_platform_admin to a user. Protected by PLATFORM_ADMIN_TOKEN env var."""
    from app.api.admin_users import AdminUserRow
    expected = os.environ.get("PLATFORM_ADMIN_TOKEN", "")
    if not expected or body.token != expected:
        raise HTTPException(403, "Invalid PLATFORM_ADMIN_TOKEN")
    user: Optional[User] = (await session.exec(
        select(User).where(User.email == body.email)
    )).first()
    if user is None:
        raise HTTPException(404, f"No user with email {body.email!r}")
    user.is_platform_admin = True
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return AdminUserRow.model_validate(user)


@router.get("/stats")
async def admin_stats(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from sqlalchemy import func

    from app.models.run import Run

    total_workspaces = (await session.exec(
        select(func.count()).select_from(Workspace)
    )).one()
    trial_workspaces = (await session.exec(
        select(func.count()).select_from(Workspace).where(Workspace.is_trial.is_(True))
    )).one()
    paid_workspaces = (await session.exec(
        select(func.count()).select_from(Workspace).where(Workspace.is_trial.is_(False))
    )).one()
    total_runs = (await session.exec(
        select(func.count()).select_from(Run)
    )).one()
    total_cost = (await session.exec(
        select(func.coalesce(func.sum(Workspace.total_cost_usd), 0.0))
    )).one()

    return {
        "workspaces": {"total": total_workspaces, "trial": trial_workspaces, "paid": paid_workspaces},
        "runs": {"total": total_runs},
        "revenue": {"total_cost_usd": float(total_cost)},
    }


@router.get("/workspaces", response_model=list[WorkspaceRow])
async def list_workspaces(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    limit: int = 100,
    offset: int = 0,
):
    rows = (await session.exec(
        select(Workspace).order_by(Workspace.created_at.desc()).offset(offset).limit(limit)
    )).all()
    return rows


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRow)
async def patch_workspace(
    workspace_id: int,
    body: WorkspacePatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    ws: Optional[Workspace] = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")

    if body.cost_multiplier_override is not None:
        ws.cost_multiplier_override = body.cost_multiplier_override
    if body.multiplier_locked is not None:
        ws.multiplier_locked = body.multiplier_locked
    if body.is_trial is not None:
        ws.is_trial = body.is_trial
    if body.max_cost_usd is not None:
        ws.max_cost_usd = body.max_cost_usd
    if body.is_blocked is not None:
        ws.is_blocked = body.is_blocked
    if body.compliance_pack_enabled is not None:
        ws.compliance_pack_enabled = body.compliance_pack_enabled

    if "cost_multiplier_override" in (body.model_fields_set or set()) and body.cost_multiplier_override is None:
        ws.cost_multiplier_override = None
    if "compliance_pack_price_monthly" in (body.model_fields_set or set()):
        ws.compliance_pack_price_monthly = body.compliance_pack_price_monthly
    if "compliance_pack_price_annual" in (body.model_fields_set or set()):
        ws.compliance_pack_price_annual = body.compliance_pack_price_annual
    if body.cost_routing_policy is not None:
        if body.cost_routing_policy not in ("none", "economy", "auto"):
            raise HTTPException(422, "cost_routing_policy must be 'none', 'economy', or 'auto'")
        ws.cost_routing_policy = body.cost_routing_policy

    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.delete("/workspaces/{workspace_id}", status_code=200)
async def delete_workspace(
    workspace_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """GDPR Art. 17 / account closure — delete all workspace data. Irreversible."""
    from sqlalchemy import text

    ws: Optional[Workspace] = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")

    p = {"ws": workspace_id}
    counts: dict[str, int] = {}

    ordered = [
        # FK children of run — delete before run
        ("agent_events",    "DELETE FROM agent_event     WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = :ws)"),
        ("events",          "DELETE FROM event            WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = :ws)"),
        ("tickets",         "DELETE FROM ticket           WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = :ws)"),
        ("hitl_reviews",    "DELETE FROM hitl_review      WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = :ws)"),
        ("webhook_deliveries", "DELETE FROM webhook_delivery WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = :ws)"),
        # Direct workspace children
        ("runs",            "DELETE FROM run              WHERE workspace_id = :ws"),
        ("api_keys",        "DELETE FROM api_key          WHERE workspace_id = :ws"),
        ("memberships",     "DELETE FROM workspace_membership WHERE workspace_id = :ws"),
        ("discovery_sessions", "DELETE FROM discovery_session WHERE workspace_id = :ws"),
        ("webhook_configs", "DELETE FROM webhook_config   WHERE workspace_id = :ws"),
        # Workspace row
        ("workspace",       "DELETE FROM workspace        WHERE id = :ws"),
    ]

    for label, sql in ordered:
        result = await session.execute(text(sql), p)
        counts[label] = result.rowcount

    await session.commit()

    from datetime import timezone
    return {
        "workspace_id": workspace_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "rows_deleted": counts,
    }
