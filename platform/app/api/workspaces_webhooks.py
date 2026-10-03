"""Workspace webhook configuration and delivery history routes."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel import col, desc, select
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
from app.core.security import validate_external_url
from app.models.run import WebhookConfig, WebhookDelivery, WebhookEvent, Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)


class CreateWebhookConfig(BaseModel):
    url: str
    events: list[str] = ["pipeline.end"]
    label: Optional[str] = None

    @field_validator("url")
    @classmethod
    def url_must_be_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("url must start with https://")
        return v

    @field_validator("events")
    @classmethod
    def events_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("events must contain at least one event type")
        return v


class WebhookConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    url: str
    events: list[str]
    label: Optional[str] = None
    enabled: bool
    created_at: datetime


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: str
    url: str
    status: str
    attempts: int
    last_error: Optional[str]
    created_at: datetime


async def _hooks_with_events(
    session: AsyncSession, workspace_id: int
) -> list[WebhookConfigOut]:
    hooks = (await session.exec(
        select(WebhookConfig).where(WebhookConfig.workspace_id == workspace_id)
    )).all()
    if not hooks:
        return []
    hook_ids = [h.id for h in hooks if h.id is not None]
    ev_rows = (await session.exec(
        select(WebhookEvent).where(col(WebhookEvent.webhook_id).in_(hook_ids))
    )).all()
    events_by_hook: dict[int, list[str]] = {}
    for ev in ev_rows:
        events_by_hook.setdefault(ev.webhook_id, []).append(ev.event_type)
    return [
        WebhookConfigOut(
            id=h.id,  # type: ignore[arg-type]
            workspace_id=h.workspace_id,
            url=h.url,
            events=events_by_hook.get(h.id, []),  # type: ignore[arg-type]
            label=h.label,
            enabled=h.enabled,
            created_at=h.created_at,
        )
        for h in hooks
    ]


@router.get("/{workspace_id}/webhooks", response_model=list[WebhookConfigOut])
async def list_webhook_configs(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """List registered webhooks for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    if not result.first():
        raise WorkspaceNotFoundError(workspace_id)
    return await _hooks_with_events(session, workspace_id)


@router.post("/{workspace_id}/webhooks", status_code=201, response_model=WebhookConfigOut,
             dependencies=[Depends(require_role("admin"))])
async def create_webhook_config(
    workspace_id: int,
    body: CreateWebhookConfig,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Register a webhook URL for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    if not result.first():
        raise WorkspaceNotFoundError(workspace_id)
    try:
        validate_external_url(body.url, allow_http=True)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid webhook URL: {exc}")
    hook = WebhookConfig(workspace_id=workspace_id, url=body.url, label=body.label)
    session.add(hook)
    await session.flush()
    for event_type in body.events:
        session.add(WebhookEvent(webhook_id=hook.id, event_type=event_type))
    await session.commit()
    await session.refresh(hook)
    return WebhookConfigOut(
        id=hook.id,  # type: ignore[arg-type]
        workspace_id=hook.workspace_id,
        url=hook.url,
        events=body.events,
        label=hook.label,
        enabled=hook.enabled,
        created_at=hook.created_at,
    )


@router.delete("/{workspace_id}/webhooks/{webhook_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_webhook_config(
    workspace_id: int,
    webhook_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Remove a registered webhook and its event subscriptions."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(
        select(WebhookConfig)
        .where(WebhookConfig.id == webhook_id)
        .where(WebhookConfig.workspace_id == workspace_id)
    )
    hook = result.first()
    if not hook:
        raise HTTPException(404, f"Webhook {webhook_id} not found in workspace {workspace_id}")
    ev_rows = (await session.exec(
        select(WebhookEvent).where(WebhookEvent.webhook_id == webhook_id)
    )).all()
    for ev in ev_rows:
        await session.delete(ev)
    await session.delete(hook)
    await session.commit()


@router.patch("/{workspace_id}/webhooks/{webhook_id}/toggle",
              response_model=WebhookConfigOut,
              dependencies=[Depends(require_role("admin"))])
async def toggle_webhook_config(
    workspace_id: int,
    webhook_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Flip enabled/disabled on a webhook without deleting it."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(
        select(WebhookConfig)
        .where(WebhookConfig.id == webhook_id)
        .where(WebhookConfig.workspace_id == workspace_id)
    )
    hook = result.first()
    if not hook:
        raise HTTPException(404, f"Webhook {webhook_id} not found in workspace {workspace_id}")
    hook.enabled = not hook.enabled
    session.add(hook)
    await session.commit()
    await session.refresh(hook)
    events = [e.event_type for e in (await session.exec(
        select(WebhookEvent).where(WebhookEvent.webhook_id == hook.id)
    )).all()]
    return WebhookConfigOut(
        id=hook.id,
        workspace_id=hook.workspace_id,
        url=hook.url,
        events=events,
        label=hook.label,
        enabled=hook.enabled,
        created_at=hook.created_at,
    )


@router.get("/{workspace_id}/webhook-deliveries",
            response_model=list[WebhookDeliveryOut],
            dependencies=[Depends(require_role("admin", "read"))])
async def list_webhook_deliveries(
    workspace_id: int,
    webhook_id: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Return recent webhook delivery attempts for this workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    hook_query = select(WebhookConfig.url).where(WebhookConfig.workspace_id == workspace_id)
    if webhook_id is not None:
        hook_query = hook_query.where(WebhookConfig.id == webhook_id)
    hook_urls_result = await session.exec(hook_query)
    hook_urls = [r for r in hook_urls_result.all()]
    if not hook_urls:
        return []
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.url.in_(hook_urls))
        .order_by(desc(WebhookDelivery.created_at))
        .limit(limit)
    )
    result = await session.exec(stmt)
    return list(result.all())
