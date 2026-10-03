"""Unit tests for Celery task logic (app/tasks/run_tasks.py).

These tests verify the task behaviour without a real Celery broker by calling
_run_async() directly with mocked dependencies.

CT01  KV memory is saved when _kv_out has a dirty InMemoryKVMemory after the run
CT02  KV memory is NOT saved when _kv_out is empty (no memory used)
CT03  KV memory is NOT saved when memory is not dirty
CT04  write-back calls _do_write_back when write_back=True and tmp_repo_dir exists
CT05  write-back skips gracefully when tmp_repo_dir does not exist (different host)
CT06  write-back skips when write_back=False
CT07  tmp_repo_dir is cleaned up after write-back (success)
CT08  tmp_repo_dir is cleaned up even when write-back raises
CT09  engine_run_id is taken from engine_run_id_future after pipeline.start
CT10  error status is written to DB when _run_sync raises
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

os.environ.setdefault("ANTCREW_TESTING", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kv_memory(*, dirty: bool, data: dict | None = None):
    """Return a mock InMemoryKVMemory."""
    kv = MagicMock()
    kv.dirty = dirty
    kv.all = MagicMock(return_value=data or {"key": "value"})
    return kv


def _make_mock_task():
    task = MagicMock()
    task.update_state = MagicMock()
    return task


def _base_params(**overrides) -> dict:
    p = {
        "team_name": "DevTeam",
        "effective_request": "build feature X",
        "thread_id": "default",
        "max_cost_usd": None,
        "force_hitl": False,
        "byok_api_key": None,
        "byok_base_url": None,
        "model": "",
        "agent_llm_configs": None,
        "dry_run": False,
        "org_context": None,
        "replay_run_id": None,
        "initial_memory": {},
        "hitl_timeout": None,
        "created_by": "api-key-1",
        "client_label": None,
        "model_overrides": None,
        "write_back": False,
        "repo_url": None,
        "tmp_repo_dir": None,
        "github_installation_id": None,
    }
    p.update(overrides)
    return p


ENGINE_RUN_ID = uuid.uuid4().hex


def _make_fake_result(run_id: str = ENGINE_RUN_ID):
    result = MagicMock()
    result.state = {"_run_id": run_id}
    return result


def _mock_run_sync_factory(kv_out_item=None, run_id: str = ENGINE_RUN_ID):
    """Return a mock _run_sync that fires pipeline.start via the bus and appends kv_out."""
    def _fake_run_sync(*args, **kwargs):
        from antcrew import bus, Event as BusEvent
        # kv_out is the last positional arg (16th, index 15)
        kv_out = args[15] if len(args) > 15 else []
        if kv_out_item is not None:
            kv_out.append(kv_out_item)
        # Fire pipeline.start so _on_pipeline_start sets the future
        ev = BusEvent("pipeline.start", {"team": "DevTeam", "request": "test"}, run_id=run_id)
        bus.emit(ev)
        return _make_fake_result(run_id)
    return _fake_run_sync


# ---------------------------------------------------------------------------
# CT01 — KV memory saved when dirty
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct01_kv_memory_saved_when_dirty():
    kv = _make_kv_memory(dirty=True, data={"counter": 5})
    task = _make_mock_task()
    params = _base_params()

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory(kv_out_item=kv)),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
        patch("app.services.runner._save_team_memory", new_callable=AsyncMock) as mock_save,
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "success"
    mock_save.assert_awaited_once_with("DevTeam", 1, {"counter": 5})


# ---------------------------------------------------------------------------
# CT02 — KV memory NOT saved when kv_out is empty
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct02_kv_memory_not_saved_when_empty():
    task = _make_mock_task()
    params = _base_params()

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory(kv_out_item=None)),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
        patch("app.services.runner._save_team_memory", new_callable=AsyncMock) as mock_save,
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "success"
    mock_save.assert_not_awaited()


# ---------------------------------------------------------------------------
# CT03 — KV memory NOT saved when not dirty
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct03_kv_memory_not_saved_when_clean():
    kv = _make_kv_memory(dirty=False)
    task = _make_mock_task()
    params = _base_params()

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory(kv_out_item=kv)),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
        patch("app.services.runner._save_team_memory", new_callable=AsyncMock) as mock_save,
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "success"
    mock_save.assert_not_awaited()


# ---------------------------------------------------------------------------
# CT04 — write-back calls _do_write_back when dir exists
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct04_write_back_called_when_dir_exists():
    task = _make_mock_task()

    with tempfile.TemporaryDirectory() as tmpdir:
        params = _base_params(
            write_back=True,
            repo_url="https://github.com/example/repo",
            tmp_repo_dir=tmpdir,
            github_installation_id=42,
        )

        with (
            patch("app.tasks.run_tasks._ensure_listener"),
            patch("app.core.listener.set_celery_loop"),
            patch("app.core.channel.PlatformChannel"),
            patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory()),
            patch("app.services.runner._store_result", new_callable=AsyncMock),
            patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
            patch("app.services.runner._save_team_memory", new_callable=AsyncMock),
            patch("app.services.runner._do_write_back", new_callable=AsyncMock) as mock_wb,
        ):
            from app.tasks.run_tasks import _run_async
            result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "success"
    mock_wb.assert_awaited_once()
    call_args = mock_wb.call_args
    assert call_args.kwargs.get("installation_id") == 42
    assert str(call_args.args[1]) == tmpdir


# ---------------------------------------------------------------------------
# CT05 — write-back skips when dir does not exist
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct05_write_back_skips_missing_dir():
    task = _make_mock_task()
    params = _base_params(
        write_back=True,
        repo_url="https://github.com/example/repo",
        tmp_repo_dir="/nonexistent/path/12345",
    )

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory()),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
        patch("app.services.runner._save_team_memory", new_callable=AsyncMock),
        patch("app.services.runner._do_write_back", new_callable=AsyncMock) as mock_wb,
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "success"
    mock_wb.assert_not_awaited()


# ---------------------------------------------------------------------------
# CT06 — write-back skips when write_back=False
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct06_write_back_skips_when_disabled():
    task = _make_mock_task()
    params = _base_params(
        write_back=False,
        repo_url="https://github.com/example/repo",
        tmp_repo_dir="/some/path",
    )

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory()),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
        patch("app.services.runner._save_team_memory", new_callable=AsyncMock),
        patch("app.services.runner._do_write_back", new_callable=AsyncMock) as mock_wb,
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "success"
    mock_wb.assert_not_awaited()


# ---------------------------------------------------------------------------
# CT07 — tmp_repo_dir is deleted after successful write-back
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct07_tmp_dir_deleted_after_write_back():
    task = _make_mock_task()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a sentinel file so we can verify the dir gets cleaned up
        sentinel = Path(tmpdir) / "repo_clone"
        sentinel.mkdir()

        params = _base_params(
            write_back=True,
            repo_url="https://github.com/example/repo",
            tmp_repo_dir=tmpdir,
        )

        with (
            patch("app.tasks.run_tasks._ensure_listener"),
            patch("app.core.listener.set_celery_loop"),
            patch("app.core.channel.PlatformChannel"),
            patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory()),
            patch("app.services.runner._store_result", new_callable=AsyncMock),
            patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
            patch("app.services.runner._save_team_memory", new_callable=AsyncMock),
            patch("app.services.runner._do_write_back", new_callable=AsyncMock),
        ):
            from app.tasks.run_tasks import _run_async
            await _run_async(task, workspace_id=1, p=params)

        assert not Path(tmpdir).exists(), "tmp_repo_dir should be deleted after write-back"


# ---------------------------------------------------------------------------
# CT08 — tmp_repo_dir cleaned up even when write-back raises
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct08_tmp_dir_deleted_on_write_back_error():
    task = _make_mock_task()

    with tempfile.TemporaryDirectory() as tmpdir:
        params = _base_params(
            write_back=True,
            repo_url="https://github.com/example/repo",
            tmp_repo_dir=tmpdir,
        )

        async def _wb_raises(*args, **kwargs):
            raise RuntimeError("git push failed")

        with (
            patch("app.tasks.run_tasks._ensure_listener"),
            patch("app.core.listener.set_celery_loop"),
            patch("app.core.channel.PlatformChannel"),
            patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory()),
            patch("app.services.runner._store_result", new_callable=AsyncMock),
            patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
            patch("app.services.runner._save_team_memory", new_callable=AsyncMock),
            patch("app.services.runner._do_write_back", side_effect=_wb_raises),
        ):
            from app.tasks.run_tasks import _run_async
            result = await _run_async(task, workspace_id=1, p=params)

        # Result still success — write-back errors are logged, not fatal
        assert result["status"] == "success"
        assert not Path(tmpdir).exists(), "tmp_repo_dir should be deleted even on write-back error"


# ---------------------------------------------------------------------------
# CT09 — engine_run_id taken from pipeline.start event
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct09_run_id_from_pipeline_start():
    custom_run_id = "custom_run_" + uuid.uuid4().hex
    task = _make_mock_task()
    params = _base_params()

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=_mock_run_sync_factory(run_id=custom_run_id)),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock) as mock_attr,
        patch("app.services.runner._save_team_memory", new_callable=AsyncMock),
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["run_id"] == custom_run_id
    mock_attr.assert_awaited_once()
    assert mock_attr.call_args.args[0] == custom_run_id


# ---------------------------------------------------------------------------
# CT10 — error status written to DB when _run_sync raises
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ct10_error_status_on_run_sync_failure():
    task = _make_mock_task()
    params = _base_params()

    mock_run = MagicMock()
    mock_run.status = "running"

    with (
        patch("app.tasks.run_tasks._ensure_listener"),
        patch("app.core.listener.set_celery_loop"),
        patch("app.core.channel.PlatformChannel"),
        patch("app.services.runner._run_sync", side_effect=RuntimeError("LLM timeout")),
        patch("app.services.runner._store_result", new_callable=AsyncMock),
        patch("app.services.runner._set_run_attribution", new_callable=AsyncMock),
    ):
        from app.tasks.run_tasks import _run_async
        result = await _run_async(task, workspace_id=1, p=params)

    assert result["status"] == "error"
    # run_id is empty because pipeline.start never fired
    assert result["run_id"] == ""
