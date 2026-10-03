"""Admin billing rates, platform agent model defaults, and bulk-apply routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.admin import PlatformConfig
from app.models.workspace import Workspace

router = APIRouter(prefix="/admin", tags=["admin"])


class BillingRates(BaseModel):
    managed_cost_multiplier: float
    byok_service_multiplier: float
    proxy_service_multiplier: float
    managed_enabled: bool
    byok_enabled: bool
    proxy_enabled: bool
    compliance_pack_price_monthly: float = 299.0
    compliance_pack_price_annual: float = 2990.0
    tier_cheap_model: str = "groq:llama-3.3-70b-versatile"
    tier_standard_model: str = "claude:claude-sonnet-5"
    tier_premium_model: str = "claude:claude-opus-5"


class BillingRatesPatch(BaseModel):
    managed_cost_multiplier: Optional[float] = None
    byok_service_multiplier: Optional[float] = None
    proxy_service_multiplier: Optional[float] = None
    managed_enabled: Optional[bool] = None
    byok_enabled: Optional[bool] = None
    proxy_enabled: Optional[bool] = None
    compliance_pack_price_monthly: Optional[float] = None
    compliance_pack_price_annual: Optional[float] = None
    tier_cheap_model: Optional[str] = None
    tier_standard_model: Optional[str] = None
    tier_premium_model: Optional[str] = None


class BulkApplyRatesRequest(BaseModel):
    mode: str = "downward_only"


class BulkApplyRatesResponse(BaseModel):
    updated: int
    skipped_locked: int
    skipped_override: int
    skipped_no_change: int


class PlatformAgentModelsPatch(BaseModel):
    default_agent_models: Optional[dict] = None


_DEFAULT_RATES = BillingRates(
    managed_cost_multiplier=3.0,
    byok_service_multiplier=0.4,
    proxy_service_multiplier=0.7,
    managed_enabled=True,
    byok_enabled=True,
    proxy_enabled=True,
    compliance_pack_price_monthly=299.0,
    compliance_pack_price_annual=2990.0,
    tier_cheap_model="groq:llama-3.3-70b-versatile",
    tier_standard_model="claude:claude-sonnet-5",
    tier_premium_model="claude:claude-opus-5",
)


def _rates_from_cfg(cfg: PlatformConfig) -> BillingRates:
    return BillingRates(
        managed_cost_multiplier=cfg.managed_cost_multiplier,
        byok_service_multiplier=cfg.byok_service_multiplier,
        proxy_service_multiplier=cfg.proxy_service_multiplier,
        managed_enabled=cfg.managed_enabled,
        byok_enabled=cfg.byok_enabled,
        proxy_enabled=cfg.proxy_enabled,
        compliance_pack_price_monthly=cfg.compliance_pack_price_monthly,
        compliance_pack_price_annual=cfg.compliance_pack_price_annual,
        tier_cheap_model=getattr(cfg, "tier_cheap_model", "groq:llama-3.3-70b-versatile") or "groq:llama-3.3-70b-versatile",
        tier_standard_model=getattr(cfg, "tier_standard_model", "claude:claude-sonnet-5") or "claude:claude-sonnet-5",
        tier_premium_model=getattr(cfg, "tier_premium_model", "claude:claude-opus-5") or "claude:claude-opus-5",
    )


@router.get("/billing-rates", response_model=BillingRates)
async def get_billing_rates(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    cfg = await session.get(PlatformConfig, 1)
    return _rates_from_cfg(cfg) if cfg else _DEFAULT_RATES


@router.patch("/billing-rates", response_model=BillingRates)
async def patch_billing_rates(
    body: BillingRatesPatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    for field, value in body.model_dump(exclude_unset=True).items():
        if isinstance(value, float) and value <= 0:
            raise HTTPException(422, f"{field} must be greater than 0")
    cfg = await session.get(PlatformConfig, 1)
    if cfg is None:
        cfg = PlatformConfig(id=1)
        session.add(cfg)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(cfg, field, value)
    from app.models._utils import _utcnow
    cfg.updated_at = _utcnow()
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return _rates_from_cfg(cfg)


@router.get("/agent-models/defaults")
async def get_platform_agent_defaults(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
) -> dict:
    cfg = await session.get(PlatformConfig, 1)
    return {"default_agent_models": (cfg.default_agent_models or {}) if cfg else {}}


@router.patch("/agent-models/defaults")
async def patch_platform_agent_defaults(
    body: PlatformAgentModelsPatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
) -> dict:
    cfg = await session.get(PlatformConfig, 1)
    if cfg is None:
        cfg = PlatformConfig(id=1)
        session.add(cfg)
    cfg.default_agent_models = body.default_agent_models or None
    from app.models._utils import _utcnow
    cfg.updated_at = _utcnow()
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return {"default_agent_models": cfg.default_agent_models or {}}


@router.post("/billing-rates/apply-to-existing", response_model=BulkApplyRatesResponse)
async def apply_rates_to_existing(
    body: BulkApplyRatesRequest,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Bulk-apply current platform rates to existing workspaces (mode: downward_only | all)."""
    if body.mode not in ("downward_only", "all"):
        raise HTTPException(422, "mode must be 'downward_only' or 'all'")
    cfg = await session.get(PlatformConfig, 1)
    if cfg is None:
        raise HTTPException(404, "Platform config not initialised")

    from app.core.byok import (
        BYOK_SERVICE_MULTIPLIER,
        MANAGED_COST_MULTIPLIER,
        PROXY_SERVICE_MULTIPLIER,
    )

    p_managed = cfg.managed_cost_multiplier
    p_byok    = cfg.byok_service_multiplier
    p_proxy   = cfg.proxy_service_multiplier

    workspaces = (await session.exec(select(Workspace))).all()
    skipped_locked = skipped_override = skipped_no_change = updated = 0

    for ws in workspaces:
        if ws.multiplier_locked:
            skipped_locked += 1
            continue
        if ws.cost_multiplier_override is not None:
            skipped_override += 1
            continue

        cur_managed = ws.base_managed_mult if ws.base_managed_mult is not None else MANAGED_COST_MULTIPLIER
        cur_byok    = ws.base_byok_mult    if ws.base_byok_mult    is not None else BYOK_SERVICE_MULTIPLIER
        cur_proxy   = ws.base_proxy_mult   if ws.base_proxy_mult   is not None else PROXY_SERVICE_MULTIPLIER

        changed = False
        if body.mode == "downward_only":
            if cur_managed > p_managed:
                ws.base_managed_mult = p_managed; changed = True
            if cur_byok > p_byok:
                ws.base_byok_mult = p_byok; changed = True
            if cur_proxy > p_proxy:
                ws.base_proxy_mult = p_proxy; changed = True
        else:
            if cur_managed != p_managed:
                ws.base_managed_mult = p_managed; changed = True
            if cur_byok != p_byok:
                ws.base_byok_mult = p_byok; changed = True
            if cur_proxy != p_proxy:
                ws.base_proxy_mult = p_proxy; changed = True

        if changed:
            session.add(ws)
            updated += 1
        else:
            skipped_no_change += 1

    await session.commit()
    return BulkApplyRatesResponse(
        updated=updated,
        skipped_locked=skipped_locked,
        skipped_override=skipped_override,
        skipped_no_change=skipped_no_change,
    )
