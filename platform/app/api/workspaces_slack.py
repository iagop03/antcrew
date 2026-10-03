"""Workspace Slack integration routes (webhook URL + Bot/App token management)."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
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
from app.core.security import validate_external_url
from app.models.run import Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)


class UpdateSlack(BaseModel):
    slack_webhook_url: Optional[str] = None
    slack_channel_id: Optional[str] = None


class UpdateSlackTokens(BaseModel):
    bot_token: str
    app_token: Optional[str] = None

    @field_validator("bot_token")
    @classmethod
    def bot_token_format(cls, v: str) -> str:
        if not v.startswith("xoxb-"):
            raise ValueError("bot_token must start with xoxb-")
        return v

    @field_validator("app_token")
    @classmethod
    def app_token_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("xapp-"):
            raise ValueError("app_token must start with xapp-")
        return v


@router.patch("/{workspace_id}/slack", dependencies=[Depends(require_role("admin"))])
async def set_slack_webhook(
    workspace_id: int,
    body: UpdateSlack,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the per-workspace Slack webhook URL for HITL notifications."""
    from app.api.workspaces import WorkspacePublic
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    if body.slack_webhook_url is not None:
        try:
            validate_external_url(body.slack_webhook_url)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    ws.slack_webhook_url = body.slack_webhook_url
    if body.slack_channel_id is not None:
        ws.slack_channel_id = body.slack_channel_id
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return WorkspacePublic.model_validate(ws)


@router.patch("/{workspace_id}/slack-tokens", dependencies=[Depends(require_role("admin"))])
async def set_slack_tokens(
    workspace_id: int,
    body: UpdateSlackTokens,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Store per-workspace Slack bot and app tokens, encrypted at rest."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    from app.core.slack_hitl import _encrypt
    ws.slack_bot_token_enc = _encrypt(body.bot_token)
    if body.app_token is not None:
        ws.slack_app_token_enc = _encrypt(body.app_token)
    session.add(ws)
    await session.commit()
    return {
        "workspace_id": workspace_id,
        "slack_bot_configured": True,
        "slack_app_configured": ws.slack_app_token_enc is not None,
    }


@router.delete("/{workspace_id}/slack-tokens", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def clear_slack_tokens(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Remove per-workspace Slack tokens, reverting to global env-var tokens."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.slack_bot_token_enc = None
    ws.slack_app_token_enc = None
    session.add(ws)
    await session.commit()


@router.post("/{workspace_id}/slack/test", dependencies=[Depends(require_role("admin", "write"))])
async def test_slack(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Send a test message to the workspace's configured Slack channel."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    from app.core.slack_hitl import _decrypt

    bot_token = (
        _decrypt(ws.slack_bot_token_enc) if ws.slack_bot_token_enc
        else os.environ.get("SLACK_BOT_TOKEN")
    )
    channel_id = ws.slack_channel_id or os.environ.get("SLACK_CHANNEL_ID")

    if not bot_token:
        raise HTTPException(422, "No Slack bot token configured.")
    if not channel_id:
        raise HTTPException(422, "No Slack channel configured.")

    try:
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=bot_token)
        resp = await client.chat_postMessage(
            channel=channel_id,
            text="AntCrew Slack integration is working correctly.",
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*AntCrew* Slack test successful.\nHITL notifications will appear here."},
            }],
        )
        if not resp["ok"]:
            raise HTTPException(502, f"Slack API error: {resp.get('error')}")
    except Exception as exc:
        raise HTTPException(502, f"Slack test failed: {exc}")

    return {"ok": True, "channel": channel_id}
