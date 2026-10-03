"""Celery tasks for durable pipeline runs.

When CELERY_BROKER_URL is set, dispatch() enqueues run_pipeline here instead of
using the in-process ThreadPoolExecutor.  The worker:
  1. Registers its event loop with the platform listener (so DB event writes
     from the executor thread still reach the shared DB via call_soon_threadsafe).
  2. Recreates PlatformChannel (DB-backed by default — works cross-process).
  3. Calls _run_sync() in an executor thread (same as the API process does).
  4. Reports run_id via task state when pipeline.start fires so the API can return
     it to the client before the run completes.
  5. Persists KV team memory when agents wrote to it during the run.
  6. Calls _do_write_back() when write_back=True and tmp_repo_dir exists on disk.
  7. Calls _store_result() after the run finishes.

Start the worker:
    celery -A app.celery_app worker --loglevel=info --concurrency=4
"""
from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# One-time listener startup guard per worker process.
_listener_once = threading.Lock()
_listener_started = False


def _ensure_listener() -> None:
    global _listener_started
    if _listener_started:
        return
    with _listener_once:
        if not _listener_started:
            from app.core.listener import start_listening
            start_listening()
            _listener_started = True


def _get_celery_app():
    from app.celery_app import celery_app
    return celery_app


def _task(*args, **kwargs):
    """Decorator that registers a task only when Celery is importable."""
    try:
        return _get_celery_app().task(*args, **kwargs)
    except Exception:
        def _noop(fn):
            return fn
        return _noop


@_task(bind=True, name="antcrew.run_pipeline", max_retries=0)
def run_pipeline(self, workspace_id: int, serialised_params: dict) -> dict:
    """Execute a pipeline run inside a Celery worker.

    Args:
        workspace_id: Workspace the run belongs to.
        serialised_params: JSON-serialisable dict of all dispatch() params resolved
            in the API process. Keys include:
              team_name, effective_request, thread_id, max_cost_usd, force_hitl,
              byok_api_key, byok_base_url, model, agent_llm_configs (lists),
              dry_run, org_context, replay_run_id, initial_memory, hitl_timeout,
              created_by, client_label, model_overrides,
              write_back (bool), repo_url, tmp_repo_dir, github_installation_id.

    Returns:
        {"run_id": engine_run_id, "status": "success"|"error"}
    """
    return asyncio.run(_run_async(self, workspace_id, serialised_params))


async def _run_async(task, workspace_id: int, p: dict) -> dict:
    from antcrew import bus

    from app.core.channel import PlatformChannel
    from app.core.listener import set_celery_loop
    from app.services.runner import _run_sync, _set_run_attribution, _store_result

    # Register this event loop with the listener so events fired from the
    # executor thread are still persisted to DB via call_soon_threadsafe.
    loop = asyncio.get_running_loop()
    set_celery_loop(loop)
    _ensure_listener()

    engine_run_id_future: asyncio.Future[str] = loop.create_future()
    platform_channel = PlatformChannel(timeout_s=p.get("hitl_timeout"))
    _per_agent_channels: list = []
    _kv_out: list = []  # InMemoryKVMemory appended by _run_sync if agents use KV memory

    def _on_pipeline_start(event) -> None:
        if not event.run_id or engine_run_id_future.done():
            return
        platform_channel.set_run_id(event.run_id)
        for ch in _per_agent_channels:
            ch.set_run_id(event.run_id)
        loop.call_soon_threadsafe(engine_run_id_future.set_result, event.run_id)
        # Publish run_id via Celery task state so the API poller can return early.
        try:
            task.update_state(
                state="PROGRESS",
                meta={"run_id": event.run_id, "status": "running"},
            )
        except Exception:
            pass

    bus.subscribe("pipeline.start", _on_pipeline_start)
    result = None
    engine_run_id = ""
    try:
        # agent_llm_configs: serialised as {name: [model, key, url]} — list unpacks fine
        _agent_llm_configs = p.get("agent_llm_configs") or None

        fn = functools.partial(
            _run_sync,
            p["team_name"],
            p["effective_request"],
            p.get("thread_id", "default"),
            p.get("max_cost_usd"),
            platform_channel,
            p.get("force_hitl", False),
            p.get("byok_api_key"),
            p.get("byok_base_url"),
            p.get("model", ""),
            _agent_llm_configs,
            p.get("dry_run", False),
            p.get("org_context"),
            p.get("replay_run_id"),
            _per_agent_channels,
            p.get("initial_memory") or {},
            _kv_out,
        )
        result = await loop.run_in_executor(None, fn)

        # Yield so call_soon_threadsafe-scheduled listener coroutines can drain.
        await asyncio.sleep(0)

        await _store_result(result)

        engine_run_id = (
            engine_run_id_future.result() if engine_run_id_future.done() else ""
        )

        # Persist KV team memory if any agent wrote to it during this run.
        if _kv_out and getattr(_kv_out[0], "dirty", False):
            try:
                from app.services.runner import _save_team_memory
                await _save_team_memory(p["team_name"], workspace_id, _kv_out[0].all())
                log.debug("run_pipeline: KV memory saved for team=%s ws=%s", p["team_name"], workspace_id)
            except Exception as _kv_exc:
                log.warning("run_pipeline: KV memory save failed: %s", _kv_exc)

        if engine_run_id:
            await _set_run_attribution(
                engine_run_id,
                p.get("created_by"),
                workspace_id,
                p.get("client_label"),
                p.get("model_overrides"),
            )

        # Write-back: push generated artifacts to the cloned repo (single-host only).
        if p.get("write_back") and p.get("repo_url") and engine_run_id and result is not None:
            _tmp_dir_str = p.get("tmp_repo_dir", "")
            if _tmp_dir_str:
                _tmp_dir = Path(_tmp_dir_str)
                if _tmp_dir.exists():
                    try:
                        from app.services.runner import _do_write_back
                        await _do_write_back(
                            result,
                            _tmp_dir,
                            engine_run_id,
                            p["repo_url"],
                            installation_id=p.get("github_installation_id"),
                        )
                    except Exception as _wb_exc:
                        log.error("run_pipeline: write-back failed for run %s: %s", engine_run_id, _wb_exc)
                    finally:
                        shutil.rmtree(_tmp_dir, ignore_errors=True)
                else:
                    log.warning(
                        "run_pipeline: write_back requested but tmp_repo_dir %s not found "
                        "(worker may be on a different host from the API process)",
                        _tmp_dir_str,
                    )
            else:
                log.warning("run_pipeline: write_back=True but tmp_repo_dir not in serialised_params")

        return {"run_id": engine_run_id, "status": "success"}

    except Exception:
        log.exception("run_pipeline Celery task failed (workspace=%s)", workspace_id)
        engine_run_id = (
            engine_run_id_future.result() if engine_run_id_future.done() else ""
        )
        if engine_run_id:
            from datetime import datetime, timezone

            from sqlmodel import select
            from sqlmodel.ext.asyncio.session import AsyncSession

            from app.core.database import engine as _db_engine
            from app.models.run import Run
            try:
                async with AsyncSession(_db_engine, expire_on_commit=False) as session:
                    run = (
                        await session.exec(select(Run).where(Run.run_id == engine_run_id))
                    ).first()
                    if run and run.status == "running":
                        run.status = "error"
                        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        session.add(run)
                        await session.commit()
            except Exception as _mark_exc:
                log.error(
                    "run_pipeline: CRITICAL — cannot mark run %s as error in DB: %s. "
                    "Run may appear stuck in 'running' state.",
                    engine_run_id, _mark_exc,
                )
        return {"run_id": engine_run_id, "status": "error"}

    finally:
        bus.unsubscribe("pipeline.start", _on_pipeline_start)
