"""Admin portal API — license management, customer lookup, instance control."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session

router = APIRouter(prefix="/admin/portal", tags=["admin-portal"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LicenseIssueRequest(BaseModel):
    email: EmailStr
    tier: str                      # open | team | regulated
    max_instances: int = 1
    expires_days: int = 365
    max_runs_per_month: Optional[int] = None   # None = unlimited
    sso_domain: Optional[str] = None           # enforce SSO for this email domain
    notes: Optional[str] = None
    ls_customer_id: Optional[str] = None
    ls_order_id: Optional[str] = None


class LicensePatchRequest(BaseModel):
    tier: Optional[str] = None
    max_instances: Optional[int] = None
    expires_days: Optional[int] = None   # extend N days from today
    max_runs_per_month: Optional[int] = None
    sso_domain: Optional[str] = None
    revoked: Optional[bool] = None
    notes: Optional[str] = None


class LicenseAdminOut(BaseModel):
    id: int
    email: str
    tier: str
    max_instances: int
    max_runs_per_month: Optional[int]
    sso_domain: Optional[str]
    expires_at: datetime
    revoked: bool
    notes: Optional[str]
    created_at: datetime
    active_instances: int
    total_instances: int


class InstanceAdminOut(BaseModel):
    id: int
    license_id: int
    fingerprint: str
    hostname: Optional[str]
    platform_version: Optional[str]
    registered_at: datetime
    last_seen_at: datetime
    revoked: bool


# ---------------------------------------------------------------------------
# List / search licenses
# ---------------------------------------------------------------------------

@router.get("/licenses", response_model=list[LicenseAdminOut])
async def list_licenses(
    email: Optional[str] = None,
    tier: Optional[str] = None,
    revoked: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from app.models.portal import PortalInstance, PortalLicense, PortalUser

    stmt = (
        select(PortalLicense, PortalUser)
        .join(PortalUser, PortalUser.id == PortalLicense.portal_user_id)
        .order_by(PortalLicense.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if email:
        stmt = stmt.where(PortalUser.email.ilike(f"%{email}%"))
    if tier:
        stmt = stmt.where(PortalLicense.tier == tier)
    if revoked is not None:
        stmt = stmt.where(PortalLicense.revoked == revoked)

    rows = (await session.exec(stmt)).all()
    result = []
    for lic, user in rows:
        instances = (await session.exec(
            select(PortalInstance).where(PortalInstance.license_id == lic.id)
        )).all()
        result.append(LicenseAdminOut(
            id=lic.id,
            email=user.email,
            tier=lic.tier,
            max_instances=lic.max_instances,
            max_runs_per_month=lic.max_runs_per_month,
            sso_domain=lic.sso_domain,
            expires_at=lic.expires_at,
            revoked=lic.revoked,
            notes=lic.notes,
            created_at=lic.created_at,
            active_instances=sum(1 for i in instances if not i.revoked),
            total_instances=len(instances),
        ))
    return result


@router.get("/licenses/{license_id}/instances", response_model=list[InstanceAdminOut])
async def list_instances(
    license_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from app.models.portal import PortalInstance

    instances = (await session.exec(
        select(PortalInstance).where(PortalInstance.license_id == license_id)
    )).all()
    return [
        InstanceAdminOut(
            id=i.id,
            license_id=i.license_id,
            fingerprint=i.fingerprint,
            hostname=i.hostname,
            platform_version=i.platform_version,
            registered_at=i.registered_at,
            last_seen_at=i.last_seen_at,
            revoked=i.revoked,
        )
        for i in instances
    ]


# ---------------------------------------------------------------------------
# Issue license
# ---------------------------------------------------------------------------

@router.post("/licenses", response_model=LicenseAdminOut, status_code=201)
async def issue_license(
    body: LicenseIssueRequest,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Manually issue a license — also triggered by Lemon Squeezy webhook."""
    from datetime import timedelta

    from app.models.portal import PortalLicense, PortalUser

    if body.tier not in ("open", "team", "regulated"):
        raise HTTPException(422, "tier must be open | team | regulated")
    if body.max_instances < 1:
        raise HTTPException(422, "max_instances must be >= 1")

    email = body.email.lower().strip()
    user = (await session.exec(
        select(PortalUser).where(PortalUser.email == email)
    )).first()
    if not user:
        user = PortalUser(
            email=email,
            ls_customer_id=body.ls_customer_id,
            ls_order_id=body.ls_order_id,
        )
        session.add(user)
        await session.flush()

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)
    jwt_token = _generate_license_jwt(
        email=email,
        tier=body.tier,
        max_instances=body.max_instances,
        expires_at=expires_at,
        max_runs_per_month=body.max_runs_per_month,
    )

    lic = PortalLicense(
        portal_user_id=user.id,
        tier=body.tier,
        jwt_token=jwt_token,
        max_instances=body.max_instances,
        max_runs_per_month=body.max_runs_per_month,
        sso_domain=body.sso_domain,
        expires_at=expires_at,
        notes=body.notes,
    )
    session.add(lic)
    await session.commit()
    await session.refresh(lic)

    return LicenseAdminOut(
        id=lic.id,
        email=email,
        tier=lic.tier,
        max_instances=lic.max_instances,
        max_runs_per_month=lic.max_runs_per_month,
        sso_domain=lic.sso_domain,
        expires_at=lic.expires_at,
        revoked=lic.revoked,
        notes=lic.notes,
        created_at=lic.created_at,
        active_instances=0,
        total_instances=0,
    )


# ---------------------------------------------------------------------------
# Patch license
# ---------------------------------------------------------------------------

@router.patch("/licenses/{license_id}", response_model=LicenseAdminOut)
async def patch_license(
    license_id: int,
    body: LicensePatchRequest,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from datetime import timedelta

    from app.models.portal import PortalInstance, PortalLicense, PortalUser

    lic = await session.get(PortalLicense, license_id)
    if not lic:
        raise HTTPException(404, "License not found")

    if body.tier is not None:
        lic.tier = body.tier
    if body.max_instances is not None:
        lic.max_instances = body.max_instances
    if body.expires_days is not None:
        lic.expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)
    if body.max_runs_per_month is not None:
        lic.max_runs_per_month = body.max_runs_per_month
    if body.sso_domain is not None:
        lic.sso_domain = body.sso_domain
    if body.revoked is not None:
        lic.revoked = body.revoked
    if body.notes is not None:
        lic.notes = body.notes

    session.add(lic)
    await session.commit()
    await session.refresh(lic)

    user = await session.get(PortalUser, lic.portal_user_id)
    instances = (await session.exec(
        select(PortalInstance).where(PortalInstance.license_id == lic.id)
    )).all()

    return LicenseAdminOut(
        id=lic.id,
        email=user.email if user else "",
        tier=lic.tier,
        max_instances=lic.max_instances,
        max_runs_per_month=lic.max_runs_per_month,
        sso_domain=lic.sso_domain,
        expires_at=lic.expires_at,
        revoked=lic.revoked,
        notes=lic.notes,
        created_at=lic.created_at,
        active_instances=sum(1 for i in instances if not i.revoked),
        total_instances=len(instances),
    )


# ---------------------------------------------------------------------------
# Instance admin actions
# ---------------------------------------------------------------------------

@router.delete("/instances/{instance_id}", status_code=204)
async def admin_revoke_instance(
    instance_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from app.models.portal import PortalInstance

    inst = await session.get(PortalInstance, instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    inst.revoked = True
    session.add(inst)
    await session.commit()


# ---------------------------------------------------------------------------
# Abuse detection
# ---------------------------------------------------------------------------

@router.get("/abuse-report")
async def abuse_report(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Licenses with more active instances than their max_instances limit."""
    from sqlalchemy import func

    from app.models.portal import PortalInstance, PortalLicense, PortalUser

    # Count active instances per license
    all_licenses = (await session.exec(
        select(PortalLicense).where(PortalLicense.revoked == False)  # noqa: E712
    )).all()

    abusers = []
    for lic in all_licenses:
        active = (await session.exec(
            select(PortalInstance).where(
                PortalInstance.license_id == lic.id,
                PortalInstance.revoked == False,  # noqa: E712
            )
        )).all()
        if len(active) > lic.max_instances:
            user = await session.get(PortalUser, lic.portal_user_id)
            abusers.append({
                "license_id": lic.id,
                "email": user.email if user else "?",
                "tier": lic.tier,
                "max_instances": lic.max_instances,
                "active_instances": len(active),
                "over_by": len(active) - lic.max_instances,
                "hostnames": [i.hostname for i in active if i.hostname],
            })

    return {"abusers": abusers, "count": len(abusers)}


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------

def _generate_license_jwt(
    email: str,
    tier: str,
    max_instances: int,
    expires_at: datetime,
    max_runs_per_month: Optional[int] = None,
) -> str:
    """Generate an RS256 JWT license key."""
    import os as _os

    try:
        import jwt as _jwt
    except ImportError:
        raise RuntimeError("PyJWT not installed")

    private_key_pem = _os.environ.get("LICENSE_PRIVATE_KEY_PEM", "")
    if not private_key_pem:
        # Fall back to a placeholder in dev — warns loudly
        import logging
        logging.getLogger(__name__).warning(
            "LICENSE_PRIVATE_KEY_PEM not set — issuing unsigned placeholder token"
        )
        import base64
        import json
        payload: dict = {"sub": email, "tier": tier, "max_instances": max_instances,
                         "exp": int(expires_at.timestamp()), "iss": "antcrew"}
        if max_runs_per_month is not None:
            payload["max_runs_per_month"] = max_runs_per_month
        fake = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return f"UNSIGNED.{fake}.PLACEHOLDER"

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem.encode(), password=None)
    payload = {
        "sub": email,
        "iss": "antcrew",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(expires_at.timestamp()),
        "tier": tier,
        "max_instances": max_instances,
    }
    if max_runs_per_month is not None:
        payload["max_runs_per_month"] = max_runs_per_month
    return _jwt.encode(payload, key, algorithm="RS256")
