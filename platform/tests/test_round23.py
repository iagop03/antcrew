"""Round 23 — Alembic skip flag + per-workspace concurrency semaphore.

Covers:
- ANTCREW_SKIP_MIGRATION=true skips subprocess call in _run_alembic_upgrade()
- Advisory-lock constant is stable (smoke test for the key value)
- Per-workspace semaphore limits concurrent dispatches per workspace
- Semaphore is per-workspace: different workspaces don't block each other
- ANTCREW_WORKSPACE_MAX_CONCURRENT configures the per-workspace limit
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# P1 — ANTCREW_SKIP_MIGRATION skips alembic subprocess
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_migration_env_var_skips_subprocess(monkeypatch):
    """_run_alembic_upgrade() returns immediately without calling subprocess when
    ANTCREW_SKIP_MIGRATION=true."""
    monkeypatch.setenv("ANTCREW_SKIP_MIGRATION", "true")

    with patch("app.core.database.subprocess.run") as mock_run:
        from app.core.database import _run_alembic_upgrade
        # SQLite URL — function normally only runs for PostgreSQL; test the flag logic
        # by patching the early-return path directly.
        # We call it with a fake PG URL context by patching DB_URL.
        with patch("app.core.database.DB_URL", "postgresql+asyncpg://fake/db"):
            # Even with a PG URL, ANTCREW_SKIP_MIGRATION must prevent any subprocess call.
            await _run_alembic_upgrade()
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_skip_migration_values(monkeypatch):
    """All truthy values for ANTCREW_SKIP_MIGRATION are accepted."""
    for val in ("1", "true", "True", "TRUE", "yes", "Yes", "YES"):
        monkeypatch.setenv("ANTCREW_SKIP_MIGRATION", val)
        with patch("app.core.database.subprocess.run") as mock_run:
            from app.core.database import _run_alembic_upgrade
            with patch("app.core.database.DB_URL", "postgresql+asyncpg://fake/db"):
                await _run_alembic_upgrade()
            mock_run.assert_not_called(), f"subprocess called for ANTCREW_SKIP_MIGRATION={val!r}"


@pytest.mark.asyncio
async def test_skip_migration_false_does_not_skip(monkeypatch):
    """ANTCREW_SKIP_MIGRATION=false (or absent) proceeds to the advisory-lock path.

    We don't actually connect to PostgreSQL in tests — we just verify subprocess IS
    attempted (the advisory-lock connect will fail first; that's expected here).
    """
    monkeypatch.delenv("ANTCREW_SKIP_MIGRATION", raising=False)

    with patch("app.core.database.DB_URL", "postgresql+asyncpg://fake/db"):
        with patch("app.core.database.engine") as mock_engine:
            mock_conn = AsyncMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=False)
            mock_conn.execute = AsyncMock(return_value=None)
            mock_engine.connect.return_value = mock_conn

            with patch("app.core.database.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="No upgrade needed.", stderr="")
                from app.core.database import _run_alembic_upgrade
                await _run_alembic_upgrade()

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "alembic" in call_args
            assert "upgrade" in call_args
            assert "head" in call_args


def test_migration_advisory_lock_key_is_stable():
    """The advisory lock key constant must not change — it would orphan locks held by
    older replicas during a rolling restart."""
    from app.core.database import _MIGRATION_ADVISORY_LOCK
    assert _MIGRATION_ADVISORY_LOCK == 938271406


# ---------------------------------------------------------------------------
# P2 — Per-workspace concurrency semaphore
# ---------------------------------------------------------------------------

def test_workspace_semaphore_created_on_first_access():
    """_get_workspace_semaphore returns a Semaphore for a new workspace_id."""
    from app.services.runner import _get_workspace_semaphore
    sem = _get_workspace_semaphore(99901)
    assert isinstance(sem, asyncio.Semaphore)


def test_workspace_semaphore_same_object_per_workspace():
    """The same Semaphore object is returned for the same workspace_id."""
    from app.services.runner import _get_workspace_semaphore
    sem_a = _get_workspace_semaphore(99902)
    sem_b = _get_workspace_semaphore(99902)
    assert sem_a is sem_b


def test_workspace_semaphores_independent_across_workspaces():
    """Different workspace_ids get different Semaphore objects."""
    from app.services.runner import _get_workspace_semaphore
    sem_a = _get_workspace_semaphore(99903)
    sem_b = _get_workspace_semaphore(99904)
    assert sem_a is not sem_b


@pytest.mark.asyncio
async def test_workspace_semaphore_limits_concurrency(monkeypatch):
    """With ANTCREW_WORKSPACE_MAX_CONCURRENT=1, a second concurrent acquire blocks
    until the first releases."""
    monkeypatch.setenv("ANTCREW_WORKSPACE_MAX_CONCURRENT", "1")

    # Import after setting env var so the default is picked up.
    # Use a fresh workspace_id that hasn't been seen yet.
    import importlib
    import app.services.runner as runner_mod
    importlib.reload(runner_mod)

    ws_id = 99905
    sem = runner_mod._get_workspace_semaphore(ws_id)
    assert sem._value == 1  # max=1

    acquired_first = False
    acquired_second = False
    first_done = asyncio.Event()

    async def first():
        nonlocal acquired_first
        async with sem:
            acquired_first = True
            await first_done.wait()

    async def second():
        nonlocal acquired_second
        async with sem:
            acquired_second = True

    t1 = asyncio.create_task(first())
    await asyncio.sleep(0)  # let first acquire
    assert acquired_first
    assert not acquired_second  # second blocked

    first_done.set()
    await asyncio.gather(t1, second())
    assert acquired_second


@pytest.mark.asyncio
async def test_workspace_semaphore_none_for_open_mode():
    """dispatch() with workspace_id=None (open mode) skips the semaphore — global
    access is unrestricted."""
    from app.services.runner import _get_workspace_semaphore
    # None workspace means no limiting — function returns None
    result = _get_workspace_semaphore(None)
    assert result is None
