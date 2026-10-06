"""License portal models — customers, licenses, and registered instances."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class PortalUser(SQLModel, table=True):
    """A customer who purchased a license."""
    __tablename__ = "portal_user"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    # Lemon Squeezy order/customer identifiers for renewal lookup
    ls_customer_id: Optional[str] = Field(default=None, index=True)
    ls_order_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class PortalLicense(SQLModel, table=True):
    """A JWT license key issued to a customer."""
    __tablename__ = "portal_license"

    id: Optional[int] = Field(default=None, primary_key=True)
    portal_user_id: int = Field(foreign_key="portal_user.id", index=True)
    tier: str  # open | team | regulated
    jwt_token: str = Field(index=True)  # the full RS256 JWT
    max_instances: int = Field(default=1)
    max_runs_per_month: Optional[int] = Field(default=None)   # None = unlimited
    sso_domain: Optional[str] = Field(default=None)           # enforce SSO for this domain
    expires_at: datetime
    revoked: bool = Field(default=False)
    notes: Optional[str] = Field(default=None)  # admin notes
    created_at: datetime = Field(default_factory=_utcnow)


class PortalInstance(SQLModel, table=True):
    """A self-hosted instance that has registered with this license."""
    __tablename__ = "portal_instance"

    id: Optional[int] = Field(default=None, primary_key=True)
    license_id: int = Field(foreign_key="portal_license.id", index=True)
    # Stable fingerprint: sha256(hostname + machine-id + install-uuid)
    fingerprint: str = Field(index=True)
    hostname: Optional[str] = Field(default=None)
    platform_version: Optional[str] = Field(default=None)
    registered_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)
    revoked: bool = Field(default=False)
    # Usage tracking — updated via POST /instances/usage pings
    runs_this_month: int = Field(default=0)
    total_runs: int = Field(default=0)
    last_usage_at: Optional[datetime] = Field(default=None)


class PortalMagicLink(SQLModel, table=True):
    """One-time login token for magic-link auth."""
    __tablename__ = "portal_magic_link"

    id: Optional[int] = Field(default=None, primary_key=True)
    portal_user_id: int = Field(foreign_key="portal_user.id", index=True)
    token_hash: str = Field(index=True, unique=True)  # sha256 of raw token
    expires_at: datetime
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)


class PortalSession(SQLModel, table=True):
    """Browser session for portal users."""
    __tablename__ = "portal_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    portal_user_id: int = Field(foreign_key="portal_user.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)
