"""GitHub OAuth2 SSO — browser login via GitHub identity.

Flow:
  GET /auth/sso/github           → redirect to GitHub authorization URL
  GET /auth/sso/github/callback  → exchange code, upsert user, set session cookie

Required env vars:
  GITHUB_OAUTH_CLIENT_ID      — OAuth App client ID (different from GitHub App ID)
  GITHUB_OAUTH_CLIENT_SECRET  — OAuth App client secret
  PLATFORM_BASE_URL           — e.g. https://app.antcrew.org (used for redirect_uri)

Feature gate: sso_github (team tier). Returns 402 when not licensed.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime as _dt
from datetime import timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import select

from app.core.database import get_session
from app.core.license import get_license

log = logging.getLogger(__name__)

# Short cookie that carries the OAuth state (anti-CSRF).
_STATE_COOKIE = "antcrew_sso_state"
_STATE_MAX_AGE = 600  # 10 min — enough to complete the GitHub OAuth round-trip

# Session cookie config — mirrors auth_session.py
_SESSION_COOKIE = "antcrew_session"
_SESSION_MAX_AGE = 2592000  # 30 days

router = APIRouter(prefix="/auth/sso", tags=["sso"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_secure() -> bool:
    return os.environ.get("APP_ENV", "dev").lower() != "dev"


def _require_sso_github() -> None:
    """Raise HTTP 402 when the instance license does not include sso_github."""
    try:
        lic = get_license()
        if not lic.has_feature("sso_github"):
            raise HTTPException(402, "SSO via GitHub requires a Team license or higher.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(402, "License check failed — SSO not available.")


def _github_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=user:email"
        f"&state={state}"
    )


async def _exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Optional[str]:
    """Exchange an OAuth code for an access token. Returns token or None on failure."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    if not r.is_success:
        log.warning("sso: GitHub token exchange failed: %s", r.status_code)
        return None
    data = r.json()
    return data.get("access_token")


async def _get_github_user(access_token: str) -> Optional[dict]:
    """Fetch authenticated GitHub user info. Returns dict with id, login, email."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if not r.is_success:
        log.warning("sso: GitHub user fetch failed: %s", r.status_code)
        return None
    return r.json()


async def _get_github_primary_email(access_token: str) -> Optional[str]:
    """Fetch user's primary verified email from GitHub (needed if profile email is private)."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if not r.is_success:
        return None
    for entry in r.json():
        if entry.get("primary") and entry.get("verified"):
            return entry["email"]
    return None


async def _create_session_token(user_id: int, api_key_id: int, session) -> str:
    """Insert a UserSession row and return the raw token (same as auth_session._create_session)."""
    import hashlib

    from app.models.auth import UserSession

    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    now = _dt.now(timezone.utc)
    user_session = UserSession(
        token=None,
        token_hash=token_hash,
        user_id=user_id,
        api_key_id=api_key_id,
        created_at=now,
        expires_at=now + timedelta(seconds=_SESSION_MAX_AGE),
        revoked=False,
    )
    session.add(user_session)
    await session.commit()
    return raw


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/github", include_in_schema=False)
async def sso_github_start(response: Response):
    """Redirect the browser to GitHub's OAuth authorization page.

    Set GITHUB_OAUTH_CLIENT_ID + GITHUB_OAUTH_CLIENT_SECRET to enable.
    Requires sso_github feature in the instance license.
    """
    _require_sso_github()

    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(503, "GITHUB_OAUTH_CLIENT_ID is not configured.")

    base_url = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    if not base_url:
        raise HTTPException(503, "PLATFORM_BASE_URL is not configured.")

    state = secrets.token_urlsafe(32)
    redirect_uri = f"{base_url}/auth/sso/github/callback"
    authorize_url = _github_authorize_url(client_id, redirect_uri, state)

    # Store state in a short-lived HttpOnly cookie for CSRF protection
    redirect = RedirectResponse(url=authorize_url, status_code=302)
    redirect.set_cookie(
        key=_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        max_age=_STATE_MAX_AGE,
    )
    return redirect


@router.get("/github/callback", include_in_schema=False)
async def sso_github_callback(
    request: Request,
    response: Response,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    session=Depends(get_session),
):
    """Receive the GitHub OAuth callback, upsert user, and create a platform session."""
    _require_sso_github()

    if error:
        log.warning("sso: GitHub OAuth error: %s", error)
        return RedirectResponse(url="/login?error=github_denied", status_code=302)

    if not code or not state:
        return RedirectResponse(url="/login?error=github_missing_params", status_code=302)

    # CSRF check: state must match what we stored in the cookie
    stored_state = request.cookies.get(_STATE_COOKIE)
    if not stored_state or not secrets.compare_digest(stored_state, state):
        log.warning("sso: GitHub OAuth state mismatch (CSRF attempt?)")
        return RedirectResponse(url="/login?error=github_state_mismatch", status_code=302)

    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "")
    base_url = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    redirect_uri = f"{base_url}/auth/sso/github/callback"

    # Exchange code → access token
    access_token = await _exchange_code(client_id, client_secret, code, redirect_uri)
    if not access_token:
        return RedirectResponse(url="/login?error=github_token_exchange", status_code=302)

    # Get user info from GitHub
    gh_user = await _get_github_user(access_token)
    if not gh_user:
        return RedirectResponse(url="/login?error=github_user_fetch", status_code=302)

    gh_id: int = gh_user["id"]
    gh_login: str = gh_user.get("login", "")
    gh_name: str = gh_user.get("name") or gh_login
    # GitHub may not expose email if private — fetch from /user/emails
    gh_email: Optional[str] = gh_user.get("email")
    if not gh_email:
        gh_email = await _get_github_primary_email(access_token)
    if not gh_email:
        return RedirectResponse(url="/login?error=github_no_email", status_code=302)

    gh_email = gh_email.strip().lower()

    from app.models.auth import ApiKey, User, UserSession  # noqa: F401 — for clarity
    from app.models.workspace import Workspace

    # Find existing user by github_id first, then by email
    user = (await session.exec(
        select(User).where(User.github_id == gh_id)
    )).first()

    if user is None:
        user = (await session.exec(
            select(User).where(User.email == gh_email)
        )).first()

    if user is None:
        # New user — create account with GitHub identity
        import re as _re
        slug_base = _re.sub(r"[^a-z0-9]+", "-", gh_email.split("@")[0])[:40] or "workspace"
        slug = slug_base
        for _attempt in range(20):
            slug_exists = (await session.exec(
                select(Workspace).where(Workspace.slug == slug)
            )).first()
            if not slug_exists:
                break
            slug = f"{slug_base}-{secrets.token_hex(3)}"
        else:
            slug = f"workspace-{secrets.token_hex(6)}"

        from app.core.byok import TRIAL_CREDIT_USD
        from app.core.promo import get_active_free_promo
        from app.models.admin import PlatformConfig

        _promo = await get_active_free_promo(session)
        _pcfg = await session.get(PlatformConfig, 1)

        user = User(
            email=gh_email,
            password_hash=f"github_sso:{secrets.token_hex(32)}",  # non-matchable placeholder
            display_name=gh_name,
            github_id=gh_id,
            github_login=gh_login,
            email_verified_at=_dt.now(timezone.utc),  # GitHub email is verified
            created_at=_dt.now(timezone.utc),
        )
        session.add(user)
        await session.flush()

        workspace = Workspace(
            name=gh_name or gh_email,
            slug=slug,
            is_trial=_promo is not None,
            max_cost_usd=TRIAL_CREDIT_USD if _promo is not None else None,
            owner_user_id=user.id,
            base_managed_mult=_pcfg.managed_cost_multiplier if _pcfg else 3.0,
            base_byok_mult=_pcfg.byok_service_multiplier if _pcfg else 0.4,
            base_proxy_mult=_pcfg.proxy_service_multiplier if _pcfg else 0.7,
        )
        session.add(workspace)
        await session.flush()

        raw_key = secrets.token_urlsafe(32)
        import hashlib
        api_key = ApiKey(
            label="admin",
            key_hash=raw_key,           # stored as plaintext temporarily; hashed on next use
            key_prefix=hashlib.sha256(raw_key.encode()).hexdigest()[:16],
            workspace_id=workspace.id,
            role="admin",
            user_id=user.id,
            email=gh_email,
        )
        session.add(api_key)
        await session.flush()
        log.info("sso: created new user %s (github_id=%d)", gh_email, gh_id)
    else:
        # Existing user — link GitHub ID if not yet linked
        if user.github_id is None:
            user.github_id = gh_id
            user.github_login = gh_login
            session.add(user)
            await session.flush()

        # Find admin API key for session
        api_key = (await session.exec(
            select(ApiKey).where(
                ApiKey.user_id == user.id,
                ApiKey.role == "admin",
                ApiKey.revoked_at == None,  # noqa: E711
            )
        )).first()
        if api_key is None:
            return RedirectResponse(url="/login?error=github_no_api_key", status_code=302)

    raw_session_token = await _create_session_token(user.id, api_key.id, session)

    # Clear state cookie, set session cookie, redirect to dashboard
    redirect = RedirectResponse(url="/runs", status_code=302)
    redirect.delete_cookie(key=_STATE_COOKIE)
    redirect.set_cookie(
        key=_SESSION_COOKIE,
        value=raw_session_token,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        max_age=_SESSION_MAX_AGE,
    )
    log.info("sso: GitHub SSO login success for %s", gh_email)
    return redirect


# ---------------------------------------------------------------------------
# SAML SSO — stub (sso_saml, team tier)
# ---------------------------------------------------------------------------

_SAML_SETUP_MSG = (
    "SAML SSO requires additional setup: "
    "(1) install python3-saml (`pip install python3-saml`), "
    "(2) set SAML_SP_ENTITY_ID, SAML_SP_ACS_URL, SAML_IDP_METADATA_URL, "
    "and SAML_SP_PRIVATE_KEY_PEM environment variables, "
    "(3) register the SP metadata at /auth/sso/saml/metadata with your IdP. "
    "Contact support@antcrew.org for setup assistance."
)


def _require_sso_saml() -> None:
    """Raise HTTP 402 when the instance license does not include sso_saml."""
    try:
        lic = get_license()
        if not lic.has_feature("sso_saml"):
            raise HTTPException(402, "SAML SSO requires a Team license or higher.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(402, "License check failed — SSO not available.")


@router.get("/saml/metadata", include_in_schema=False)
async def sso_saml_metadata():
    """Serve SP metadata XML to be registered with the IdP.

    Not yet implemented — configure the required env vars and restart the platform.
    """
    _require_sso_saml()
    raise HTTPException(501, _SAML_SETUP_MSG)


@router.get("/saml", include_in_schema=False)
async def sso_saml_start():
    """Redirect the browser to the SAML IdP for authentication.

    Not yet implemented — configure the required env vars and restart the platform.
    """
    _require_sso_saml()
    raise HTTPException(501, _SAML_SETUP_MSG)


@router.post("/saml/callback", include_in_schema=False)
async def sso_saml_callback():
    """Receive the SAML assertion from the IdP and establish a platform session.

    Not yet implemented — configure the required env vars and restart the platform.
    """
    _require_sso_saml()
    raise HTTPException(501, _SAML_SETUP_MSG)
