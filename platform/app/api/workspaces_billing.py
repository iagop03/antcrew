"""Workspace trial and billing-profile routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator
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
from app.models.run import Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

_ENTITY_TYPES = ("empresa", "autonomo", "particular")


class TrialUpdateRequest(BaseModel):
    is_trial: bool
    additional_credit_usd: Optional[float] = None


class BillingProfileBody(BaseModel):
    billing_entity_type: Optional[str] = None
    billing_razon_social: Optional[str] = None
    billing_nif: Optional[str] = None
    billing_address: Optional[str] = None
    billing_postal_code: Optional[str] = None
    billing_city: Optional[str] = None
    billing_country: Optional[str] = None
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None


class BillingProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    billing_entity_type: Optional[str] = None
    billing_razon_social: Optional[str] = None
    billing_nif: Optional[str] = None
    billing_address: Optional[str] = None
    billing_postal_code: Optional[str] = None
    billing_city: Optional[str] = None
    billing_country: str = "ES"
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    complete: bool = False

    @model_validator(mode="before")
    @classmethod
    def _set_complete(cls, data: object) -> object:
        if hasattr(data, "billing_nif"):
            d = {
                "billing_entity_type": data.billing_entity_type,
                "billing_razon_social": data.billing_razon_social,
                "billing_nif": data.billing_nif,
                "billing_address": data.billing_address,
                "billing_postal_code": data.billing_postal_code,
                "billing_city": data.billing_city,
                "billing_country": data.billing_country or "ES",
                "billing_email": data.billing_email,
                "billing_phone": data.billing_phone,
            }
            d["complete"] = bool(
                d["billing_entity_type"]
                and d["billing_razon_social"]
                and d["billing_nif"]
                and d["billing_address"]
                and d["billing_city"]
            )
            return d
        return data


@router.patch("/{workspace_id}/trial", dependencies=[Depends(require_role("admin"))])
async def update_trial(
    workspace_id: int,
    body: TrialUpdateRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
):
    """Set or clear trial status; optionally top up max_cost_usd."""
    from app.api.workspaces import WorkspacePublic
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    ws.is_trial = body.is_trial
    if body.additional_credit_usd is not None and body.additional_credit_usd > 0:
        current = ws.max_cost_usd or 0.0
        ws.max_cost_usd = round(current + body.additional_credit_usd, 4)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return WorkspacePublic.model_validate(ws)


@router.get("/{workspace_id}/billing-profile", response_model=BillingProfileOut)
async def get_billing_profile(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> BillingProfileOut:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    return BillingProfileOut.model_validate(ws)


@router.patch("/{workspace_id}/billing-profile", response_model=BillingProfileOut,
              dependencies=[Depends(require_role("admin", "write"))])
async def update_billing_profile(
    workspace_id: int,
    body: BillingProfileBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> BillingProfileOut:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    if body.billing_entity_type is not None:
        if body.billing_entity_type not in _ENTITY_TYPES:
            raise HTTPException(422, f"billing_entity_type must be one of: {', '.join(_ENTITY_TYPES)}")
        ws.billing_entity_type = body.billing_entity_type
    if body.billing_razon_social is not None:
        ws.billing_razon_social = body.billing_razon_social.strip() or None
    if body.billing_nif is not None:
        ws.billing_nif = body.billing_nif.strip().upper() or None
    if body.billing_address is not None:
        ws.billing_address = body.billing_address.strip() or None
    if body.billing_postal_code is not None:
        ws.billing_postal_code = body.billing_postal_code.strip() or None
    if body.billing_city is not None:
        ws.billing_city = body.billing_city.strip() or None
    if body.billing_country is not None:
        ws.billing_country = body.billing_country.strip().upper() or "ES"
    if body.billing_email is not None:
        ws.billing_email = body.billing_email.strip() or None
    if body.billing_phone is not None:
        ws.billing_phone = body.billing_phone.strip() or None
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return BillingProfileOut.model_validate(ws)
