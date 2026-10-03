"""Tests for app.core.rate_limit — both the in-process and Redis-backed paths.

Strategy:
  - In-process (memory) backend is tested directly by exercising _check_memory
    and check() with REDIS_URL unset.
  - Redis backend is tested by patching _get_redis() with an AsyncMock that
    simulates the Lua script return values — we test the *integration* of
    _check_redis with the rest of check(), not the Lua script itself.
  - Identity extraction (_ident) covers all three key shapes: workspace,
    API key, and IP (with and without trusted-proxy XFF).
"""
from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(host: str = "1.2.3.4", xff: str | None = None) -> MagicMock:
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = host
    headers: dict[str, str] = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    req.headers = headers
    return req


def _reload_module(**env_overrides):
    """Re-import rate_limit with a patched os.environ so module-level constants reset."""
    import app.core.rate_limit as rl
    # Patch the module-level values directly (cheaper than full reload)
    for attr, val in env_overrides.items():
        setattr(rl, attr, val)
    return rl


# ---------------------------------------------------------------------------
# Identity extraction
# ---------------------------------------------------------------------------

def test_ident_workspace_id():
    from app.core.rate_limit import _ident
    req = _make_request()
    assert _ident(req, workspace_id=7, created_by=None) == "ws:7"


def test_ident_api_key():
    from app.core.rate_limit import _ident
    req = _make_request()
    assert _ident(req, workspace_id=None, created_by="key-abc") == "key:key-abc"


def test_ident_direct_ip():
    from app.core.rate_limit import _ident
    req = _make_request(host="5.6.7.8")
    assert _ident(req, workspace_id=None, created_by=None) == "ip:5.6.7.8"


def test_ident_xff_trusted_proxy():
    """X-Forwarded-For is honoured when the connection comes from a trusted proxy."""
    import app.core.rate_limit as rl
    original = rl._TRUSTED_PROXIES
    rl._TRUSTED_PROXIES = frozenset({"10.0.0.1"})
    try:
        req = _make_request(host="10.0.0.1", xff="203.0.113.5, 10.0.0.1")
        assert rl._ident(req, None, None) == "ip:203.0.113.5"
    finally:
        rl._TRUSTED_PROXIES = original


def test_ident_xff_untrusted_proxy():
    """X-Forwarded-For is ignored when the direct connection is NOT a trusted proxy."""
    import app.core.rate_limit as rl
    original = rl._TRUSTED_PROXIES
    rl._TRUSTED_PROXIES = frozenset({"10.0.0.1"})
    try:
        req = _make_request(host="9.9.9.9", xff="1.1.1.1")
        # spoofed XFF from untrusted host — must use direct IP
        assert rl._ident(req, None, None) == "ip:9.9.9.9"
    finally:
        rl._TRUSTED_PROXIES = original


def test_ident_no_trusted_proxies_never_trusts_xff():
    """When TRUSTED_PROXIES is empty, XFF is never used."""
    import app.core.rate_limit as rl
    original = rl._TRUSTED_PROXIES
    rl._TRUSTED_PROXIES = frozenset()
    try:
        req = _make_request(host="10.0.0.1", xff="attacker-ip")
        assert rl._ident(req, None, None) == "ip:10.0.0.1"
    finally:
        rl._TRUSTED_PROXIES = original


# ---------------------------------------------------------------------------
# In-process (memory) backend
# ---------------------------------------------------------------------------

async def test_memory_allows_within_limit():
    import app.core.rate_limit as rl
    rl.reset()
    original_rpm = rl._RPM
    rl._RPM = 5
    try:
        for _ in range(5):
            blocked = await rl._check_memory("test-key")
            assert not blocked
    finally:
        rl._RPM = original_rpm
        rl.reset()


async def test_memory_blocks_at_limit():
    import app.core.rate_limit as rl
    rl.reset()
    original_rpm = rl._RPM
    rl._RPM = 3
    try:
        for _ in range(3):
            await rl._check_memory("burst-key")
        blocked = await rl._check_memory("burst-key")
        assert blocked
    finally:
        rl._RPM = original_rpm
        rl.reset()


async def test_memory_isolates_keys():
    import app.core.rate_limit as rl
    rl.reset()
    original_rpm = rl._RPM
    rl._RPM = 2
    try:
        await rl._check_memory("key-a")
        await rl._check_memory("key-a")
        # key-a is now at limit; key-b should still pass
        assert await rl._check_memory("key-a")   # blocked
        assert not await rl._check_memory("key-b")  # allowed
    finally:
        rl._RPM = original_rpm
        rl.reset()


async def test_memory_window_slides():
    """Entries older than the window are evicted and free up capacity."""
    import time
    import app.core.rate_limit as rl
    rl.reset()
    original_rpm = rl._RPM
    original_window = rl._WINDOW
    rl._RPM = 2
    rl._WINDOW = 0.05   # 50 ms window for the test
    try:
        await rl._check_memory("slide-key")
        await rl._check_memory("slide-key")
        assert await rl._check_memory("slide-key")  # at limit

        # Wait for the window to expire
        import asyncio
        await asyncio.sleep(0.06)

        assert not await rl._check_memory("slide-key")  # window slid, allowed again
    finally:
        rl._RPM = original_rpm
        rl._WINDOW = original_window
        rl.reset()


def test_reset_clears_windows():
    import app.core.rate_limit as rl
    rl._windows["ws:1"] = __import__("collections").deque([1.0, 2.0])
    rl.reset()
    assert not rl._windows


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------

async def test_redis_allowed_when_lua_returns_zero():
    import app.core.rate_limit as rl
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=0)
    with patch.object(rl, "_get_redis", return_value=mock_redis):
        blocked = await rl._check_redis("ws:42")
    assert not blocked
    mock_redis.eval.assert_awaited_once()


async def test_redis_blocked_when_lua_returns_one():
    import app.core.rate_limit as rl
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=1)
    with patch.object(rl, "_get_redis", return_value=mock_redis):
        blocked = await rl._check_redis("ws:42")
    assert blocked


async def test_redis_fail_open_on_error():
    """If Redis raises, the request passes through (fail-open) so we don't block legit traffic."""
    import app.core.rate_limit as rl
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=Exception("connection refused"))
    with patch.object(rl, "_get_redis", return_value=mock_redis):
        blocked = await rl._check_redis("ws:42")
    assert not blocked  # fail open


async def test_redis_key_prefix():
    """The Lua script receives a 'rl:' prefixed key."""
    import app.core.rate_limit as rl
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=0)
    with patch.object(rl, "_get_redis", return_value=mock_redis):
        await rl._check_redis("ws:99")
    call_args = mock_redis.eval.call_args
    assert call_args[0][2] == "rl:ws:99"  # positional: script, numkeys, key, ...


# ---------------------------------------------------------------------------
# Public check() interface
# ---------------------------------------------------------------------------

async def test_check_noop_when_rpm_zero():
    """RATE_LIMIT_RPM=0 disables the limiter entirely — no 429 even after many calls."""
    import app.core.rate_limit as rl
    rl.reset()
    original_rpm = rl._RPM
    original_url = rl._REDIS_URL
    rl._RPM = 0
    rl._REDIS_URL = None
    try:
        req = _make_request()
        for _ in range(200):
            await rl.check(req, None, None)  # must not raise
    finally:
        rl._RPM = original_rpm
        rl._REDIS_URL = original_url
        rl.reset()


async def test_check_raises_429_memory_backend():
    import app.core.rate_limit as rl
    rl.reset()
    original_rpm = rl._RPM
    original_url = rl._REDIS_URL
    rl._RPM = 2
    rl._REDIS_URL = None
    try:
        req = _make_request()
        await rl.check(req, None, None)
        await rl.check(req, None, None)
        with pytest.raises(HTTPException) as exc_info:
            await rl.check(req, None, None)
        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers
    finally:
        rl._RPM = original_rpm
        rl._REDIS_URL = original_url
        rl.reset()


async def test_check_raises_429_redis_backend():
    import app.core.rate_limit as rl
    rl.reset()
    original_url = rl._REDIS_URL
    rl._REDIS_URL = "redis://fake:6379/0"
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=1)  # Lua says: blocked
    with patch.object(rl, "_get_redis", return_value=mock_redis):
        req = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await rl.check(req, workspace_id=5, created_by=None)
        assert exc_info.value.status_code == 429
    rl._REDIS_URL = original_url


async def test_check_passes_redis_backend():
    import app.core.rate_limit as rl
    rl.reset()
    original_url = rl._REDIS_URL
    rl._REDIS_URL = "redis://fake:6379/0"
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=0)  # Lua says: allowed
    with patch.object(rl, "_get_redis", return_value=mock_redis):
        req = _make_request()
        await rl.check(req, workspace_id=5, created_by=None)  # must not raise
    rl._REDIS_URL = original_url


async def test_check_uses_redis_when_redis_url_set():
    """Ensures Redis path is taken (not memory) when _REDIS_URL is configured."""
    import app.core.rate_limit as rl
    rl.reset()
    original_url = rl._REDIS_URL
    rl._REDIS_URL = "redis://fake:6379/0"
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=0)
    with patch.object(rl, "_get_redis", return_value=mock_redis), \
         patch.object(rl, "_check_memory", AsyncMock()) as mem_mock:
        req = _make_request()
        await rl.check(req, None, None)
        mem_mock.assert_not_awaited()
        mock_redis.eval.assert_awaited_once()
    rl._REDIS_URL = original_url


async def test_check_uses_memory_when_no_redis_url():
    """Ensures memory path is taken when _REDIS_URL is not configured."""
    import app.core.rate_limit as rl
    rl.reset()
    original_url = rl._REDIS_URL
    rl._REDIS_URL = None
    with patch.object(rl, "_check_redis", AsyncMock()) as redis_mock:
        req = _make_request()
        await rl.check(req, None, None)
        redis_mock.assert_not_awaited()
    rl._REDIS_URL = original_url
    rl.reset()
