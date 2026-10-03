"""Tests for the full login/logout/register/verify-email session flow.

Covers the gaps left by test_auth.py (which only tests API key presence):
  - POST /auth/register: success, duplicate email, weak password, missing email
  - POST /auth/login: success sets cookie, wrong password, unknown email
  - DELETE /auth/token: revokes session (cookie becomes invalid)
  - POST /auth/verify-email: correct code, wrong code, expired code, replay
  - POST /auth/resend-code: invalidates previous code, issues new one
  - Session expiry: expired UserSession is rejected
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-session-secret-do-not-use-in-prod")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register(client, email: str = "user@example.com", password: str = "Password123!"):
    return await client.post("/auth/register", json={"email": email, "password": password})


async def _login(client, email: str, password: str):
    return await client.post("/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_creates_user(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "user@example.com"
    assert "api_key" in body
    assert body["email_verified"] is False
    # Session cookie must be set
    assert "antcrew_session" in r.cookies


@pytest.mark.asyncio
async def test_register_duplicate_email_rejected(client):
    await _register(client, "dup@example.com")
    r = await _register(client, "dup@example.com")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_register_duplicate_email_case_insensitive(client):
    await _register(client, "Case@Example.com")
    r = await _register(client, "case@example.com")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_register_weak_password_rejected(client):
    r = await client.post("/auth/register", json={"email": "pw@test.com", "password": "short"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_register_empty_email_rejected(client):
    r = await client.post("/auth/register", json={"email": "", "password": "Password123!"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_register_invalid_email_rejected(client):
    r = await client.post("/auth/register", json={"email": "not-an-email", "password": "Password123!"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success(client):
    await _register(client, "login-ok@example.com")
    r = await _login(client, "login-ok@example.com", "Password123!")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "login-ok@example.com"
    assert "antcrew_session" in r.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await _register(client, "login-wp@example.com")
    r = await _login(client, "login-wp@example.com", "WrongPassword!")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(client):
    r = await _login(client, "nobody@nowhere.io", "Password123!")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_email_case_insensitive(client):
    """Login with uppercase email should succeed for a lowercased registration."""
    await _register(client, "mixed@example.com")
    r = await _login(client, "MIXED@Example.COM", "Password123!")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /auth/token (logout)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_revokes_session(client):
    await _register(client, "logout@example.com")
    login_r = await _login(client, "logout@example.com", "Password123!")
    session_cookie = login_r.cookies["antcrew_session"]

    # Logout
    logout_r = await client.delete("/auth/token", cookies={"antcrew_session": session_cookie})
    assert logout_r.status_code == 204

    # Cookie should be cleared (empty value / max_age=0)
    assert logout_r.cookies.get("antcrew_session", "") == ""


@pytest.mark.asyncio
async def test_logout_revoked_session_rejects_next_request(client, session):
    """After logout the old session token must not authenticate further requests."""
    from app.models.run import ApiKey, User, UserSession

    await _register(client, "logout2@example.com")
    login_r = await _login(client, "logout2@example.com", "Password123!")
    token = login_r.cookies["antcrew_session"]

    await client.delete("/auth/token", cookies={"antcrew_session": token})

    # A state-mutating request with the revoked session must be rejected
    r = await client.patch(
        "/auth/profile",
        json={"display_name": "Hacker"},
        cookies={"antcrew_session": token},
        headers={"X-CSRF-Token": "anything"},
    )
    # Auth layer will 401 (session revoked) before CSRF check
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /auth/verify-email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_email_correct_code(client, session):
    """Providing the correct code marks the user as verified."""
    import hmac as _hmac

    from app.models.run import EmailVerification

    reg_r = await _register(client, "verify@example.com")
    token = reg_r.cookies["antcrew_session"]

    # Retrieve code_hash from DB and craft matching code
    # (we can't read the plaintext code; we must inject a known-hash code)
    code = "123456"
    secret = os.environ.get("SECRET_KEY", "test-session-secret-do-not-use-in-prod")
    code_hash = _hmac.new(secret.encode(), code.encode(), "sha256").hexdigest()

    # Overwrite the verification row with a known hash
    verif = (await session.exec(
        __import__("sqlmodel", fromlist=["select"]).select(EmailVerification)
    )).first()
    assert verif is not None, "EmailVerification row should exist after register"
    verif.code_hash = code_hash
    verif.code = None
    session.add(verif)
    await session.commit()

    r = await client.post(
        "/auth/verify-email",
        json={"code": code},
        cookies={"antcrew_session": token},
    )
    assert r.status_code == 200
    assert r.json()["verified"] is True


@pytest.mark.asyncio
async def test_verify_email_wrong_code(client, session):
    import hmac as _hmac

    from app.models.run import EmailVerification

    reg_r = await _register(client, "verify-wrong@example.com")
    token = reg_r.cookies["antcrew_session"]

    verif = (await session.exec(
        __import__("sqlmodel", fromlist=["select"]).select(EmailVerification)
    )).first()
    assert verif is not None

    r = await client.post(
        "/auth/verify-email",
        json={"code": "000000"},
        cookies={"antcrew_session": token},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_expired_code_rejected(client, session):
    """An expired EmailVerification row must be rejected even with the correct code."""
    import hmac as _hmac

    from app.models.run import EmailVerification

    reg_r = await _register(client, "verify-exp@example.com")
    token = reg_r.cookies["antcrew_session"]

    code = "654321"
    secret = os.environ.get("SECRET_KEY", "test-session-secret-do-not-use-in-prod")
    code_hash = _hmac.new(secret.encode(), code.encode(), "sha256").hexdigest()

    verif = (await session.exec(
        __import__("sqlmodel", fromlist=["select"]).select(EmailVerification)
    )).first()
    assert verif is not None
    verif.code_hash = code_hash
    verif.expires_at = _utcnow() - timedelta(minutes=1)  # already expired
    session.add(verif)
    await session.commit()

    r = await client.post(
        "/auth/verify-email",
        json={"code": code},
        cookies={"antcrew_session": token},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_replay_rejected(client, session):
    """Using the same code twice must fail on the second attempt."""
    import hmac as _hmac

    from app.models.run import EmailVerification

    reg_r = await _register(client, "verify-replay@example.com")
    token = reg_r.cookies["antcrew_session"]

    code = "789012"
    secret = os.environ.get("SECRET_KEY", "test-session-secret-do-not-use-in-prod")
    code_hash = _hmac.new(secret.encode(), code.encode(), "sha256").hexdigest()

    verif = (await session.exec(
        __import__("sqlmodel", fromlist=["select"]).select(EmailVerification)
    )).first()
    assert verif is not None
    verif.code_hash = code_hash
    session.add(verif)
    await session.commit()

    r1 = await client.post(
        "/auth/verify-email",
        json={"code": code},
        cookies={"antcrew_session": token},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        "/auth/verify-email",
        json={"code": code},
        cookies={"antcrew_session": token},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_without_session_rejected(client):
    r = await client.post("/auth/verify-email", json={"code": "123456"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/resend-code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resend_code_invalidates_previous(client, session):
    """Resending should mark all old pending codes as used."""
    from sqlmodel import select as _select

    from app.models.run import EmailVerification

    reg_r = await _register(client, "resend@example.com")
    token = reg_r.cookies["antcrew_session"]

    # There should be 1 pending code
    before = (await session.exec(_select(EmailVerification).where(EmailVerification.used == False))).all()  # noqa: E712
    assert len(before) == 1

    r = await client.post("/auth/resend-code", cookies={"antcrew_session": token})
    assert r.status_code == 200

    # After resend: old code is used=True, new code is used=False
    await session.refresh(before[0])
    assert before[0].used is True

    after = (await session.exec(_select(EmailVerification).where(EmailVerification.used == False))).all()  # noqa: E712
    assert len(after) == 1
    assert after[0].id != before[0].id


@pytest.mark.asyncio
async def test_resend_code_already_verified_returns_200(client, session):
    """Resend on an already-verified account should return 200 gracefully."""
    from sqlmodel import select as _select

    from app.models.run import User

    reg_r = await _register(client, "resend-verified@example.com")
    token = reg_r.cookies["antcrew_session"]

    # Manually mark verified
    user = (await session.exec(_select(User).where(User.email == "resend-verified@example.com"))).first()
    assert user is not None
    user.email_verified_at = _utcnow()
    session.add(user)
    await session.commit()

    r = await client.post("/auth/resend-code", cookies={"antcrew_session": token})
    assert r.status_code == 200
    assert "already verified" in r.json().get("message", "").lower()


# ---------------------------------------------------------------------------
# Session expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_session_rejected(client, session):
    """An expired UserSession must not authenticate requests."""
    from sqlmodel import select as _select

    from app.models.run import UserSession

    reg_r = await _register(client, "expired-sess@example.com")
    token = reg_r.cookies["antcrew_session"]

    token_hash = _sha256(token)
    user_session = (await session.exec(
        _select(UserSession).where(UserSession.token_hash == token_hash)
    )).first()
    # Fallback: pre-033 plaintext token
    if user_session is None:
        user_session = (await session.exec(
            _select(UserSession).where(UserSession.token == token)
        )).first()
    assert user_session is not None
    user_session.expires_at = _utcnow() - timedelta(seconds=1)
    session.add(user_session)
    await session.commit()

    r = await client.post(
        "/auth/verify-email",
        json={"code": "123456"},
        cookies={"antcrew_session": token},
    )
    assert r.status_code == 401
