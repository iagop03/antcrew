"""GitHub App integration — installation callback, webhook receiver, and management API.

Routers:
  webhook_router  — no API-key auth (callback from GitHub OAuth flow, HMAC-signed webhooks)
  router          — protected by require_api_key (list / delete installations)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, require_api_key, ws_filter
from app.core.database import get_session
from app.models.github_app import GitHubInstallation

log = logging.getLogger(__name__)

# Bounded set of seen X-GitHub-Delivery IDs — prevents double-dispatch on webhook re-delivery.
_SEEN_DELIVERIES: set[str] = set()
_SEEN_MAX = 2000


def _is_duplicate_delivery(delivery_id: str) -> bool:
    """Return True if this delivery ID has already been processed."""
    if not delivery_id:
        return False
    if delivery_id in _SEEN_DELIVERIES:
        return True
    if len(_SEEN_DELIVERIES) >= _SEEN_MAX:
        _SEEN_DELIVERIES.clear()
    _SEEN_DELIVERIES.add(delivery_id)
    return False


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# No require_api_key: callback comes from GitHub OAuth redirect; webhook is HMAC-verified.
# The callback lives at /github/callback; the webhook at /webhooks/github (as registered
# in the GitHub App settings: https://platform.antcrew.org/webhooks/github).
webhook_router = APIRouter(tags=["github-app"])

# Protected endpoints for workspace members.
router = APIRouter(
    prefix="/github",
    tags=["github-app"],
    dependencies=[Depends(require_api_key)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_github_signature(body: bytes, signature_header: str) -> bool:
    """Return True when HMAC-SHA256 of *body* matches *signature_header*.

    If GITHUB_WEBHOOK_SECRET is not configured, skip verification (dev mode).
    The header format is ``sha256=<hexdigest>``.
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").encode()
    if not secret:
        return True  # dev mode: skip (warn logged at startup)
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Webhook-router endpoints (no CSRF, no API-key required)
# ---------------------------------------------------------------------------

@webhook_router.get("/github/callback", include_in_schema=False)
async def github_installation_callback(
    installation_id: int,
    setup_action: str = "install",
    state: Optional[str] = None,  # workspace_id encoded as string
    session: AsyncSession = Depends(get_session),
):
    """Called by GitHub after a user installs (or updates) the app.

    GitHub redirects the browser here with ``installation_id`` and the ``state``
    we embedded in the install URL (which encodes the target workspace_id).
    """
    workspace_id: Optional[int] = None
    if state and state.isdigit():
        workspace_id = int(state)

    if workspace_id is None:
        log.warning("github_app: callback with no valid workspace state=%r", state)
        return RedirectResponse(url="/settings?github=error&reason=no_workspace")

    if setup_action == "delete":
        # App uninstalled from GitHub side — remove our record.
        existing = (await session.exec(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
        )).first()
        if existing:
            await session.delete(existing)
            await session.commit()
        return RedirectResponse(url="/settings?github=disconnected")

    # Fetch installation details from GitHub to get account info.
    account_login = "unknown"
    account_type = "Organization"
    try:
        import httpx

        from app.services.github_tokens import _make_app_jwt

        app_jwt = _make_app_jwt()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.github.com/app/installations/{installation_id}",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if r.is_success:
                data = r.json()
                acc = data.get("account", {})
                account_login = acc.get("login", "unknown")
                account_type = acc.get("type", "Organization")
    except Exception as exc:
        log.warning("github_app: could not fetch installation details: %s", exc)

    # Upsert the installation record.
    existing = (await session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.workspace_id == workspace_id,
            GitHubInstallation.installation_id == installation_id,
        )
    )).first()

    now = datetime.utcnow()
    if existing:
        existing.account_login = account_login
        existing.account_type = account_type
        existing.updated_at = now
        session.add(existing)
    else:
        inst = GitHubInstallation(
            workspace_id=workspace_id,
            installation_id=installation_id,
            account_login=account_login,
            account_type=account_type,
            created_at=now,
            updated_at=now,
        )
        session.add(inst)

    await session.commit()
    log.info(
        "github_app: installation %d linked to workspace %d (%s)",
        installation_id, workspace_id, account_login,
    )
    return RedirectResponse(url="/settings?github=connected&tab=github")


@webhook_router.post("/webhooks/github", status_code=200, include_in_schema=False)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Receive GitHub webhook events.

    Verifies HMAC-SHA256 (``X-Hub-Signature-256``) then stores or routes the event.
    Currently handles ``installation`` lifecycle events to keep our DB in sync.
    Other events are acknowledged and logged.
    """
    body = await request.body()

    if not _verify_github_signature(body, x_hub_signature_256):
        log.warning("github_app: invalid webhook signature")
        raise HTTPException(401, "Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    action: str = payload.get("action", "")
    log.debug("github_app: webhook event=%r action=%r", x_github_event, action)

    # Keep installation records in sync when the app is uninstalled via GitHub UI.
    if x_github_event == "installation" and action == "deleted":
        inst_data = payload.get("installation", {})
        gh_installation_id: int = inst_data.get("id", 0)
        if gh_installation_id:
            records = (await session.exec(
                select(GitHubInstallation).where(
                    GitHubInstallation.installation_id == gh_installation_id
                )
            )).all()
            for rec in records:
                await session.delete(rec)
            if records:
                await session.commit()
                log.info(
                    "github_app: removed %d installation record(s) for gh_id=%d",
                    len(records), gh_installation_id,
                )

    # Dispatch a run when a push matches a configured push trigger.
    if x_github_event == "push" and not _is_duplicate_delivery(x_github_delivery):
        ref: str = payload.get("ref", "")  # e.g. "refs/heads/main"
        repo_full_name: str = (payload.get("repository") or {}).get("full_name", "")
        gh_inst_id: int = (payload.get("installation") or {}).get("id", 0)
        if gh_inst_id:
            insts = (await session.exec(
                select(GitHubInstallation).where(
                    GitHubInstallation.installation_id == gh_inst_id
                )
            )).all()
            for inst in insts:
                if not inst.push_goal:
                    continue
                # Branch filter: "*" matches all; otherwise match full ref or short branch name
                bf = inst.push_branch_filter or "*"
                if bf != "*":
                    branch = ref.removeprefix("refs/heads/")
                    if ref != bf and branch != bf:
                        continue
                # Repo allowlist
                allowlist = json.loads(inst.repo_allowlist or "[]")
                if allowlist and repo_full_name not in allowlist:
                    continue
                # Fire-and-forget dispatch
                async def _dispatch(ws_id=inst.workspace_id, goal=inst.push_goal, mdl=inst.push_model):
                    try:
                        from app.services.runner import dispatch
                        await dispatch(
                            "DevTeam", goal, "default",
                            created_by="github-push",
                            workspace_id=ws_id,
                            model=mdl,
                        )
                        log.info("github_app: push trigger dispatched for workspace %d", ws_id)
                    except Exception as _exc:
                        log.warning("github_app: push trigger dispatch failed: %s", _exc)
                import asyncio as _asyncio
                _asyncio.ensure_future(_dispatch())

    return {"received": True}


# ---------------------------------------------------------------------------
# Protected management endpoints
# ---------------------------------------------------------------------------

@router.get("/installations")
async def list_installations(
    workspace_id: Optional[int] = None,
    ctx: WorkspaceContext = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """List GitHub App installations accessible to the caller.

    Pass ``workspace_id`` to filter to a single workspace (must be accessible).
    """
    stmt = select(GitHubInstallation)

    if workspace_id is not None:
        # Validate the caller can access this workspace.
        ids = ctx.workspace_ids
        if ids is not None and workspace_id not in ids:
            raise HTTPException(403, "Access to this workspace is not allowed")
        stmt = stmt.where(GitHubInstallation.workspace_id == workspace_id)
    else:
        stmt = ws_filter(stmt, GitHubInstallation.workspace_id, ctx)

    installations = (await session.exec(stmt)).all()
    return [
        {
            "id": inst.id,
            "workspace_id": inst.workspace_id,
            "installation_id": inst.installation_id,
            "account_login": inst.account_login,
            "account_type": inst.account_type,
            "repo_allowlist": json.loads(inst.repo_allowlist or "[]"),
            "push_goal": inst.push_goal,
            "push_model": inst.push_model,
            "push_branch_filter": inst.push_branch_filter,
            "created_at": inst.created_at.isoformat() if inst.created_at else None,
        }
        for inst in installations
    ]


@router.delete("/installations/{installation_id}", status_code=204)
async def disconnect_installation(
    installation_id: int,
    ctx: WorkspaceContext = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Disconnect a GitHub App installation from a workspace."""
    inst = (await session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )).first()

    if inst is None:
        raise HTTPException(404, "Installation not found")

    # Verify the caller has access to the installation's workspace.
    ids = ctx.workspace_ids
    if ids is not None and inst.workspace_id not in ids:
        raise HTTPException(403, "Access to this workspace is not allowed")

    await session.delete(inst)
    await session.commit()
    log.info(
        "github_app: disconnected installation %d from workspace %d",
        installation_id, inst.workspace_id,
    )


class _PushTriggerBody(BaseModel):
    push_goal: Optional[str] = None       # null to disable
    push_model: str = "claude"
    push_branch_filter: str = "*"


@router.patch("/installations/{installation_id}/push-trigger", status_code=200)
async def configure_push_trigger(
    installation_id: int,
    body: _PushTriggerBody,
    ctx: WorkspaceContext = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the push trigger for a GitHub App installation.

    When ``push_goal`` is set, any push event on the matched branch will
    automatically dispatch a DevTeam run with that goal in the linked workspace.
    Pass ``push_goal: null`` to disable.
    """
    inst = (await session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )).first()
    if inst is None:
        raise HTTPException(404, "Installation not found")
    ids = ctx.workspace_ids
    if ids is not None and inst.workspace_id not in ids:
        raise HTTPException(403, "Access to this workspace is not allowed")

    inst.push_goal = body.push_goal or None
    inst.push_model = body.push_model or "claude"
    inst.push_branch_filter = body.push_branch_filter or "*"
    inst.updated_at = datetime.utcnow()
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    return {
        "installation_id": inst.installation_id,
        "push_goal": inst.push_goal,
        "push_model": inst.push_model,
        "push_branch_filter": inst.push_branch_filter,
    }
