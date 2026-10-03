"""Background team runner — dispatches antcrew pipelines from the API.

Flow:
  POST /run → dispatch() → _run_sync() in thread pool → _store_result()

HITL flow: if force_hitl=True (from `POST /run { "hitl": true }`) OR any agent has
approval_required=True, dispatch injects PlatformChannel and calls run_interactive().
The channel blocks the executor thread on a concurrent.futures.Future until
POST /reviews/:id resolves it.

Module structure:
  runner_core.py     — executor, team registry, repo helpers, DB write-backs
  runner_pipeline.py — visual pipeline, custom pipeline, quick pipeline dispatch
  runner.py          — main dispatch() for pre-built teams + re-exports
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
from typing import Optional

from antcrew import WebhookSink, bus
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.channel import PlatformChannel
from app.core.database import engine
from app.services.runner_base import (
    _check_workspace_budget,
    _get_budget_lock,
    _mark_workspace_budget_status,
)
from app.services.runner_core import (
    _DISPATCH_TIMEOUT,
    _do_write_back,  # also used by run_tasks re-export
    _executor,
    _get_workspace_semaphore,
    _inject_repo_context,
    _load_team_memory,
    _make_agent_channel,
    _maybe_snapshot_team,
    _persist_agent_events,
    _run_sync,
    _save_team_memory,
    _set_run_attribution,
    _store_result,
    _sync_tickets_after_run,
)

log = logging.getLogger(__name__)


async def dispatch(
    team_name: str,
    request: str,
    thread_id: str = "default",
    *,
    # ── Dispatch path guide ──────────────────────────────────────────────────
    # dispatch()          → Pre-built teams (DevTeam, FullStackTeam, …). The
    #                       default for any standard team run.
    # dispatch_engine()   → antcrew-engine EngineLoop with CapabilityDescriptors.
    #                       Use when you need capability-level condition guards.
    # dispatch_custom()   → TemplateAgent + Python-defined step list. Use when
    #                       you need a custom agent graph without writing a team.
    # dispatch_pipeline() → Visual Pipeline Builder workflow JSON. Driven by the
    #                       UI drag-and-drop editor; agents defined in JSON, not code.
    # ────────────────────────────────────────────────────────────────────────
    model: str = "",
    model_overrides: Optional[dict] = None,
    max_cost_usd: Optional[float] = None,
    created_by: Optional[str] = None,
    workspace_id: Optional[int] = None,
    force_hitl: bool = False,
    repo_url: Optional[str] = None,
    repo_token: Optional[str] = None,
    client_label: Optional[str] = None,
    write_back: bool = False,
    dry_run: bool = False,
    org_context: Optional[dict] = None,
    replay_run_id: Optional[str] = None,
) -> Optional[str]:
    """Start a team run in the background. Returns run_id once pipeline.start fires.

    force_hitl=True injects PlatformChannel into ALL agents for this run,
    regardless of their approval_required setting.

    repo_url, if set, is cloned depth=1 and its file tree + source files are
    prepended to the request so agents have codebase context. repo_token can be
    a GitHub/GitLab personal access token for private repos (injected into the
    HTTPS URL — never stored or logged).

    write_back=True writes generated artifacts back to the cloned repo on a new
    branch (antcrew/wb-<run_id[:8]>) and pushes it to origin. Only applies when
    repo_url is also set. The temp clone dir is deleted after the push (or on
    error); without write_back it is deleted immediately after the run.

    dry_run=True suppresses write-back and sandbox side effects but runs LLMs normally.
    org_context: dict pre-populated into ProjectKB before team.run().
    replay_run_id: injects artifacts from a past ChromaMemory run as context for BA/PM.
    """
    if workspace_id is not None:
        async with _get_budget_lock(workspace_id):
            await _check_workspace_budget(workspace_id)

    # Look up per-workspace HITL timeout, BYOK key, and GitHub installation in one DB call.
    _hitl_timeout: Optional[float] = None
    _byok_api_key: Optional[str] = None
    _byok_base_url: Optional[str] = None
    _github_installation_id: Optional[int] = None
    _agent_llm_configs: dict = {}
    _cli_working_dir: Optional[str] = None
    _doc_mgr = None
    if workspace_id is not None:
        from sqlmodel import select as _sel

        from app.models.run import Workspace as _WS
        async with AsyncSession(engine, expire_on_commit=False) as _sess:
            _ws = (await _sess.exec(_sel(_WS).where(_WS.id == workspace_id))).first()
            if _ws:
                if _ws.hitl_timeout_s is not None:
                    _hitl_timeout = _ws.hitl_timeout_s
                # Resolve effective model: run-level > workspace default > platform default > "claude"
                _ws_agent_models = getattr(_ws, "agent_models", None) or {}
                if not model:
                    model = _ws_agent_models.get("default", "") or ""
                if not model:
                    from app.models.admin import PlatformConfig as _PC
                    _pc = await _sess.get(_PC, 1)
                    _platform_defaults = (_pc.default_agent_models or {}) if _pc else {}
                    model = _platform_defaults.get("default", "") or ""
                from app.services.runner_base import resolve_workspace_llm_config
                _byok_api_key, _byok_base_url = await resolve_workspace_llm_config(_sess, _ws, model or "claude")
                _cli_working_dir = getattr(_ws, "cli_working_dir", None)
                try:
                    from app.api.workspaces_docs import build_doc_manager_for_workspace as _bdm
                    _doc_mgr = _bdm(_ws)
                except Exception as _docs_exc:
                    log.debug("runner: could not build doc manager: %s", _docs_exc)
                # Apply cost routing policy as the lowest-priority override layer
                _routing_policy = getattr(_ws, "cost_routing_policy", "none") or "none"
                if _routing_policy != "none":
                    from app.models.admin import PlatformConfig as _PC2
                    _pc2 = await _sess.get(_PC2, 1)
                    _tiers: dict = {
                        "cheap":    getattr(_pc2, "tier_cheap_model", "groq:llama-3.3-70b-versatile") if _pc2 else "groq:llama-3.3-70b-versatile",
                        "standard": getattr(_pc2, "tier_standard_model", "claude:claude-sonnet-5") if _pc2 else "claude:claude-sonnet-5",
                        "premium":  getattr(_pc2, "tier_premium_model", "claude:claude-opus-5") if _pc2 else "claude:claude-opus-5",
                    }
                    from app.services.cost_router import (
                        build_routing_overrides as _build_routing,
                    )
                    _routing_overrides = _build_routing(team_name, _routing_policy, _tiers)
                    # Routing is lowest priority: explicit workspace entries override it
                    _merged_agent_models = {**_routing_overrides, **_ws_agent_models}
                    if not model and _routing_overrides.get("default"):
                        model = _routing_overrides["default"]
                    _ws_agent_models = _merged_agent_models

                # Resolve per-agent overrides: workspace non-default entries + run-level overrides
                _effective_agent_overrides: dict = {
                    k: v for k, v in _ws_agent_models.items() if k != "default" and v
                }
                for _k, _v in (model_overrides or {}).items():
                    if _v:
                        _effective_agent_overrides[_k] = _v
                for _agent_name, _agent_model in _effective_agent_overrides.items():
                    _akey, _aurl = await resolve_workspace_llm_config(_sess, _ws, _agent_model)
                    _agent_llm_configs[_agent_name] = (_agent_model, _akey, _aurl)
            # Look up GitHub App installation for this workspace (used by write-back)
            if write_back and repo_url:
                try:
                    from app.models.github_app import GitHubInstallation as _GHI
                    _inst = (await _sess.exec(
                        _sel(_GHI).where(_GHI.workspace_id == workspace_id)
                    )).first()
                    if _inst:
                        _github_installation_id = _inst.installation_id
                except Exception as _exc:
                    log.debug("runner: no GitHub installation for workspace %s: %s", workspace_id, _exc)

    # If write-back is requested and a GitHub installation is linked, get an installation
    # token now (before cloning) so it can authenticate both the clone and the push.
    if _github_installation_id is not None and repo_url:
        try:
            from app.services.github_tokens import (
                get_installation_token as _get_inst_token,
            )
            _inst_token = await _get_inst_token(_github_installation_id)
            repo_token = _inst_token
            log.debug("runner: using GitHub App installation token for repo clone/push")
        except Exception as _exc:
            log.warning("runner: could not get GitHub installation token (%s) — falling back to repo_token", _exc)  # nosemgrep
            _github_installation_id = None  # disable PR creation if token exchange fails

    # Clone repo and inject context before dispatching to the thread pool.
    _tmp_repo_dir: Optional[str] = None
    effective_request = request
    if repo_url:
        try:
            _tmp_path, effective_request = await _inject_repo_context(
                repo_url, request, repo_token=repo_token
            )
            _tmp_repo_dir = str(_tmp_path)
            log.info("runner: injected repo context from %s (%d chars)", repo_url, len(effective_request))
        except Exception as exc:
            log.warning("runner: repo clone failed for %s — running without context: %s", repo_url, exc)

    # Resolve max_messages limit: team-preset level > workspace level > None.
    # Set the context var here so it is inherited by the thread pool context.
    _max_messages: Optional[int] = None
    if workspace_id is not None:
        try:
            from sqlmodel import select as _sel_mm

            from app.models.run import RunPreset as _RP_mm
            from app.models.run import Workspace as _WS_mm
            async with AsyncSession(engine, expire_on_commit=False) as _mm_sess:
                _ws_mm = (await _mm_sess.exec(_sel_mm(_WS_mm).where(_WS_mm.id == workspace_id))).first()
                if _ws_mm:
                    _max_messages = getattr(_ws_mm, "max_messages", None)
                # Team-preset entry for this team overrides workspace value
                _rp_mm = (await _mm_sess.exec(
                    _sel_mm(_RP_mm)
                    .where(_RP_mm.workspace_id == workspace_id)
                    .where(_RP_mm.team == team_name)
                    .limit(1)
                )).first()
                if _rp_mm and getattr(_rp_mm, "max_messages", None) is not None:
                    _max_messages = _rp_mm.max_messages
        except Exception as _mm_exc:
            log.debug("runner: max_messages lookup failed: %s", _mm_exc)
    if _max_messages is not None:
        from antcrew.core.state import _max_messages_var as _mmv
        _mmv.set(_max_messages)

    # Load team KV memory before running so agents start with prior context
    _initial_memory: dict = await _load_team_memory(team_name, workspace_id)
    _kv_out: list = []  # InMemoryKVMemory will be appended here by _run_sync

    loop = asyncio.get_running_loop()
    run_id_future: asyncio.Future[str] = loop.create_future()
    platform_channel = PlatformChannel(timeout_s=_hitl_timeout)
    _per_agent_channels: list = []
    _agent_end_events: list[dict] = []

    # Outbound webhook sink: activated when workspace has enabled WebhookConfig rows
    _webhook_sink: Optional[WebhookSink] = None
    if workspace_id is not None:
        try:
            from sqlmodel import select as _sel_wh

            from app.models.webhook import WebhookConfig as _WC
            async with AsyncSession(engine, expire_on_commit=False) as _wh_sess:
                _has_hooks = bool((await _wh_sess.exec(
                    _sel_wh(_WC).where(_WC.workspace_id == workspace_id, _WC.enabled == True)  # noqa: E712
                )).first())
            if _has_hooks:
                _webhook_sink = WebhookSink()
        except Exception as _wh_exc:
            log.debug("runner: webhook sink check failed: %s", _wh_exc)

    def _on_agent_end(event) -> None:
        try:
            payload = event.data if hasattr(event, "data") else (event if isinstance(event, dict) else {})
            _agent_end_events.append(payload)
        except Exception:
            pass

    def _on_pipeline_start(event) -> None:
        if event.run_id and not run_id_future.done():
            platform_channel.set_run_id(event.run_id)
            for _ch in _per_agent_channels:
                _ch.set_run_id(event.run_id)
            if _webhook_sink is not None:
                _webhook_sink.run_id = event.run_id
            loop.call_soon_threadsafe(run_id_future.set_result, event.run_id)

    bus.subscribe("pipeline.start", _on_pipeline_start)
    bus.subscribe("agent.end", _on_agent_end)
    if _webhook_sink is not None:
        bus.subscribe("*", _webhook_sink.handle)

    _ws_semaphore = _get_workspace_semaphore(workspace_id)

    async def _bg() -> None:
        try:
            fn = functools.partial(
                _run_sync, team_name, effective_request, thread_id,
                max_cost_usd, platform_channel, force_hitl, _byok_api_key, _byok_base_url, model,
                _agent_llm_configs or None, dry_run, org_context, replay_run_id, _per_agent_channels,
                _initial_memory, _kv_out, _cli_working_dir, _doc_mgr,
            )
            if _ws_semaphore is not None:
                async with _ws_semaphore:
                    result = await loop.run_in_executor(_executor, fn)
            else:
                result = await loop.run_in_executor(_executor, fn)
            await _store_result(result)
            _run_id_val = run_id_future.result() if run_id_future.done() else ""
            # Persist KV memory snapshot if any agent wrote to it during this run
            if _kv_out and _kv_out[0].dirty:
                await _save_team_memory(team_name, workspace_id, _kv_out[0].all())
            if _run_id_val and _agent_end_events:
                await _persist_agent_events(_run_id_val, _agent_end_events)
                await _maybe_snapshot_team(team_name, workspace_id, _agent_end_events)
                await _sync_tickets_after_run(_run_id_val, team_name, workspace_id)
            if workspace_id is not None:
                await _mark_workspace_budget_status(workspace_id)
            if write_back and _tmp_repo_dir is not None:
                from pathlib import Path as _Path
                _tmp_path_obj = _Path(_tmp_repo_dir)
                _run_id = run_id_future.result() if run_id_future.done() else ""
                if not _run_id:
                    if isinstance(result, dict):
                        _run_id = result.get("_run_id") or ""
                    elif hasattr(result, "state"):
                        _run_id = (result.state or {}).get("_run_id") or ""
                try:
                    await _do_write_back(
                        result, _tmp_path_obj, _run_id, repo_url or "",
                        installation_id=_github_installation_id,
                    )
                except Exception as exc:
                    log.error("runner: write-back failed for run %s: %s", _run_id, exc)
                    shutil.rmtree(_tmp_repo_dir, ignore_errors=True)
        except Exception as exc:
            log.error("runner: %s failed: %s", team_name, exc)
            if not run_id_future.done():
                loop.call_soon_threadsafe(run_id_future.set_result, None)
        finally:
            bus.unsubscribe("pipeline.start", _on_pipeline_start)
            bus.unsubscribe("agent.end", _on_agent_end)
            if _webhook_sink is not None:
                bus.unsubscribe("*", _webhook_sink.handle)
                _wh_run_id = _webhook_sink.run_id or (
                    run_id_future.result() if run_id_future.done() else ""
                )
                _wh_events = _webhook_sink.drain()
                if _wh_run_id and _wh_events:
                    try:
                        from app.services.webhook import fire_event_webhooks as _fire_wh
                        from app.services.webhook import (
                            notify_new_delivery as _notify_wh,
                        )
                        async with AsyncSession(engine, expire_on_commit=False) as _wh_s:
                            _wh_total = 0
                            for _ev_type, _ev_payload in _wh_events:
                                _wh_total += await _fire_wh(
                                    _wh_s,
                                    workspace_id=workspace_id,
                                    event_type=_ev_type,
                                    run_id=_wh_run_id,
                                    payload=_ev_payload,
                                )
                            if _wh_total:
                                await _wh_s.commit()
                                _notify_wh()
                    except Exception as _wh_exc:
                        log.warning("runner: webhook delivery failed: %s", _wh_exc)
            if _tmp_repo_dir is not None and not write_back:
                shutil.rmtree(_tmp_repo_dir, ignore_errors=True)
            if workspace_id is not None:
                try:
                    _rid = run_id_future.result() if run_id_future.done() else None
                    if _rid:
                        from app.api.stream import deregister_run as _deregister_run
                        _deregister_run(_rid)
                except Exception:
                    pass

    # ── Celery path ─────────────────────────────────────────────────────────
    # When CELERY_BROKER_URL is set, offload the run to a Celery worker instead
    # of using the in-process ThreadPoolExecutor.  All params are serialised here
    # (in the API process) and the worker recreates PlatformChannel from DB.
    # Write-back is skipped in Celery mode (tmp dir was cleaned up above).
    if os.environ.get("CELERY_BROKER_URL", ""):
        # Clean up tmp_repo_dir only when NOT doing write-back.
        # For write-back, pass the path to the worker (single-host deployments).
        if _tmp_repo_dir is not None and not (write_back and repo_url):
            import shutil as _shutil_celery
            _shutil_celery.rmtree(_tmp_repo_dir, ignore_errors=True)

        bus.unsubscribe("pipeline.start", _on_pipeline_start)
        bus.unsubscribe("agent.end", _on_agent_end)
        if _webhook_sink is not None:
            bus.unsubscribe("*", _webhook_sink.handle)

        _serialised = {
            "team_name": team_name,
            "effective_request": effective_request,
            "thread_id": thread_id,
            "max_cost_usd": max_cost_usd,
            "force_hitl": force_hitl,
            "byok_api_key": _byok_api_key,
            "byok_base_url": _byok_base_url,
            "model": model,
            # tuples → lists so JSON serialisation works; _run_sync unpacks as list
            "agent_llm_configs": {
                k: list(v) for k, v in (_agent_llm_configs or {}).items()
            },
            "dry_run": dry_run,
            "org_context": org_context,
            "replay_run_id": replay_run_id,
            "initial_memory": _initial_memory,
            "hitl_timeout": _hitl_timeout,
            "created_by": created_by,
            "client_label": client_label,
            "model_overrides": model_overrides,
            # Write-back: worker uses these to push artifacts to the repo.
            # tmp_repo_dir only works when API and workers share the filesystem.
            "write_back": write_back,
            "repo_url": repo_url,
            "tmp_repo_dir": _tmp_repo_dir if (_tmp_repo_dir and write_back and repo_url) else None,
            "github_installation_id": _github_installation_id,
        }
        try:
            from app.tasks.run_tasks import run_pipeline as _celery_task
            _celery_job = _celery_task.delay(workspace_id, _serialised)
        except Exception as _enq_exc:
            log.error("runner: celery enqueue failed for %s — falling through to in-process: %s", team_name, _enq_exc)
        else:
            # Poll Celery task state for run_id (set via update_state when pipeline.start fires).
            import time as _time
            _poll_start = _time.monotonic()
            while _time.monotonic() - _poll_start < _DISPATCH_TIMEOUT:
                await asyncio.sleep(0.25)
                try:
                    from celery.result import AsyncResult as _AR  # type: ignore[import]
                    _ar = _AR(_celery_job.id)
                    if _ar.state == "PROGRESS":
                        _run_id = (_ar.info or {}).get("run_id", "")
                        if _run_id:
                            await _set_run_attribution(_run_id, created_by, workspace_id, client_label, model_overrides)
                            if workspace_id is not None:
                                from app.api.stream import register_run as _register_run
                                _register_run(_run_id, workspace_id)
                            return _run_id
                    elif _ar.state == "SUCCESS":
                        _run_id = (_ar.result or {}).get("run_id", "") if isinstance(_ar.result, dict) else ""
                        if _run_id:
                            await _set_run_attribution(_run_id, created_by, workspace_id, client_label, model_overrides)
                        return _run_id or None
                    elif _ar.state == "FAILURE":
                        log.error("runner: celery task %s failed for team %s", _celery_job.id, team_name)
                        return None
                except Exception as _poll_exc:
                    log.debug("runner: celery result poll error: %s", _poll_exc)
            log.warning("runner: celery pipeline.start not received within %.0f s for %s", _DISPATCH_TIMEOUT, team_name)
            return None
        # Enqueue failed — re-register handlers and fall through to in-process path.
        bus.subscribe("pipeline.start", _on_pipeline_start)
        bus.subscribe("agent.end", _on_agent_end)
        if _webhook_sink is not None:
            bus.subscribe("*", _webhook_sink.handle)
    # ── End Celery path ──────────────────────────────────────────────────────

    asyncio.ensure_future(_bg())

    try:
        run_id = await asyncio.wait_for(asyncio.shield(run_id_future), timeout=_DISPATCH_TIMEOUT)
        if (created_by or workspace_id is not None) and run_id:
            # Await attribution before returning so workspace_id is set before the
            # 202 response reaches the client — eliminates the race on GET /runs/.
            await _set_run_attribution(run_id, created_by, workspace_id, client_label, model_overrides)
        if run_id and workspace_id is not None:
            from app.api.stream import register_run as _register_run
            _register_run(run_id, workspace_id)
        return run_id
    except asyncio.TimeoutError:
        log.warning("runner: pipeline.start not received within %.0f s for %s", _DISPATCH_TIMEOUT, team_name)
        return None


# ── Re-exports for backward compatibility ────────────────────────────────────
# All names from runner_core and runner_pipeline that tests and API files import
# from app.services.runner — keep them importable here without any caller changes.
from app.services.runner_core import (  # noqa: E402, F401
    _ALLOWED_TEAM_PREFIXES,
    _MAX_WORKERS,
    _REPO_CLONE_TIMEOUT_S,
    _REPO_MAX_CONTEXT_CHARS,
    _REPO_MAX_FILE_CHARS,
    _REPO_MAX_FILES,
    _REPO_SKIP,
    _REPO_SRC_EXTS,
    _TEAM_REGISTRY,
    _WORKSPACE_MAX_CONCURRENT,
    ALL_PIPELINE_TYPES,
    AVAILABLE_TEAMS,
    _advance_sprint_after_run,
    _allowed_module_path,
    _build_repo_context,
    _build_team_registry,
    _extract_github_repo_name,
    _make_team,
    _save_to_dead_letter,
    _serialize_state,
    _workspace_semaphores,
    make_team,
    shutdown,
)
from app.services.runner_pipeline import (  # noqa: E402, F401
    _resolve_node_channel,
    _run_custom_sync,
    _run_interactive_pipeline,
    _run_pipeline_sync,
    _run_quick_sync,
    _validate_custom_dag,
    dispatch_custom,
    dispatch_pipeline,
    dispatch_quick,
)
