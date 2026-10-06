"""Admin campaign management routes."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.admin import Campaign

router = APIRouter(prefix="/admin", tags=["admin"])


class CampaignCreate(BaseModel):
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str = "all"
    discount_days: Optional[int] = None
    max_participants: Optional[int] = None

    def clean_dates(self) -> "CampaignCreate":
        return self


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    multiplier: Optional[float] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    target: Optional[str] = None
    active: Optional[bool] = None
    discount_days: Optional[int] = None
    max_participants: Optional[int] = None


class CampaignRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str
    active: bool
    discount_days: Optional[int] = None
    max_participants: Optional[int] = None
    created_at: datetime


@router.get("/campaigns", response_model=list[CampaignRow])
async def list_campaigns(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from sqlmodel import select
    rows = (await session.exec(
        select(Campaign).order_by(Campaign.starts_at.desc())
    )).all()
    return rows


@router.post("/campaigns", response_model=CampaignRow, status_code=201)
async def create_campaign(
    body: CampaignCreate,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    if body.target not in ("all", "new"):
        raise HTTPException(422, "target must be 'all' or 'new'")
    if body.multiplier <= 0:
        raise HTTPException(422, "multiplier must be greater than 0")
    body.clean_dates()
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "ends_at must be after starts_at")
    campaign = Campaign(
        name=body.name,
        multiplier=body.multiplier,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        target=body.target,
        active=True,
        discount_days=body.discount_days,
        max_participants=body.max_participants,
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign


@router.patch("/campaigns/{campaign_id}", response_model=CampaignRow)
async def patch_campaign(
    campaign_id: int,
    body: CampaignPatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    camp: Optional[Campaign] = await session.get(Campaign, campaign_id)
    if camp is None:
        raise HTTPException(404, "Campaign not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(camp, field, value)
    session.add(camp)
    await session.commit()
    await session.refresh(camp)
    return camp


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    camp: Optional[Campaign] = await session.get(Campaign, campaign_id)
    if camp is None:
        raise HTTPException(404, "Campaign not found")
    await session.delete(camp)
    await session.commit()
