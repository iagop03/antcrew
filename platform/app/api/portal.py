"""Customer portal — magic-link auth, license view, instance management."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlmodel import select

from app.core.database import get_session

router = APIRouter(prefix="/portal", tags=["portal"])

_PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "https://platform.antcrew.org")
_MAGIC_LINK_TTL_MINUTES = 15
_SESSION_TTL_DAYS = 7
_FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@antcrew.org")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _get_portal_user(
    portal_token: str | None = Cookie(default=None),
    session=Depends(get_session),
):
    """Dependency: resolve portal session → PortalUser or 401."""
    from app.models.portal import PortalSession, PortalUser

    if not portal_token:
        raise HTTPException(401, "Not authenticated")
    token_hash = _hash(portal_token)
    sess = (await session.exec(
        select(PortalSession).where(PortalSession.token_hash == token_hash)
    )).first()
    if not sess or sess.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Session expired")
    user = await session.get(PortalUser, sess.portal_user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return user


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerify(BaseModel):
    token: str


class LicenseOut(BaseModel):
    id: int
    tier: str
    max_instances: int
    expires_at: datetime
    revoked: bool
    active_instances: int
    instances: list[dict]


class PortalUserOut(BaseModel):
    email: str
    licenses: list[LicenseOut]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/auth/magic-link", status_code=202)
async def request_magic_link(
    body: MagicLinkRequest,
    session=Depends(get_session),
):
    """Send a magic-link login email. Always returns 202 to prevent user enumeration."""
    from app.models.portal import PortalMagicLink, PortalUser

    email = body.email.lower().strip()
    user = (await session.exec(
        select(PortalUser).where(PortalUser.email == email)
    )).first()
    if not user:
        # No account — silently succeed to prevent enumeration
        return {"detail": "If this email has a license, a login link was sent."}

    raw_token = secrets.token_urlsafe(32)
    link = PortalMagicLink(
        portal_user_id=user.id,
        token_hash=_hash(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=_MAGIC_LINK_TTL_MINUTES),
    )
    session.add(link)
    await session.commit()

    login_url = f"{_PORTAL_BASE_URL}/portal/auth/verify?token={raw_token}"
    await _send_magic_link_email(email, login_url)

    return {"detail": "If this email has a license, a login link was sent."}


@router.post("/auth/verify")
async def verify_magic_link(
    body: MagicLinkVerify,
    response: Response,
    session=Depends(get_session),
):
    """Validate a magic-link token and issue a session cookie."""
    from app.models.portal import PortalMagicLink, PortalSession

    token_hash = _hash(body.token)
    link = (await session.exec(
        select(PortalMagicLink).where(
            PortalMagicLink.token_hash == token_hash,
            PortalMagicLink.used == False,  # noqa: E712
        )
    )).first()

    if not link or link.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Invalid or expired token")

    link.used = True
    session.add(link)

    session_token = secrets.token_urlsafe(32)
    sess = PortalSession(
        portal_user_id=link.portal_user_id,
        token_hash=_hash(session_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS),
    )
    session.add(sess)
    await session.commit()

    response.set_cookie(
        "portal_token",
        session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_SESSION_TTL_DAYS * 86400,
        path="/portal",
    )
    return {"detail": "Authenticated"}


@router.post("/auth/logout", status_code=204)
async def logout(
    response: Response,
    portal_token: str | None = Cookie(default=None),
    session=Depends(get_session),
):
    from app.models.portal import PortalSession

    if portal_token:
        tok = _hash(portal_token)
        sess = (await session.exec(
            select(PortalSession).where(PortalSession.token_hash == tok)
        )).first()
        if sess:
            await session.delete(sess)
            await session.commit()
    response.delete_cookie("portal_token", path="/portal")


@router.get("/me", response_model=PortalUserOut)
async def get_me(
    user=Depends(_get_portal_user),
    session=Depends(get_session),
):
    """Return the authenticated customer's licenses and instances."""
    from app.models.portal import PortalInstance, PortalLicense

    licenses = (await session.exec(
        select(PortalLicense).where(PortalLicense.portal_user_id == user.id)
    )).all()

    licenses_out = []
    for lic in licenses:
        instances = (await session.exec(
            select(PortalInstance).where(PortalInstance.license_id == lic.id)
        )).all()
        active = [i for i in instances if not i.revoked]
        licenses_out.append(LicenseOut(
            id=lic.id,
            tier=lic.tier,
            max_instances=lic.max_instances,
            expires_at=lic.expires_at,
            revoked=lic.revoked,
            active_instances=len(active),
            instances=[
                {
                    "id": i.id,
                    "hostname": i.hostname,
                    "platform_version": i.platform_version,
                    "registered_at": i.registered_at.isoformat(),
                    "last_seen_at": i.last_seen_at.isoformat(),
                    "revoked": i.revoked,
                    "fingerprint_short": i.fingerprint[:12] + "…",
                }
                for i in instances
            ],
        ))

    return PortalUserOut(email=user.email, licenses=licenses_out)


@router.delete("/instances/{instance_id}", status_code=204)
async def revoke_instance(
    instance_id: int,
    user=Depends(_get_portal_user),
    session=Depends(get_session),
):
    """Revoke a specific instance (customer can do this to free up a slot)."""
    from app.models.portal import PortalInstance, PortalLicense

    inst = await session.get(PortalInstance, instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")

    lic = await session.get(PortalLicense, inst.license_id)
    if not lic or lic.portal_user_id != user.id:
        raise HTTPException(403, "Not your instance")

    inst.revoked = True
    session.add(inst)
    await session.commit()


# ---------------------------------------------------------------------------
# Email helper (stubbed — replace with SES/Resend/etc.)
# ---------------------------------------------------------------------------

async def _send_magic_link_email(to_email: str, login_url: str) -> None:
    """Send magic-link email. Configure SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    if not smtp_host:
        import logging
        logging.getLogger(__name__).info("magic-link URL (no SMTP): %s", login_url)
        return

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Tu link de acceso a antcrew"
    msg["From"] = _FROM_EMAIL
    msg["To"] = to_email

    plain = f"Accede a tu portal de licencias:\n{login_url}\n\nCaduca en {_MAGIC_LINK_TTL_MINUTES} minutos."
    html = f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:40px auto;color:#1a1a2e">
    <h2>antcrew — Portal de Licencias</h2>
    <p>Haz clic en el botón para acceder a tu portal:</p>
    <a href="{login_url}" style="display:inline-block;padding:12px 24px;background:#6366f1;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">Acceder al portal</a>
    <p style="color:#888;font-size:12px;margin-top:24px">Caduca en {_MAGIC_LINK_TTL_MINUTES} minutos. Si no lo solicitaste, ignora este email.</p>
    </body></html>
    """
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_pass)
        server.sendmail(_FROM_EMAIL, to_email, msg.as_string())
