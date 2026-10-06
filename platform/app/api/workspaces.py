"""Workspace CRUD — isolated project scopes for multi-team deployments.

Slack integration    → workspaces_slack.py
Webhook config       → workspaces_webhooks.py
Trial & billing      → workspaces_billing.py
Analytics            → workspaces_analytics.py
Presets, KB, routing → workspaces_config.py
"""
from __future__ import annotations

import re as _re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
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
    ws_filter,
)
from app.core.database import get_session
from app.core.exceptions import WorkspaceNotFoundError
from app.core.license_gate import check_workspace_limit
from app.models.run import HitlReview, Run, Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

_REPO_URL_RE = _re.compile(
    r"^(https?://[\w.\-]+/[\w.\-/]+|git@[\w.\-]+:[\w.\-/]+)(\.git)?$"
)


class CreateWorkspace(BaseModel):
    name: str
    slug: str
    max_cost_usd: Optional[float] = None
    default_repo_url: Optional[str] = None
    hitl_default: bool = False

    @field_validator("slug")
    @classmethod
    def slug_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if not _re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must be lowercase alphanumeric with hyphens only")
        return v

    @field_validator("default_repo_url")
    @classmethod
    def repo_url_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _REPO_URL_RE.match(v):
            raise ValueError("default_repo_url must be an HTTPS or SSH git URL")
        return v


class UpdateWorkspaceName(BaseModel):
    name: str
    slug: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("slug")
    @classmethod
    def slug_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not _re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must be lowercase alphanumeric with hyphens only")
        return v


class UpdateBudget(BaseModel):
    max_cost_usd: Optional[float] = None


class UpdateDefaultRepo(BaseModel):
    default_repo_url: Optional[str] = None

    @field_validator("default_repo_url")
    @classmethod
    def repo_url_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _REPO_URL_RE.match(v):
            raise ValueError("default_repo_url must be an HTTPS or SSH git URL")
        return v


class UpdateTicketConfig(BaseModel):
    ticket_prefix: str

    @field_validator("ticket_prefix")
    @classmethod
    def prefix_valid(cls, v: str) -> str:
        v = v.strip().upper()
        if not _re.match(r"^[A-Z0-9]{1,8}$", v):
            raise ValueError("ticket_prefix must be 1–8 uppercase letters/digits (e.g. PROJ)")
        return v


class UpdateHitlDefault(BaseModel):
    hitl_default: bool


class UpdateHitlTimeout(BaseModel):
    hitl_timeout_s: Optional[float] = None


class UpdateAgentModels(BaseModel):
    """Map of agent-name → model string for workspace-level LLM defaults.

    Use the key "default" to set the fallback model for all agents not explicitly listed.
    Set to null to clear all overrides and fall back to the global platform default.
    """
    agent_models: Optional[dict] = None


class WorkspacePublic(BaseModel):
    """Workspace response that never exposes encrypted token fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    max_cost_usd: Optional[float] = None
    budget_exceeded: bool
    total_cost_usd: float
    default_repo_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    slack_channel_id: Optional[str] = None
    slack_bot_configured: bool = False
    slack_app_configured: bool = False
    hitl_default: bool
    hitl_timeout_s: Optional[float] = None
    stripe_customer_id: Optional[str] = None
    subscription_status: Optional[str] = None
    billing_provider: str = "mor"
    llm_key_mode: str = "managed"
    byok_managed_fallback: bool = False
    is_trial: bool = True
    byok_providers: list[str] = []
    ticket_prefix: str = "TKT"
    agent_models: Optional[dict] = None
    max_messages: Optional[int] = None
    default_team: Optional[str] = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _from_workspace(cls, data: object) -> object:
        if hasattr(data, "slack_bot_token_enc"):
            return {
                "id": data.id,
                "name": data.name,
                "slug": data.slug,
                "max_cost_usd": data.max_cost_usd,
                "budget_exceeded": (
                    data.max_cost_usd is not None
                    and data.total_cost_usd >= data.max_cost_usd
                ),
                "total_cost_usd": data.total_cost_usd,
                "default_repo_url": data.default_repo_url,
                "slack_webhook_url": data.slack_webhook_url,
                "slack_channel_id": data.slack_channel_id,
                "slack_bot_configured": bool(data.slack_bot_token_enc),
                "slack_app_configured": bool(data.slack_app_token_enc),
                "hitl_default": data.hitl_default,
                "hitl_timeout_s": data.hitl_timeout_s,
                "stripe_customer_id": getattr(data, "stripe_customer_id", None),
                "subscription_status": getattr(data, "subscription_status", None),
                "billing_provider": getattr(data, "billing_provider", "mor"),
                "llm_key_mode": getattr(data, "llm_key_mode", "managed"),
                "byok_managed_fallback": getattr(data, "byok_managed_fallback", False),
                "is_trial": getattr(data, "is_trial", True),
                "byok_providers": getattr(data, "_byok_providers", []),
                "ticket_prefix": getattr(data, "ticket_prefix", "TKT"),
                "agent_models": getattr(data, "agent_models", None),
                "max_messages": getattr(data, "max_messages", None),
                "created_at": data.created_at,
            }
        return data


@router.get("/", response_model=list[WorkspacePublic])
async def list_workspaces(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = ws_filter(select(Workspace), Workspace.id, ctx)
    result = await session.exec(stmt)
    return list(result.all())


@router.post("/", status_code=201, response_model=WorkspacePublic,
             dependencies=[Depends(require_role("admin"))])
async def create_workspace(body: CreateWorkspace, session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(Workspace).where(Workspace.slug == body.slug))
    if result.first():
        raise HTTPException(409, f"Workspace with slug {body.slug!r} already exists")
    ws_count = (await session.exec(sa_select(func.count()).select_from(Workspace))).scalar() or 0
    check_workspace_limit(ws_count)
    from app.core.byok import TRIAL_CREDIT_USD
    ws = Workspace(
        name=body.name,
        slug=body.slug,
        max_cost_usd=body.max_cost_usd if body.max_cost_usd is not None else TRIAL_CREDIT_USD,
        default_repo_url=body.default_repo_url,
        hitl_default=body.hitl_default,
        is_trial=True,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}", response_model=WorkspacePublic)
async def get_workspace(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    return ws


@router.patch("/{workspace_id}/name", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def update_workspace_name(
    workspace_id: int,
    body: UpdateWorkspaceName,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Update a workspace's display name and optionally its slug."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.name = body.name
    if body.slug is not None:
        existing = await session.exec(
            select(Workspace).where(Workspace.slug == body.slug, Workspace.id != workspace_id)
        )
        if existing.first():
            raise HTTPException(409, f"Slug {body.slug!r} already taken")
        ws.slug = body.slug
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/budget", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_budget(
    workspace_id: int,
    body: UpdateBudget,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the spending limit for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.max_cost_usd = body.max_cost_usd
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}/spend")
async def workspace_spend(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return total spend, budget status, and per-client breakdown for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        agg_rows = (await session.execute(
            sa_select(
                Run.client_label,
                func.count(Run.id).label("run_count"),
                func.coalesce(func.sum(Run.cost_usd), 0).label("spend_usd"),
            )
            .where(Run.workspace_id == workspace_id)
            .group_by(Run.client_label)
        )).fetchall()

    total_spend = round(ws.total_cost_usd, 6)
    budget = ws.max_cost_usd
    exhausted = budget is not None and total_spend >= budget

    by_client = [
        {
            "client_label": row.client_label,
            "run_count": row.run_count,
            "spend_usd": round(float(row.spend_usd), 6),
        }
        for row in sorted(agg_rows, key=lambda r: -(r.spend_usd or 0))
    ]

    return {
        "workspace_id": workspace_id,
        "slug": ws.slug,
        "total_spend_usd": total_spend,
        "budget_usd": budget,
        "remaining_usd": round(budget - total_spend, 6) if budget is not None else None,
        "exhausted": exhausted,
        "run_count": sum(r["run_count"] for r in by_client),
        "by_client": by_client,
    }


@router.patch("/{workspace_id}/repo", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_default_repo(
    workspace_id: int,
    body: UpdateDefaultRepo,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the default repo URL for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.default_repo_url = body.default_repo_url
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/ticket-config", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_ticket_config(
    workspace_id: int,
    body: UpdateTicketConfig,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set the ticket ID prefix for this workspace (e.g. 'PROJ' → PROJ-00001)."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.ticket_prefix = body.ticket_prefix
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/hitl", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_hitl_default(
    workspace_id: int,
    body: UpdateHitlDefault,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Enable or disable HITL by default for all runs in this workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.hitl_default = body.hitl_default
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/hitl-timeout", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_hitl_timeout(
    workspace_id: int,
    body: UpdateHitlTimeout,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the per-workspace HITL review timeout."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    if body.hitl_timeout_s is not None and body.hitl_timeout_s <= 0:
        raise HTTPException(422, "hitl_timeout_s must be positive")
    ws.hitl_timeout_s = body.hitl_timeout_s
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/agent-models", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_agent_models(
    workspace_id: int,
    body: UpdateAgentModels,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set per-agent model defaults for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.agent_models = body.agent_models
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


class UpdateMaxMessages(BaseModel):
    max_messages: Optional[int] = None


class UpdateDefaultTeam(BaseModel):
    default_team: Optional[str] = None  # null clears the default


@router.patch("/{workspace_id}/max-messages", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_max_messages(
    workspace_id: int,
    body: UpdateMaxMessages,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set the workspace-level TeamState message-count limit.

    Runs in this workspace trim their message history to at most *max_messages*
    entries. Team-preset entries (PATCH /workspaces/{id}/presets/{pid}) can
    override this per team type; the preset value takes precedence when set.
    Pass null to remove the limit.
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    if body.max_messages is not None and body.max_messages < 1:
        raise HTTPException(422, "max_messages must be a positive integer or null")
    ws.max_messages = body.max_messages
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}/reviews", response_model=list[HitlReview])
async def workspace_reviews(
    workspace_id: int,
    status: str = Query("pending", description="Filter by status"),
    limit: int = Query(50, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """List HITL reviews for all runs in a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    if not result.first():
        raise WorkspaceNotFoundError(workspace_id)
    stmt = (
        select(HitlReview)
        .join(Run, Run.run_id == HitlReview.run_id)
        .where(Run.workspace_id == workspace_id)
        .where(HitlReview.status == status)
        .order_by(HitlReview.created_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    reviews_result = await session.exec(stmt)
    return list(reviews_result.all())


@router.patch("/{workspace_id}/default-team", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_default_team(
    workspace_id: int,
    body: UpdateDefaultTeam,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set the default team pre-selected in the run modal for this workspace. Pass null to clear."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    from app.services.runner import AVAILABLE_TEAMS
    if body.default_team is not None and body.default_team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.default_team!r}. Available: {AVAILABLE_TEAMS}")
    ws.default_team = body.default_team
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.delete("/{workspace_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_workspace(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    await session.delete(ws)
    await session.commit()
