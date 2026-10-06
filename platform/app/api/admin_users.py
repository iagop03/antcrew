"""Admin user management, feedback read, GDPR erase, and BYOK key rotation routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.auth import User
from app.models.feedback import UserFeedback

router = APIRouter(prefix="/admin", tags=["admin"])


class FeedbackRow(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    user_id: Optional[int]
    workspace_id: Optional[int]
    context: str
    helpful: Optional[bool]
    message: Optional[str]
    created_at: datetime


class AdminUserRow(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    email: str
    display_name: Optional[str]
    is_platform_admin: bool
    accounting_access: bool = False
    use_case: Optional[str]
    team_size: Optional[str]
    created_at: datetime


class ByokRotateRequest(BaseModel):
    new_key: str


@router.get("/feedback", response_model=list[FeedbackRow])
async def list_feedback(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    limit: int = 100,
    offset: int = 0,
):
    rows = (await session.exec(
        select(UserFeedback).order_by(UserFeedback.created_at.desc()).offset(offset).limit(limit)
    )).all()
    return rows


@router.get("/users", response_model=list[AdminUserRow])
async def list_users(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    limit: int = 100,
    offset: int = 0,
):
    rows = (await session.exec(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
    )).all()
    return rows


@router.patch("/users/{user_id}/admin", response_model=AdminUserRow)
async def set_user_admin(
    user_id: int,
    is_admin: bool,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    user: Optional[User] = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    user.is_platform_admin = is_admin
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/users/{user_id}/accounting", response_model=AdminUserRow)
async def set_user_accounting(
    user_id: int,
    accounting_access: bool,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    user: Optional[User] = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    user.accounting_access = accounting_access
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/users/{user_id}/erase", status_code=200)
async def erase_user_data(
    user_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """GDPR Art. 17 — anonymise PII and erase run request content. Irreversible."""
    from app.models.auth import ApiKey, UserSession, WorkspaceMembership
    from app.models.discovery import DiscoverySession
    from app.models.run import Run
    from app.models.workspace import Workspace

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.email.startswith("erased_") and user.email.endswith("@erased.antcrew"):
        raise HTTPException(409, "User data has already been erased")

    erased_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    placeholder = f"[erased {erased_at}]"

    user.email = f"erased_{user_id}@erased.antcrew"
    user.display_name = "[erased]"
    user.totp_secret = None
    user.mfa_enabled = False
    user.password_hash = "erased"
    session.add(user)

    owned_ids = {
        ws.id for ws in (await session.exec(
            select(Workspace).where(Workspace.owner_user_id == user_id)
        )).all()
        if ws.id is not None
    }
    member_ids = {
        m.workspace_id for m in (await session.exec(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user_id)
        )).all()
    }
    all_ws_ids = owned_ids | member_ids

    runs_erased = 0
    for ws_id in all_ws_ids:
        for run in (await session.exec(select(Run).where(Run.workspace_id == ws_id))).all():
            if run.request and not run.request.startswith("[erased"):
                run.request = placeholder
                session.add(run)
                runs_erased += 1

    disc_count = 0
    for ws_id in all_ws_ids:
        for ds in (await session.exec(
            select(DiscoverySession).where(DiscoverySession.workspace_id == ws_id)
        )).all():
            await session.delete(ds)
            disc_count += 1

    keys = (await session.exec(
        select(ApiKey).where(ApiKey.user_id == user_id).where(ApiKey.revoked_at == None)  # noqa: E711
    )).all()
    now_dt = datetime.now(timezone.utc)
    for key in keys:
        key.revoked_at = now_dt
        session.add(key)

    user_sessions = (await session.exec(
        select(UserSession).where(UserSession.user_id == user_id)
    )).all()
    for us in user_sessions:
        await session.delete(us)

    await session.commit()
    return {
        "erased_at": erased_at,
        "user_id": user_id,
        "email_anonymised": f"erased_{user_id}@erased.antcrew",
        "runs_request_erased": runs_erased,
        "discovery_sessions_deleted": disc_count,
        "api_keys_revoked": len(keys),
        "browser_sessions_deleted": len(user_sessions),
        "workspaces_affected": sorted(all_ws_ids),
    }


@router.post("/byok/rotate-key")
async def rotate_byok_key(
    body: ByokRotateRequest,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Re-encrypt all LLMProviderKey rows with a new BYOK_ENCRYPTION_KEY (zero-downtime rotation)."""
    try:
        from cryptography.fernet import Fernet
        new_fernet = Fernet(body.new_key.strip().encode())
    except Exception:
        raise HTTPException(422, "new_key is not a valid Fernet key. "
                            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                            "print(Fernet.generate_key().decode())\"")

    from app.core.byok import _decrypt, log_byok_event
    from app.models.workspace import LLMProviderKey

    rows = (await session.exec(select(LLMProviderKey))).all()
    reencrypted = skipped_empty = 0
    errors: list[dict] = []

    for row in rows:
        if not row.key_enc:
            skipped_empty += 1
            continue
        try:
            plaintext = _decrypt(row.key_enc)
            row.key_enc = new_fernet.encrypt(plaintext.encode()).decode()
            session.add(row)
            reencrypted += 1
        except Exception as exc:
            errors.append({
                "workspace_id": row.workspace_id,
                "provider": row.provider,
                "error": str(exc),
            })

    if errors:
        await session.rollback()
        raise HTTPException(500, {
            "message": "Rotation aborted — no rows updated. Fix the errors below before retrying.",
            "errors": errors,
        })

    for row in rows:
        if row.key_enc:
            await log_byok_event(
                session,
                workspace_id=row.workspace_id,
                provider=row.provider,
                event_type="rotate",
                note=f"batch rotation {reencrypted} keys",
            )

    await session.commit()
    return {
        "reencrypted": reencrypted,
        "skipped_empty_keys": skipped_empty,
        "next_step": "Remove BYOK_ENCRYPTION_KEY_OLD from env and redeploy.",
    }
