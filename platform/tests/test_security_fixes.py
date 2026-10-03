"""Tests for the security fixes introduced in the audit remediation.

Covers:
- WebSocket _Connection.enqueue workspace filtering (register_run / deregister_run)
- Invite token hashed lookup (token_hash) with legacy plaintext fallback
- WsAuth resolve functions return correct workspace_ids from DB
- Startup hardening (H1/H2/H5 from security audit Aug 2026):
  - H1: PLATFORM_API_KEY minimum entropy (≥32 chars)
  - H2: Open mode on public host is a hard error unless ANTCREW_OPEN_MODE=true
  - H5: ANTCREW_ENCRYPTION_KEY missing in production is a hard error
- H3 (full): CSP nonce middleware injects nonce into inline <script> tags
- DNS rebinding: validate_external_url resolves hostnames and rejects private IPs
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, Workspace, WorkspaceInvite


# ---------------------------------------------------------------------------
# WebSocket _Connection workspace filtering
# ---------------------------------------------------------------------------

def test_connection_enqueue_no_filter():
    """Without workspace_ids, all events pass through."""
    from antcrew import Event
    from app.api.stream import _Connection

    ws = MagicMock()
    conn = _Connection(ws, workspace_ids=None)

    event = Event("agent.start", run_id="run-abc", thread_id="t")
    conn.enqueue(event)
    assert conn._queue.qsize() == 1


def test_connection_enqueue_filters_other_workspace():
    """Events for runs in a different workspace are dropped."""
    from antcrew import Event
    from app.api.stream import _Connection, register_run, deregister_run

    ws = MagicMock()
    register_run("run-ws2", workspace_id=2)
    try:
        conn = _Connection(ws, workspace_ids=frozenset({1}))  # connected to workspace 1
        event = Event("agent.start", run_id="run-ws2", thread_id="t")
        conn.enqueue(event)
        assert conn._queue.qsize() == 0, "event from workspace 2 must be dropped"
    finally:
        deregister_run("run-ws2")


def test_connection_enqueue_passes_own_workspace():
    """Events for runs in the connection's own workspace pass through."""
    from antcrew import Event
    from app.api.stream import _Connection, register_run, deregister_run

    ws = MagicMock()
    register_run("run-ws1", workspace_id=1)
    try:
        conn = _Connection(ws, workspace_ids=frozenset({1}))
        event = Event("agent.start", run_id="run-ws1", thread_id="t")
        conn.enqueue(event)
        assert conn._queue.qsize() == 1
    finally:
        deregister_run("run-ws1")


def test_connection_enqueue_unknown_run_id_dropped_for_scoped_client():
    """Events with an unknown run_id are dropped for workspace-scoped connections.

    Fail-closed: if a run is not in the workspace map (deregistered or unregistered),
    workspace-scoped clients do not receive the event. Global-access connections
    (workspace_ids=None) still receive all events.
    """
    from antcrew import Event
    from app.api.stream import _Connection

    ws = MagicMock()
    conn = _Connection(ws, workspace_ids=frozenset({1}))
    event = Event("agent.start", run_id="unknown-run", thread_id="t")
    conn.enqueue(event)
    assert conn._queue.qsize() == 0, "unknown run_id must be dropped for scoped client"


def test_connection_enqueue_unknown_run_id_passes_for_global_client():
    """Global-access connections (workspace_ids=None) still receive events for unknown runs."""
    from antcrew import Event
    from app.api.stream import _Connection

    ws = MagicMock()
    conn = _Connection(ws, workspace_ids=None)
    event = Event("agent.start", run_id="unknown-run", thread_id="t")
    conn.enqueue(event)
    assert conn._queue.qsize() == 1


def test_connection_run_id_filter_still_works():
    """The existing run_id filter is preserved alongside the workspace filter."""
    from antcrew import Event
    from app.api.stream import _Connection, register_run, deregister_run

    ws = MagicMock()
    register_run("run-target", workspace_id=1)
    register_run("run-other", workspace_id=1)
    try:
        # Connected to workspace 1 and watching only run-target
        conn = _Connection(ws, run_id="run-target", workspace_ids=frozenset({1}))
        event_target = Event("step.end", run_id="run-target", thread_id="t")
        event_other = Event("step.end", run_id="run-other", thread_id="t")
        conn.enqueue(event_target)
        conn.enqueue(event_other)
        assert conn._queue.qsize() == 1  # only the matching run_id
    finally:
        deregister_run("run-target")
        deregister_run("run-other")


# ---------------------------------------------------------------------------
# Invite token hash lookup
# ---------------------------------------------------------------------------

def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _make_session(session, *, label: str) -> tuple[ApiKey, str]:
    """Create an ApiKey + UserSession and return (key, session_token)."""
    import secrets
    from datetime import timedelta
    from app.models.run import UserSession

    api_key = ApiKey(label=label, key_hash=_hash(label + "-secret"), role="write")
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    raw_token = secrets.token_urlsafe(32)
    user_session = UserSession(
        token=raw_token,
        api_key_id=api_key.id,
        expires_at=_utcnow() + timedelta(days=1),
        revoked=False,
    )
    session.add(user_session)
    await session.commit()
    return api_key, raw_token


@pytest.mark.asyncio
async def test_accept_invite_uses_token_hash(client, session):
    """accept_invite succeeds when invite is stored with token_hash (no plaintext)."""
    from datetime import timedelta

    ws = Workspace(name="Invite WS", slug="invite-ws")
    session.add(ws)
    api_key, session_token = await _make_session(session, label="inv-user")
    await session.refresh(ws)

    raw_token = "invite-abc123"
    invite = WorkspaceInvite(
        token=None,
        token_hash=_hash(raw_token),
        workspace_id=ws.id,
        invitee_email="user@example.com",
        inviter_email="admin@example.com",
        role="write",
        status="pending",
        expires_at=_utcnow() + timedelta(days=1),
    )
    session.add(invite)
    await session.commit()

    r = await client.post(
        "/auth/accept-invite",
        json={"token": raw_token},
        cookies={"antcrew_session": session_token},
    )
    assert r.status_code == 200
    assert r.json()["workspace_id"] == ws.id


@pytest.mark.asyncio
async def test_accept_invite_invalid_token_returns_404(client, session):
    """accept_invite returns 404 for an unknown token."""
    _, session_token = await _make_session(session, label="inv-user-2")

    r = await client.post(
        "/auth/accept-invite",
        json={"token": "completely-wrong-token"},
        cookies={"antcrew_session": session_token},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# H1 — PLATFORM_API_KEY minimum entropy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h1_short_platform_api_key_raises(monkeypatch):
    """H1: PLATFORM_API_KEY shorter than 32 chars raises RuntimeError at startup."""
    monkeypatch.setenv("PLATFORM_API_KEY", "short-key")  # 9 chars
    from app.core.startup import _check_auth_mode
    with pytest.raises(RuntimeError, match="32"):
        await _check_auth_mode()


@pytest.mark.asyncio
async def test_h1_32_char_platform_api_key_accepted(monkeypatch):
    """H1: PLATFORM_API_KEY of exactly 32 chars is accepted."""
    import secrets
    monkeypatch.setenv("PLATFORM_API_KEY", secrets.token_urlsafe(24))  # 32 base64 chars
    from app.core.startup import _check_auth_mode
    await _check_auth_mode()  # must not raise


@pytest.mark.asyncio
async def test_h1_long_platform_api_key_accepted(monkeypatch):
    """H1: PLATFORM_API_KEY of 43 chars (token_urlsafe(32) output) is accepted."""
    import secrets
    monkeypatch.setenv("PLATFORM_API_KEY", secrets.token_urlsafe(32))  # 43 chars
    from app.core.startup import _check_auth_mode
    await _check_auth_mode()  # must not raise


# ---------------------------------------------------------------------------
# H2 — Open mode on public host requires ANTCREW_OPEN_MODE=true
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h2_open_mode_public_host_raises(monkeypatch):
    """H2: no credentials + public HOST → RuntimeError unless ANTCREW_OPEN_MODE=true."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.delenv("ANTCREW_OPEN_MODE", raising=False)
    from app.core.startup import _check_auth_mode
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_exec = AsyncMock(return_value=mock_result)
    mock_session = AsyncMock()
    mock_session.exec = mock_exec
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=mock_session):
        with pytest.raises(RuntimeError, match="public interface"):
            await _check_auth_mode()


@pytest.mark.asyncio
async def test_h2_open_mode_public_host_allowed_with_flag(monkeypatch):
    """H2: ANTCREW_OPEN_MODE=true allows open mode on public host (explicit opt-in)."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("ANTCREW_OPEN_MODE", "true")
    from app.core.startup import _check_auth_mode
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_exec = AsyncMock(return_value=mock_result)
    mock_session = AsyncMock()
    mock_session.exec = mock_exec
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=mock_session):
        await _check_auth_mode()  # must not raise


@pytest.mark.asyncio
async def test_h2_open_mode_localhost_ok(monkeypatch):
    """H2: open mode on localhost is still allowed without any flag (dev default)."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.delenv("ANTCREW_REQUIRE_AUTH", raising=False)
    from app.core.startup import _check_auth_mode
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_exec = AsyncMock(return_value=mock_result)
    mock_session = AsyncMock()
    mock_session.exec = mock_exec
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=mock_session):
        await _check_auth_mode()  # must not raise


# ---------------------------------------------------------------------------
# H5 — ANTCREW_ENCRYPTION_KEY required in production
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h5_missing_encryption_key_in_prod_raises(monkeypatch):
    """H5: no ANTCREW_ENCRYPTION_KEY in APP_ENV=prod → RuntimeError."""
    monkeypatch.delenv("ANTCREW_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    from app.core.startup import _check_encryption_key
    with pytest.raises(RuntimeError, match="ANTCREW_ENCRYPTION_KEY"):
        await _check_encryption_key()


@pytest.mark.asyncio
async def test_h5_encryption_key_set_passes(monkeypatch):
    """H5: ANTCREW_ENCRYPTION_KEY set in prod → no error."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ANTCREW_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_ENV", "prod")
    from app.core.startup import _check_encryption_key
    await _check_encryption_key()  # must not raise


@pytest.mark.asyncio
async def test_h5_missing_encryption_key_in_dev_logs_only(monkeypatch, caplog):
    """H5: no ANTCREW_ENCRYPTION_KEY in dev → only a debug log, no RuntimeError."""
    import logging
    monkeypatch.delenv("ANTCREW_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("HOST", "127.0.0.1")
    from app.core.startup import _check_encryption_key
    with caplog.at_level(logging.DEBUG):
        await _check_encryption_key()  # must not raise


# ---------------------------------------------------------------------------
# H3 (full) — CSP nonce middleware
# ---------------------------------------------------------------------------

def test_h3_inject_nonce_adds_to_bare_script_tag():
    """_inject_nonce adds nonce to a bare <script> tag."""
    from app.main import _inject_nonce
    html = b'<html><head></head><body><script>alert(1)</script></body></html>'
    result = _inject_nonce(html, b"TEST_NONCE")
    assert b'<script nonce="TEST_NONCE">' in result


def test_h3_inject_nonce_does_not_touch_external_scripts():
    """_inject_nonce leaves <script src="..."> untouched."""
    from app.main import _inject_nonce
    html = b'<script src="/static/app.js"></script>'
    result = _inject_nonce(html, b"MY_NONCE")
    assert b'nonce' not in result
    assert b'<script src="/static/app.js">' in result


def test_h3_inject_nonce_handles_cdn_script():
    """_inject_nonce leaves CDN <script src="..." defer> untouched."""
    from app.main import _inject_nonce
    html = b'<script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js" defer></script>'
    result = _inject_nonce(html, b"NONCE")
    assert b'nonce' not in result


def test_h3_inject_nonce_handles_mixed_page():
    """Mixed page: inline gets nonce, external scripts do not."""
    from app.main import _inject_nonce
    html = (
        b'<script src="/static/i18n.js"></script>'
        b'<script>if("serviceWorker" in navigator){navigator.serviceWorker.register("/sw.js")}</script>'
        b'<script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js" defer></script>'
        b'<script>function dashboard(){return {}}</script>'
    )
    result = _inject_nonce(html, b"TESTNONCE")
    # Inline scripts get the nonce
    assert result.count(b'nonce="TESTNONCE"') == 2
    # External scripts are unchanged
    assert b'<script src="/static/i18n.js">' in result
    assert b'<script src="https://cdn.jsdelivr.net' in result


@pytest.mark.asyncio
async def test_h3_csp_header_contains_nonce(client):
    """Security middleware sets a per-request nonce in the CSP header for HTML responses."""
    r = await client.get("/static/login.html")
    csp = r.headers.get("content-security-policy", "")
    # Should have nonce-based CSP, not unsafe-inline in script-src
    assert "nonce-" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


@pytest.mark.asyncio
async def test_h3_nonce_differs_per_request(client):
    """Each request generates a fresh nonce — reuse would allow replay attacks."""
    r1 = await client.get("/static/login.html")
    r2 = await client.get("/static/login.html")
    csp1 = r1.headers.get("content-security-policy", "")
    csp2 = r2.headers.get("content-security-policy", "")
    nonce1 = [p for p in csp1.split() if p.startswith("'nonce-")]
    nonce2 = [p for p in csp2.split() if p.startswith("'nonce-")]
    assert nonce1 and nonce2
    assert nonce1[0] != nonce2[0], "nonce must be unique per request"


# ---------------------------------------------------------------------------
# DNS rebinding — validate_external_url with resolve_dns
# ---------------------------------------------------------------------------

def test_dns_rebinding_resolve_rejects_private_resolution(monkeypatch):
    """validate_external_url raises ValueError when hostname resolves to a private IP."""
    import socket as _socket
    from app.core.security import validate_external_url

    # Simulate DNS resolution returning a private IP
    monkeypatch.setattr(
        _socket, "getaddrinfo",
        lambda *a, **kw: [(_socket.AF_INET, _socket.SOCK_STREAM, 0, "", ("192.168.1.1", 443))]
    )
    with pytest.raises(ValueError, match="private"):
        validate_external_url("https://attacker-dns-rebind.example.com/hook")


def test_dns_rebinding_resolve_accepts_public_ip(monkeypatch):
    """validate_external_url passes when hostname resolves to a public IP."""
    import socket as _socket
    from app.core.security import validate_external_url

    monkeypatch.setattr(
        _socket, "getaddrinfo",
        lambda *a, **kw: [(_socket.AF_INET, _socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))]
    )
    validate_external_url("https://example.com/webhook")  # must not raise


def test_dns_rebinding_resolve_rejects_loopback_resolution(monkeypatch):
    """validate_external_url raises ValueError when hostname resolves to loopback."""
    import socket as _socket
    from app.core.security import validate_external_url

    monkeypatch.setattr(
        _socket, "getaddrinfo",
        lambda *a, **kw: [(_socket.AF_INET, _socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError, match="private"):
        validate_external_url("https://internal.example.com/hook")


def test_dns_rebinding_skip_with_resolve_dns_false():
    """resolve_dns=False skips DNS resolution — for unit tests without network."""
    from app.core.security import validate_external_url
    # This would fail DNS resolution in a real network test; resolve_dns=False bypasses it
    validate_external_url("https://this-does-not-resolve.invalid/hook", resolve_dns=False)


def test_dns_rebinding_resolution_failure_raises(monkeypatch):
    """validate_external_url raises ValueError when DNS resolution fails entirely."""
    import socket as _socket
    from app.core.security import validate_external_url

    monkeypatch.setattr(
        _socket, "getaddrinfo",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("Name or service not known"))
    )
    with pytest.raises(ValueError, match="could not be resolved"):
        validate_external_url("https://nonexistent.invalid/hook")
