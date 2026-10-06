"""Instance registration endpoint — called by self-hosted antcrew-platform at startup."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.core.database import get_session

router = APIRouter(prefix="/instances", tags=["instances"])

_SHARED_SECRET = os.environ.get("INSTANCE_REGISTRATION_SECRET", "")


class InstanceRegisterRequest(BaseModel):
    license_jwt: str
    fingerprint: str       # sha256(hostname + machine-id + install-uuid)
    hostname: str | None = None
    platform_version: str | None = None
    secret: str | None = None  # optional shared secret for extra verification


class InstanceRegisterResponse(BaseModel):
    status: str            # "registered" | "updated" | "revoked"
    instance_id: int
    max_instances: int
    active_instances: int
    message: str | None = None


@router.post("/register", response_model=InstanceRegisterResponse)
async def register_instance(
    body: InstanceRegisterRequest,
    session=Depends(get_session),
):
    """Register or update a self-hosted instance.

    Called by antcrew-platform at startup. The license JWT is verified,
    the fingerprint is stored, and the response tells the instance whether
    it is within its allowed instance count.
    """
    # Shared-secret check (optional — only enforced when env var is set)
    if _SHARED_SECRET and body.secret != _SHARED_SECRET:
        raise HTTPException(401, "Invalid registration secret")

    from app.models.portal import PortalInstance, PortalLicense

    # Locate the license by JWT token
    lic: PortalLicense | None = (await session.exec(
        select(PortalLicense).where(PortalLicense.jwt_token == body.license_jwt)
    )).first()

    if lic is None:
        raise HTTPException(404, "License not found")

    if lic.revoked:
        raise HTTPException(403, "License has been revoked")

    now = datetime.now(timezone.utc)
    if lic.expires_at < now:
        raise HTTPException(402, "License has expired")

    # Check for existing instance with this fingerprint
    existing: PortalInstance | None = (await session.exec(
        select(PortalInstance).where(
            PortalInstance.license_id == lic.id,
            PortalInstance.fingerprint == body.fingerprint,
        )
    )).first()

    if existing:
        if existing.revoked:
            return InstanceRegisterResponse(
                status="revoked",
                instance_id=existing.id,
                max_instances=lic.max_instances,
                active_instances=_count_active(await _all_active(session, lic.id)),
                message="This instance has been revoked. Contact support.",
            )
        existing.last_seen_at = now
        if body.hostname:
            existing.hostname = body.hostname
        if body.platform_version:
            existing.platform_version = body.platform_version
        session.add(existing)
        await session.commit()
        active = await _all_active(session, lic.id)
        return InstanceRegisterResponse(
            status="updated",
            instance_id=existing.id,
            max_instances=lic.max_instances,
            active_instances=_count_active(active),
        )

    # New instance — check capacity before adding
    active_instances = await _all_active(session, lic.id)
    active_count = _count_active(active_instances)
    if active_count >= lic.max_instances:
        # 7-day grace period: still register but warn
        inst = PortalInstance(
            license_id=lic.id,
            fingerprint=body.fingerprint,
            hostname=body.hostname,
            platform_version=body.platform_version,
        )
        session.add(inst)
        await session.commit()
        await session.refresh(inst)
        return InstanceRegisterResponse(
            status="registered",
            instance_id=inst.id,
            max_instances=lic.max_instances,
            active_instances=active_count + 1,
            message=(
                f"WARNING: License allows {lic.max_instances} instance(s) but "
                f"{active_count + 1} are now registered. "
                "You have a 7-day grace period before EE features are disabled."
            ),
        )

    inst = PortalInstance(
        license_id=lic.id,
        fingerprint=body.fingerprint,
        hostname=body.hostname,
        platform_version=body.platform_version,
    )
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    return InstanceRegisterResponse(
        status="registered",
        instance_id=inst.id,
        max_instances=lic.max_instances,
        active_instances=active_count + 1,
    )


class InstanceUsageRequest(BaseModel):
    license_jwt: str
    fingerprint: str
    runs_this_period: int    # runs since last usage ping
    secret: str | None = None


class InstanceUsageResponse(BaseModel):
    instance_id: int
    runs_this_month: int
    total_runs: int
    limit: int | None        # max_runs_per_month from license, None = unlimited


@router.post("/usage", response_model=InstanceUsageResponse)
async def report_usage(
    body: InstanceUsageRequest,
    session=Depends(get_session),
):
    """Report usage metrics from a self-hosted instance.

    Called periodically (e.g. hourly or after each run) by antcrew-platform.
    Increments per-instance run counters and resets the monthly counter at the
    start of each calendar month.
    """
    if _SHARED_SECRET and body.secret != _SHARED_SECRET:
        raise HTTPException(401, "Invalid registration secret")

    from app.models.portal import PortalInstance, PortalLicense

    lic: PortalLicense | None = (await session.exec(
        select(PortalLicense).where(PortalLicense.jwt_token == body.license_jwt)
    )).first()
    if lic is None:
        raise HTTPException(404, "License not found")

    inst: PortalInstance | None = (await session.exec(
        select(PortalInstance).where(
            PortalInstance.license_id == lic.id,
            PortalInstance.fingerprint == body.fingerprint,
            PortalInstance.revoked == False,  # noqa: E712
        )
    )).first()
    if inst is None:
        raise HTTPException(404, "Instance not found — register first")

    now = datetime.now(timezone.utc)

    # Reset monthly counter if we're in a new calendar month
    if inst.last_usage_at:
        if (inst.last_usage_at.year, inst.last_usage_at.month) != (now.year, now.month):
            inst.runs_this_month = 0

    inst.runs_this_month += body.runs_this_period
    inst.total_runs += body.runs_this_period
    inst.last_usage_at = now
    inst.last_seen_at = now
    session.add(inst)
    await session.commit()
    await session.refresh(inst)

    return InstanceUsageResponse(
        instance_id=inst.id,
        runs_this_month=inst.runs_this_month,
        total_runs=inst.total_runs,
        limit=lic.max_runs_per_month,
    )


async def _all_active(session, license_id: int):
    from app.models.portal import PortalInstance
    return (await session.exec(
        select(PortalInstance).where(
            PortalInstance.license_id == license_id,
            PortalInstance.revoked == False,  # noqa: E712
        )
    )).all()


def _count_active(instances) -> int:
    return len(instances)
