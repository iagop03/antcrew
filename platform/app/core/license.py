"""License key validation for antcrew platform tiers.

License keys are RS256-signed JWTs. The private key is held by antcrew;
the public key is embedded here for offline validation. No phone-home.

Environment variable:
    ANTCREW_LICENSE_KEY  — the JWT license key string

Tiers:
    open       — 1 workspace, 3 members, core features only (default, no key required)
    team       — unlimited workspaces, 15 members, scheduling/webhooks/SSO
    regulated  — unlimited workspaces + members, full Compliance Pack

Grace period:
    If the key has expired, the platform runs in grace mode for 30 days.
    After grace period, it degrades to Open tier — never a hard stop.

Usage::

    from antcrew.core.license import get_license

    lic = get_license()
    if not lic.has_feature("scheduling"):
        raise HTTPException(402, "Run scheduling requires Team or Regulated tier.")
    if not lic.within_workspace_limit(current_count):
        raise HTTPException(402, "Workspace limit reached for your license tier.")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSA public key (embedded — matches the private key used by generate_license.py)
# Replace with the actual generated public key before first production use.
# ---------------------------------------------------------------------------
_PUBLIC_KEY_PEM = os.environ.get("ANTCREW_LICENSE_PUBLIC_KEY", """
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArvy7rfcC91Vd68DinP/I
rQgeZSoI/3xk1xT0P2jDFNDUIyUcRMhPuIMgqFskTqpUvhd3xuEhLRCWoT/AkIAu
F1d+Zj2JUsVyz9OvVoE3JObKZw8WOFc8sJjokjBq1buAC6oaCzNXcWfpid9SakTE
5ee/+Tn7A0NzmO9wg16Kr3eZMq2JJzaggRhtePUDgwQR4YQjw3vwDzY5hDn7NfqW
G5m8qW8jM/e+xrI/hbXVB25oC0pQcPSeyhEVWdoyYw9/fjs0IZee1Fmfy9vhoY3u
+t7+zBxDguPGl1TR28rLdofhyUn3/fpIlPVn1O3/eo891sI6D8Fve0lmTblkjLKM
bQIDAQAB
-----END PUBLIC KEY-----
""".strip())

# ---------------------------------------------------------------------------
# Feature sets per tier
# ---------------------------------------------------------------------------
_TIER_FEATURES: dict[str, set[str]] = {
    "open": {
        "runs", "hitl_single", "tracelog", "replay", "evals_basic",
        "cli", "byok",
    },
    "team": {
        "runs", "hitl_single", "hitl_multi", "hitl_escalation",
        "tracelog", "replay", "evals_basic", "evals_regression",
        "cli", "byok",
        "scheduling", "webhooks", "sso_github", "sso_saml",
        "team_history", "multiple_workspaces",
    },
    "regulated": {
        "runs", "hitl_single", "hitl_multi", "hitl_escalation",
        "tracelog", "replay", "evals_basic", "evals_regression",
        "cli", "byok",
        "scheduling", "webhooks", "sso_github", "sso_saml",
        "team_history", "multiple_workspaces",
        # Compliance Pack exclusives
        "compliance_pack", "compliance_export", "hash_chain_export",
        "approvers_config", "servicenow", "field_encryption",
        "dpa_templates", "retention_policy",
    },
}

# Limits per tier: (workspace_limit, member_limit)
# None = unlimited
_TIER_LIMITS: dict[str, tuple[Optional[int], Optional[int]]] = {
    "open":      (1,    3),
    "team":      (None, 15),
    "regulated": (None, None),
}

GRACE_PERIOD_DAYS = 30


@dataclass
class LicenseContext:
    tier: str = "open"
    workspace_limit: Optional[int] = 1
    member_limit: Optional[int] = 3
    features: set[str] = field(default_factory=lambda: set(_TIER_FEATURES["open"]))
    expires_at: Optional[datetime] = None
    grace: bool = False          # True = key expired, within grace period
    degraded: bool = False       # True = key expired + grace period over → back to Open
    raw_tier: str = "open"       # Tier in the key (before possible grace degradation)

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def within_workspace_limit(self, current_count: int) -> bool:
        if self.workspace_limit is None:
            return True
        return current_count < self.workspace_limit

    def within_member_limit(self, current_count: int) -> bool:
        if self.member_limit is None:
            return True
        return current_count < self.member_limit

    @property
    def is_paid(self) -> bool:
        return self.tier in ("team", "regulated") and not self.degraded

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expires_at is None:
            return None
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, delta.days)


def _make_open() -> LicenseContext:
    return LicenseContext()


def _parse_jwt(token: str) -> LicenseContext:
    """Validate the RS256 JWT and return a LicenseContext.

    Falls back to Open tier on any validation failure so the platform
    never crashes due to a bad key — it just loses paid features.
    """
    try:
        import jwt as _jwt  # PyJWT
    except ImportError:
        log.warning("PyJWT not installed — ANTCREW_LICENSE_KEY ignored, running Open tier")
        return _make_open()

    try:
        payload = _jwt.decode(
            token,
            _PUBLIC_KEY_PEM,
            algorithms=["RS256"],
            options={"verify_exp": False},  # we handle expiry + grace ourselves
        )
    except Exception as exc:
        log.warning("License key validation failed: %s — running Open tier", exc)
        return _make_open()

    raw_tier = payload.get("tier", "open")
    if raw_tier not in _TIER_FEATURES:
        log.warning("Unknown tier %r in license key — running Open tier", raw_tier)
        return _make_open()

    exp_ts = payload.get("exp")
    expires_at: Optional[datetime] = None
    grace = False
    degraded = False
    effective_tier = raw_tier

    if exp_ts is not None:
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > expires_at:
            days_over = (now - expires_at).days
            if days_over <= GRACE_PERIOD_DAYS:
                grace = True
                log.warning(
                    "License key expired %d day(s) ago — %d day(s) of grace period remaining",
                    days_over, GRACE_PERIOD_DAYS - days_over,
                )
            else:
                degraded = True
                effective_tier = "open"
                log.warning(
                    "License key expired %d day(s) ago — grace period over, degraded to Open tier",
                    days_over,
                )

    # Override limits from payload if present (enterprise custom limits)
    ws_limit, mem_limit = _TIER_LIMITS[effective_tier]
    if "workspace_limit" in payload:
        v = payload["workspace_limit"]
        ws_limit = None if v == 0 else int(v)
    if "member_limit" in payload:
        v = payload["member_limit"]
        mem_limit = None if v == 0 else int(v)

    return LicenseContext(
        tier=effective_tier,
        workspace_limit=ws_limit,
        member_limit=mem_limit,
        features=set(_TIER_FEATURES[effective_tier]),
        expires_at=expires_at,
        grace=grace,
        degraded=degraded,
        raw_tier=raw_tier,
    )


@lru_cache(maxsize=1)
def _cached_license() -> LicenseContext:
    """Parse and cache the license key once at startup."""
    key = os.environ.get("ANTCREW_LICENSE_KEY", "").strip()
    if not key:
        return _make_open()
    return _parse_jwt(key)


def get_license() -> LicenseContext:
    """Return the active LicenseContext.

    Cached after first call. To reload (e.g. in tests), call
    ``_cached_license.cache_clear()`` before ``get_license()``.
    """
    return _cached_license()


def license_status() -> dict:
    """Return a serialisable summary for the /admin/license endpoint."""
    lic = get_license()
    return {
        "tier": lic.tier,
        "raw_tier": lic.raw_tier,
        "grace": lic.grace,
        "degraded": lic.degraded,
        "workspace_limit": lic.workspace_limit,
        "member_limit": lic.member_limit,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "days_until_expiry": lic.days_until_expiry,
        "features": sorted(lic.features),
    }
