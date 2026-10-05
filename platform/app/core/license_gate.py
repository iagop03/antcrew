"""FastAPI dependencies and decorators for license feature gating.

Usage in an endpoint::

    from app.core.license_gate import require_feature, require_tier

    @router.post("/run-schedules")
    async def create_schedule(
        _: None = Depends(require_feature("scheduling")),
        ...
    ):
        ...

    @router.get("/compliance/export")
    async def export_compliance(
        _: None = Depends(require_feature("compliance_export")),
        ...
    ):
        ...

Both raise HTTP 402 with a clear message when the feature is not available,
so the frontend can show a consistent upgrade prompt.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.core.license import LicenseContext, get_license

_UPGRADE_URLS = {
    "team":      "https://antcrew.org/pricing",
    "regulated": "https://antcrew.org/regulated",
}

_FEATURE_TIER: dict[str, str] = {
    # Team features
    "scheduling":          "team",
    "webhooks":            "team",
    "sso_github":          "team",
    "sso_saml":            "team",
    "hitl_multi":          "team",
    "hitl_escalation":     "team",
    "evals_regression":    "team",
    "team_history":        "team",
    "multiple_workspaces": "team",
    # Regulated features
    "compliance_pack":     "regulated",
    "compliance_export":   "regulated",
    "hash_chain_export":   "regulated",
    "approvers_config":    "regulated",
    "servicenow":          "regulated",
    "field_encryption":    "regulated",
    "dpa_templates":       "regulated",
    "retention_policy":    "regulated",
}


def _license_dep() -> LicenseContext:
    return get_license()


def require_feature(feature: str):
    """FastAPI dependency that raises 402 if the active license lacks *feature*."""
    def _check(lic: LicenseContext = Depends(_license_dep)) -> None:
        if not lic.has_feature(feature):
            required_tier = _FEATURE_TIER.get(feature, "team")
            url = _UPGRADE_URLS.get(required_tier, "https://antcrew.org/pricing")
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "feature_not_available",
                    "feature": feature,
                    "required_tier": required_tier,
                    "current_tier": lic.tier,
                    "upgrade_url": url,
                    "message": (
                        f"'{feature}' requires the {required_tier.capitalize()} tier. "
                        f"Your current license is '{lic.tier}'. "
                        f"Upgrade at {url}"
                    ),
                },
            )
    return _check


def require_tier(tier: str):
    """FastAPI dependency that raises 402 if the active license is below *tier*."""
    _order = ("open", "team", "regulated")

    def _check(lic: LicenseContext = Depends(_license_dep)) -> None:
        current_idx = _order.index(lic.tier) if lic.tier in _order else 0
        required_idx = _order.index(tier) if tier in _order else 0
        if current_idx < required_idx:
            url = _UPGRADE_URLS.get(tier, "https://antcrew.org/pricing")
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "tier_not_available",
                    "required_tier": tier,
                    "current_tier": lic.tier,
                    "upgrade_url": url,
                    "message": (
                        f"This feature requires the {tier.capitalize()} tier. "
                        f"Your current license is '{lic.tier}'. "
                        f"Upgrade at {url}"
                    ),
                },
            )
    return _check


def check_workspace_limit(current_count: int) -> None:
    """Raise 402 if creating a new workspace would exceed the license limit.

    Call this inside workspace creation endpoints before inserting into the DB.
    """
    lic = get_license()
    if not lic.within_workspace_limit(current_count):
        limit = lic.workspace_limit
        url = _UPGRADE_URLS.get("team", "https://antcrew.org/pricing")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "workspace_limit_reached",
                "current_tier": lic.tier,
                "workspace_limit": limit,
                "current_count": current_count,
                "upgrade_url": url,
                "message": (
                    f"Your {lic.tier} license allows {limit} workspace(s). "
                    f"You already have {current_count}. Upgrade at {url}"
                ),
            },
        )


def check_member_limit(current_count: int) -> None:
    """Raise 402 if adding a member would exceed the license limit.

    Call this inside member invitation/creation endpoints.
    """
    lic = get_license()
    if not lic.within_member_limit(current_count):
        limit = lic.member_limit
        url = _UPGRADE_URLS.get("team", "https://antcrew.org/pricing")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "member_limit_reached",
                "current_tier": lic.tier,
                "member_limit": limit,
                "current_count": current_count,
                "upgrade_url": url,
                "message": (
                    f"Your {lic.tier} license allows {limit} member(s) per workspace. "
                    f"You already have {current_count}. Upgrade at {url}"
                ),
            },
        )
