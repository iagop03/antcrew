"""Execution core — thread pool, team registry, repo helpers, DB write-backs.

Imported by runner.py (main dispatch) and run_tasks.py (Celery workers).
Does NOT import from runner.py or runner_pipeline.py at module level.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.channel import PlatformChannel
from app.core.database import engine
from app.services.runs import upsert_tickets_from_run

log = logging.getLogger(__name__)

# Module path prefixes allowed in ANTCREW_TEAMS.  Blocks arbitrary module import
# if the env var is compromised (CI/CD breach, misconfigured secrets management).
# Extend at deploy time with ANTCREW_TEAMS_ALLOWED_PREFIXES=myorg.,internal.
_ALLOWED_TEAM_PREFIXES: tuple[str, ...] = ("antcrew.", "app.")


def _allowed_module_path(module_path: str) -> bool:
    extra = tuple(
        p.strip()
        for p in os.environ.get("ANTCREW_TEAMS_ALLOWED_PREFIXES", "").split(",")
        if p.strip()
    )
    return module_path.startswith(_ALLOWED_TEAM_PREFIXES + extra)


_MAX_WORKERS = int(os.environ.get("ANTCREW_WORKERS", "4"))
_DISPATCH_TIMEOUT = float(os.environ.get("ANTCREW_DISPATCH_TIMEOUT", "10"))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="antcrew-runner")

# Per-workspace concurrency limit — prevents one workspace from consuming all thread-pool
# workers and starving others (workspace starvation).  None = unlimited (open mode / no WS).
_WORKSPACE_MAX_CONCURRENT = int(os.environ.get("ANTCREW_WORKSPACE_MAX_CONCURRENT", "2"))
_workspace_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_workspace_semaphore(workspace_id: Optional[int]) -> Optional[asyncio.Semaphore]:
    """Return a per-workspace Semaphore, creating it on first access.

    Returns None for open-mode requests (workspace_id is None) — those are
    unrestricted.  All other workspaces share a semaphore with the same limit
    so no single workspace can hold more than ANTCREW_WORKSPACE_MAX_CONCURRENT
    slots in the global thread pool simultaneously.

    Reads ANTCREW_WORKSPACE_MAX_CONCURRENT at semaphore-creation time so that
    environment overrides (e.g. monkeypatching in tests) are picked up for new
    workspace_ids without requiring a full module reload.
    """
    if workspace_id is None:
        return None
    if workspace_id not in _workspace_semaphores:
        max_concurrent = int(os.environ.get("ANTCREW_WORKSPACE_MAX_CONCURRENT", "2"))
        _workspace_semaphores[workspace_id] = asyncio.Semaphore(max_concurrent)
    return _workspace_semaphores[workspace_id]


def _build_team_registry() -> dict[str, tuple[str, str]]:
    """Merge built-in teams with any custom teams from ANTCREW_TEAMS env var.

    ANTCREW_TEAMS format (comma-separated): "my.module:MyTeam,other.module:OtherTeam"
    Custom teams shadow built-ins with the same name.

    CustomTeam is intentionally excluded from the default registry — it requires
    Python-level agent composition and is only useful when configured via ANTCREW_TEAMS.
    """
    registry: dict[str, tuple[str, str]] = {
        "DevTeam":       ("antcrew.teams.dev_team",        "DevTeam"),
        "FullStackTeam": ("antcrew.teams.fullstack_team",  "FullStackTeam"),
        "ResearchTeam":  ("antcrew.teams.research_team",   "ResearchTeam"),
        "ContentTeam":   ("antcrew.teams.content_team",    "ContentTeam"),
        "FeatureTeam":   ("antcrew.agents.feature_agent",  "FeatureTeam"),
    }
    extra = os.environ.get("ANTCREW_TEAMS", "").strip()
    for entry in extra.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            module_path, class_name = entry.rsplit(":", 1)
            module_path = module_path.strip()
            class_name = class_name.strip()
            if not _allowed_module_path(module_path):
                log.warning(
                    "runner: ANTCREW_TEAMS entry %r rejected — module path %r is not in allowed "
                    "prefixes %r. Set ANTCREW_TEAMS_ALLOWED_PREFIXES=<your.prefix.> to allow it.",
                    entry, module_path, _ALLOWED_TEAM_PREFIXES,
                )
                continue
            registry[class_name] = (module_path, class_name)
        except ValueError:
            log.warning("runner: invalid ANTCREW_TEAMS entry %r — expected 'module.path:ClassName'", entry)
    return registry


_TEAM_REGISTRY = _build_team_registry()
AVAILABLE_TEAMS = list(_TEAM_REGISTRY)
# Includes "custom" (POST /run/pipeline) for discoverability in GET /run/teams
ALL_PIPELINE_TYPES = AVAILABLE_TEAMS + ["custom", "engine"]


def _make_team(
    team_name: str,
    max_cost_usd: Optional[float] = None,
    model: str = "",
    byok_api_key: Optional[str] = None,
    byok_base_url: Optional[str] = None,
    trace_log=None,
    cli_working_dir: Optional[str] = None,
):
    if team_name == "custom":
        raise ValueError(
            "Use POST /run/pipeline (dispatch_custom) for custom pipelines — "
            "'custom' cannot be dispatched via _make_team."
        )
    if team_name not in _TEAM_REGISTRY:
        raise ValueError(f"Unknown team {team_name!r}. Available: {AVAILABLE_TEAMS}")
    module_path, class_name = _TEAM_REGISTRY[team_name]
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    kwargs: dict = {}
    if model or byok_api_key or byok_base_url or cli_working_dir:
        from antcrew import build_llm as _build_llm
        _llm_kw: dict = {}
        if byok_api_key is not None:
            _llm_kw["api_key"] = byok_api_key
        if byok_base_url is not None:
            _llm_kw["base_url"] = byok_base_url
        if cli_working_dir:
            _llm_kw["extra_body"] = {"working_directory": cli_working_dir}
        llm = _build_llm(model or "claude", **_llm_kw)
        if max_cost_usd is not None:
            llm.max_cost_usd = max_cost_usd
        kwargs["llm"] = llm
    elif max_cost_usd is not None:
        kwargs["max_cost_usd"] = max_cost_usd
    if trace_log is not None:
        kwargs["trace_log"] = trace_log
    try:
        return cls(**kwargs)
    except TypeError as exc:
        # Pop unsupported kwargs and retry — order: trace_log first (silent), llm second (warn).
        if "trace_log" in kwargs:
            log.debug("_make_team: %s does not accept trace_log= — tracing disabled", class_name)
            kwargs.pop("trace_log")
            try:
                return cls(**kwargs)
            except TypeError:
                pass
        if "llm" in kwargs:
            log.warning(
                "_make_team: %s does not accept llm= kwarg (%s) — falling back to default LLM",
                class_name, exc,
            )
            kwargs.pop("llm")
            if max_cost_usd is not None:
                kwargs["max_cost_usd"] = max_cost_usd
            return cls(**kwargs)
        raise


make_team = _make_team  # public alias for eval_runner and external callers


_REPO_SKIP = {
    '.git', '__pycache__', 'node_modules', '.venv', 'venv', 'dist', 'build',
    '.mypy_cache', '.pytest_cache', '.tox', 'coverage', '.eggs',
}


def _extract_github_repo_name(repo_url: str) -> Optional[str]:
    """Extract 'owner/repo' from a GitHub HTTPS URL, or None if not a GitHub URL."""
    import re as _re
    m = _re.match(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    return m.group(1) if m else None


_REPO_SRC_EXTS = {
    '.py', '.ts', '.tsx', '.js', '.jsx', '.go', '.rs', '.java', '.cs',
    '.rb', '.php', '.swift', '.kt', '.cpp', '.c', '.h', '.yaml', '.yml',
    '.toml', '.json', '.md',
}
_REPO_MAX_FILE_CHARS = 6000
_REPO_MAX_FILES = 25
_REPO_MAX_CONTEXT_CHARS = 50_000
_REPO_CLONE_TIMEOUT_S = 120


def _build_repo_context(repo_dir: Path) -> str:
    """Walk a cloned repo and return a compact context string for LLM injection."""
    tree_lines = ["## Repository file tree\n```"]
    src_files: list[Path] = []

    for path in sorted(repo_dir.rglob("*")):
        rel = path.relative_to(repo_dir)
        parts = rel.parts
        if any(p in _REPO_SKIP or p.startswith('.') for p in parts):
            continue
        indent = "  " * (len(parts) - 1)
        tree_lines.append(f"{indent}{path.name}{'/' if path.is_dir() else ''}")
        if path.is_file() and path.suffix.lower() in _REPO_SRC_EXTS:
            src_files.append(path)

    tree_lines.append("```\n")
    context_parts = ["\n".join(tree_lines), "## Key source files\n"]
    total_chars = sum(len(p) for p in context_parts)

    for fp in src_files[:_REPO_MAX_FILES]:
        if total_chars >= _REPO_MAX_CONTEXT_CHARS:
            context_parts.append("... [context truncated — too many files]\n")
            break
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > _REPO_MAX_FILE_CHARS:
            content = content[:_REPO_MAX_FILE_CHARS] + "\n... [truncated]"
        rel = fp.relative_to(repo_dir)
        snippet = f"### {rel}\n```{fp.suffix.lstrip('.')}\n{content}\n```\n"
        context_parts.append(snippet)
        total_chars += len(snippet)

    return "\n".join(context_parts)


async def _inject_repo_context(
    repo_url: str, request: str, repo_token: Optional[str] = None
) -> tuple[Path, str]:
    """Clone *repo_url* (depth=1) to a temp dir and prepend its context to *request*.

    Returns (tmp_dir, augmented_request). Caller must clean up tmp_dir.
    Raises RuntimeError on clone failure or if repo_url targets an internal host.
    """
    from app.core.security import validate_external_url
    try:
        validate_external_url(repo_url)
    except ValueError as exc:
        raise RuntimeError(f"Blocked repository URL: {exc}") from exc

    clone_url = repo_url
    if repo_token and repo_url.startswith("https://"):
        # Inject token into URL for private repos: https://token@github.com/...
        clone_url = repo_url.replace("https://", f"https://{repo_token}@", 1)

    tmp = Path(tempfile.mkdtemp(prefix="antcrew-repo-"))
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "--single-branch", clone_url, str(tmp),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_REPO_CLONE_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"git clone timed out after {_REPO_CLONE_TIMEOUT_S}s: {repo_url}")

        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            err = (stderr or b"").decode(errors="replace")[:400]
            raise RuntimeError(f"git clone failed (exit {proc.returncode}): {err}")

        context = _build_repo_context(tmp)
        augmented = (
            f"[Repository context cloned from {repo_url}]\n\n"
            f"{context}\n"
            f"---\n\n"
            f"{request}"
        )
        return tmp, augmented
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _serialize_state(state: dict) -> dict:
    """Convert a LangGraph state dict to a JSON-serializable form."""
    from pydantic import BaseModel

    def _v(val):
        if isinstance(val, BaseModel):
            return val.model_dump()
        if isinstance(val, list):
            return [_v(item) for item in val]
        if isinstance(val, dict):
            return {k: _v(v) for k, v in val.items()}
        return val

    return {k: _v(v) for k, v in state.items() if not k.startswith("_")}


def _make_agent_channel(
    base_channel: PlatformChannel,
    agent,
    per_agent_channels: list,
) -> PlatformChannel:
    """Return a per-agent PlatformChannel that carries the agent's hitl_channel + feedback_schema.

    If both are defaults, returns base_channel unchanged to avoid unnecessary objects.
    The returned channel is appended to *per_agent_channels* so _on_pipeline_start can set run_id.
    """
    import json as _json
    hitl_ch = getattr(agent, "hitl_channel", "default")
    schema_json_str: Optional[str] = None
    try:
        from antcrew.core.events import _schema_json as _sj
        _schema_dict = _sj(agent)
        if _schema_dict is not None:
            schema_json_str = _json.dumps(_schema_dict)
    except Exception:
        pass

    if hitl_ch == "default" and schema_json_str is None:
        return base_channel

    ch = PlatformChannel(
        timeout_s=base_channel._timeout_s,
        hitl_channel=hitl_ch,
        feedback_schema_json=schema_json_str,
    )
    per_agent_channels.append(ch)
    return ch


def _run_sync(
    team_name: str,
    request: str,
    thread_id: str,
    max_cost_usd: Optional[float],
    platform_channel: PlatformChannel,
    force_hitl: bool,
    byok_api_key: Optional[str] = None,
    byok_base_url: Optional[str] = None,
    model: str = "",
    agent_llm_configs: Optional[dict] = None,
    dry_run: bool = False,
    org_context: Optional[dict] = None,
    replay_run_id: Optional[str] = None,
    per_agent_channels: Optional[list] = None,
    initial_memory: Optional[dict] = None,
    kv_out: Optional[list] = None,
    cli_working_dir: Optional[str] = None,
    doc_manager=None,
):
    """Run a team synchronously in the executor thread.

    When force_hitl=True all agents get the platform channel and run_interactive is used.
    Otherwise only agents already marked approval_required=True get the channel.

    agent_llm_configs: {AgentClassName: (model, api_key, base_url)} for per-agent LLM overrides.
    dry_run: suppress side effects (no write_back, no sandbox) but run all LLMs normally.
    org_context: pre-populate ProjectKB before running (keys: decisions, tech_stack, dependencies).
    replay_run_id: inject artifacts from a past ChromaMemory run as context for BA and PM.
    per_agent_channels: mutable list; per-agent PlatformChannels are appended so _bg() can set run_id.
    doc_manager: pre-built DocumentationManager; when set, indexes docs and injects into all agents.
    """
    _pac = per_agent_channels if per_agent_channels is not None else []

    # TraceLog: active by default; disable with ANTCREW_TRACELOG_ENABLED=false.
    # Path is configurable via ANTCREW_TRACELOG_PATH (default ./antcrew_trace.db).
    _trace_log = None
    if os.environ.get("ANTCREW_TRACELOG_ENABLED", "true").lower() not in ("0", "false", "no"):
        _tl_path = os.environ.get("ANTCREW_TRACELOG_PATH", "./antcrew_trace.db")
        try:
            from antcrew.trace import TraceLog as _TraceLog
            _trace_log = _TraceLog(_tl_path)
        except Exception as _tl_err:
            log.debug("runner: TraceLog init skipped: %s", _tl_err)

    team = _make_team(
        team_name,
        max_cost_usd=max_cost_usd,
        model=model,
        byok_api_key=byok_api_key,
        byok_base_url=byok_base_url,
        trace_log=_trace_log,
        cli_working_dir=cli_working_dir,
    )
    all_agents = list(getattr(team, "_agents", {}).values())

    # Inject KV memory into each agent so they can read/write persistent state
    if initial_memory is not None:
        try:
            from antcrew.memory.kv_store import InMemoryKVMemory
            _kv_mem = InMemoryKVMemory(initial=initial_memory)
            for _ag in all_agents:
                _ag.kv_memory = _kv_mem
            if kv_out is not None:
                kv_out.append(_kv_mem)
        except Exception as _mem_err:
            log.debug("runner: KV memory injection skipped: %s", _mem_err)

    if agent_llm_configs:
        from antcrew import build_llm as _build_llm
        for _agent in all_agents:
            _atype = type(_agent).__name__
            if _atype in agent_llm_configs:
                _amodel, _akey, _aurl = agent_llm_configs[_atype]
                _kw: dict = {}
                if _akey is not None:
                    _kw["api_key"] = _akey
                if _aurl is not None:
                    _kw["base_url"] = _aurl
                try:
                    _new_llm = _build_llm(_amodel, **_kw)
                    if max_cost_usd is not None:
                        _new_llm.max_cost_usd = max_cost_usd
                    _agent.llm = _new_llm
                except Exception as _e:
                    log.warning("runner: LLM override for %s failed: %s", _atype, _e)

    if doc_manager is not None:
        try:
            doc_manager.index_from_storage()
            for _ag in all_agents:
                if hasattr(_ag, "set_documentation"):
                    _ag.set_documentation(doc_manager)
        except Exception as _doc_exc:
            log.warning("runner: docs setup failed: %s", _doc_exc)

    if force_hitl:
        # Force HITL on all agents regardless of their approval_required flag
        for agent in all_agents:
            if not getattr(agent, "channel", None):
                agent.channel = _make_agent_channel(platform_channel, agent, _pac)
            agent.approval_required = True
        return team.run_interactive(request, thread_id=thread_id)

    hitl_agents = [a for a in all_agents if getattr(a, "approval_required", False)]
    if hitl_agents:
        for agent in hitl_agents:
            if not getattr(agent, "channel", None):
                agent.channel = _make_agent_channel(platform_channel, agent, _pac)
        return team.run_interactive(request, thread_id=thread_id)

    result = team.run(
        request,
        thread_id=thread_id,
        dry_run=dry_run,
        **({"org_context": org_context} if org_context else {}),
        **({"replay_run_id": replay_run_id} if replay_run_id else {}),
    )

    # Persist ComparisonLLM comparison_log in state so GET /runs/{id}/comparison can serve it.
    try:
        if hasattr(team.llm, "comparison_log"):
            cmp_log = team.llm.comparison_log()
            if cmp_log and hasattr(result, "state") and isinstance(result.state, dict):
                result.state["_comparison_log"] = cmp_log
    except Exception as _e:
        log.debug("runner: comparison_log extraction failed: %s", _e)

    return result


async def _set_run_attribution(
    run_id: str,
    created_by: Optional[str],
    workspace_id: Optional[int],
    client_label: Optional[str] = None,
    model_overrides: Optional[dict] = None,
) -> None:
    from sqlmodel import select

    from app.models.run import Run
    from app.models.workspace import Workspace
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await session.exec(select(Run).where(Run.run_id == run_id))
            run = result.first()
            if run:
                if created_by:
                    run.created_by = created_by
                if workspace_id is not None:
                    run.workspace_id = workspace_id
                    ws = (await session.exec(
                        select(Workspace).where(Workspace.id == workspace_id)
                    )).first()
                    if ws:
                        run.llm_key_mode = ws.llm_key_mode
                if client_label is not None:
                    run.client_label = client_label
                if model_overrides:
                    run.model_overrides = model_overrides
                session.add(run)
                await session.commit()
    except Exception as exc:
        log.warning("runner: failed to set attribution for run %s: %s", run_id, exc)


async def _save_to_dead_letter(run_id: str, state_dict: dict) -> None:
    """Last-resort: persist run state to a local file when the DB is unreachable.

    The dead-letter directory (ANTCREW_DEAD_LETTER_DIR, default ./dead_letter) is
    meant for manual recovery by an operator. Each file is named {run_id}.json and
    contains the full state plus metadata. Re-import via POST /admin/runs/recover
    once the DB is back.
    """
    import json
    from datetime import datetime, timezone

    dl_dir = Path(os.environ.get("ANTCREW_DEAD_LETTER_DIR", "./dead_letter"))
    try:
        dl_dir.mkdir(parents=True, exist_ok=True)
        dl_path = dl_dir / f"{run_id}.json"
        dl_path.write_text(json.dumps({
            "run_id": run_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "state": state_dict,
        }, default=str))
        log.error(
            "runner: run %s state saved to dead-letter at %s — "
            "recover manually once DB is back",
            run_id, dl_path,
        )
    except Exception as dl_exc:
        log.error(
            "runner: CRITICAL — cannot persist run %s to DB or dead-letter (%s). "
            "Run output is permanently lost.",
            run_id, dl_exc,
        )


async def _store_result(result) -> None:
    """Persist the full run state and upsert tickets. Retries up to 3 times.

    Handles both RunResult (from team.run()) and plain dict (from team.run_interactive()).
    """
    from sqlmodel import select

    from app.models.run import Run

    if isinstance(result, dict):
        # run_interactive() returns final_state = app.get_state(config).values
        run_id = result.get("_run_id")
        state_dict = _serialize_state(result)
        raw_state = result
    elif hasattr(result, "state") and isinstance(result.state, dict):
        run_id = result.state.get("_run_id")
        state_dict = result.to_dict() if hasattr(result, "to_dict") else result.state
        raw_state = result.state
    else:
        return

    if not run_id:
        return

    from app.services.artifact_storage import externalize_artifacts
    state_dict = await externalize_artifacts(state_dict, run_id)

    for attempt in range(3):
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                stmt = select(Run).where(Run.run_id == run_id)
                db_run = (await session.exec(stmt)).first()
                if db_run:
                    db_run.state = state_dict
                    session.add(db_run)
                ws_id = db_run.workspace_id if db_run else None
                await upsert_tickets_from_run(session, run_id, raw_state, workspace_id=ws_id)
                await session.commit()  # single commit for state + tickets

            # Auto-advance sprint: if this run belongs to a sprint ticket, find and dispatch
            # the next wave of ready tickets without blocking _store_result.
            if db_run and db_run.client_label and db_run.client_label.startswith("sprint:"):
                asyncio.ensure_future(_advance_sprint_after_run(
                    db_run.client_label, db_run.status, db_run.workspace_id,
                ))
            return
        except Exception as exc:
            if attempt == 2:
                log.error("runner: failed to store result for run %s after 3 attempts: %s", run_id, exc)
                await _save_to_dead_letter(run_id, state_dict)
            else:
                await asyncio.sleep(2 ** attempt)


async def _do_write_back(
    result, repo_dir: Path, run_id: str, repo_url: str,
    installation_id: Optional[int] = None,
) -> None:
    """Write artifacts to the cloned repo, push to a new branch, and open a PR.

    When *installation_id* is provided, opens a GitHub PR via the app installation
    token after a successful push. Falls back silently if PR creation fails.
    """
    import subprocess

    from antcrew.core.writeback import write_back as _wb

    # 1. Write artifacts to disk
    _wb(result, repo_dir, yes=True, print_fn=lambda m: log.info("writeback: %s", m))

    # 2. Check if there are any changes
    diff_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "diff", "--stat", "HEAD"],
        capture_output=True, text=True, timeout=30
    )
    diff_summary = diff_proc.stdout.strip()
    if not diff_summary:
        log.info("runner: write-back: no changes detected for run %s", run_id)
        shutil.rmtree(repo_dir, ignore_errors=True)
        return

    # 3. Create branch antcrew/wb-{run_id[:8]}
    branch = f"antcrew/wb-{run_id[:8]}"
    subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", "-b", branch],
        capture_output=True, timeout=30
    )

    # 4. Commit changes
    request_text = ""
    if isinstance(result, dict):
        request_text = (result.get("request") or "")[:60]
    elif hasattr(result, "state"):
        request_text = (result.state.get("request") or "")[:60]

    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], capture_output=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit",
         "-m", f"antcrew: {request_text or 'AI-generated changes'}\n\nRun: {run_id}"],
        capture_output=True, timeout=30
    )

    # 5. Push branch
    push_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "push", "origin", branch],
        capture_output=True, text=True, timeout=60
    )

    pushed_ok = push_proc.returncode == 0

    # 6. Open a GitHub PR via the App installation token (if available)
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    if pushed_ok and installation_id is not None:
        repo_name = _extract_github_repo_name(repo_url)
        if repo_name:
            try:
                from app.services.github_tokens import create_pull_request as _create_pr
                _pr = await _create_pr(
                    installation_id=installation_id,
                    repo_full_name=repo_name,
                    head=branch,
                    title=f"antcrew: {request_text or 'AI-generated changes'}",
                    body=(
                        f"Generated by [antcrew](https://platform.antcrew.org).\n\n"
                        f"**Run ID:** `{run_id}`"
                    ),
                )
                pr_url = _pr.get("html_url")
                pr_number = _pr.get("number")
                log.info("runner: opened PR #%s for run %s: %s", pr_number, run_id, pr_url)
            except Exception as exc:
                log.warning("runner: could not open PR for run %s: %s", run_id, exc)

    # 7. Store write-back result in run state
    try:
        from sqlmodel import select as _sel

        from app.models.run import Run
        async with AsyncSession(engine, expire_on_commit=False) as _sess:
            _run = (await _sess.exec(_sel(Run).where(Run.run_id == run_id))).first()
            if _run and _run.state:
                wb_result = {
                    "branch": branch,
                    "diff_summary": diff_summary,
                    "pushed": pushed_ok,
                    "push_error": push_proc.stderr[:200] if not pushed_ok else None,
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                }
                _run.state = {**(_run.state or {}), "write_back_result": wb_result}
                _sess.add(_run)
                await _sess.commit()
    except Exception as exc:
        log.warning("runner: could not store write-back result: %s", exc)

    log.info(
        "runner: write-back for run %s — branch=%s pushed=%s pr=%s",
        run_id, branch, pushed_ok, pr_url or "none"
    )

    # 8. Clean up
    shutil.rmtree(repo_dir, ignore_errors=True)


async def _load_team_memory(team_name: str, workspace_id: Optional[int]) -> dict:
    """Load the persisted KV memory dict for team_name in workspace."""
    if workspace_id is None:
        return {}
    try:
        from sqlmodel import select as _sel

        from app.models.memory import RunMemory
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            row = (await sess.exec(
                _sel(RunMemory)
                .where(RunMemory.workspace_id == workspace_id)
                .where(RunMemory.team_name == team_name)
            )).first()
            return dict(row.memory_json or {}) if row else {}
    except Exception as exc:
        log.warning("runner: could not load team memory for %s: %s", team_name, exc)
        return {}


async def _save_team_memory(team_name: str, workspace_id: Optional[int], snapshot: dict) -> None:
    """Persist the KV memory snapshot for team_name in workspace (upsert)."""
    if workspace_id is None or not snapshot:
        return
    try:
        from sqlmodel import select as _sel

        from app.models._utils import _utcnow
        from app.models.memory import RunMemory
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            row = (await sess.exec(
                _sel(RunMemory)
                .where(RunMemory.workspace_id == workspace_id)
                .where(RunMemory.team_name == team_name)
            )).first()
            if row is None:
                row = RunMemory(workspace_id=workspace_id, team_name=team_name)
                sess.add(row)
            row.memory_json = snapshot
            row.updated_at = _utcnow()
            sess.add(row)
            await sess.commit()
    except Exception as exc:
        log.warning("runner: could not save team memory for %s: %s", team_name, exc)


async def _maybe_snapshot_team(
    team_name: str,
    workspace_id: Optional[int],
    agent_end_events: list[dict],
) -> None:
    """Create a TeamSnapshot if the team's governance hash has changed since the last run."""
    import hashlib as _hashlib

    agents_data = [
        {
            "agent_name": ev.get("agent_name", ""),
            "governance_hash": ev.get("governance_hash", ""),
            "stage": ev.get("stage", ""),
        }
        for ev in agent_end_events
        if ev.get("agent_name")
    ]
    if not agents_data:
        return

    components = sorted(
        f"{a['agent_name']}:{a['governance_hash']}" for a in agents_data
    )
    team_hash = _hashlib.sha256(":".join(components).encode()).hexdigest()[:16]

    try:
        from sqlmodel import select as _sel

        from app.models.integrations import TeamSnapshot
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            latest = (await sess.exec(
                _sel(TeamSnapshot)
                .where(TeamSnapshot.team_name == team_name)
                .where(TeamSnapshot.workspace_id == workspace_id)
                .order_by(TeamSnapshot.created_at.desc())
                .limit(1)
            )).first()
            if latest and latest.team_hash == team_hash:
                return
            snap = TeamSnapshot(
                workspace_id=workspace_id,
                team_name=team_name,
                team_hash=team_hash,
                agents_json=agents_data,
            )
            sess.add(snap)
            await sess.commit()
            log.info("runner: new team snapshot for %s hash=%s", team_name, team_hash)
    except Exception as exc:
        log.warning("runner: team snapshot failed for %s: %s", team_name, exc)


async def _sync_tickets_after_run(
    run_id: str,
    team_name: str,
    workspace_id: Optional[int],
) -> None:
    """Load persisted tickets for the run and sync them to PM destinations."""
    if not workspace_id:
        return
    try:
        from sqlmodel import select as _sel

        from app.models.run import Ticket as _Ticket
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            tickets = (await sess.exec(
                _sel(_Ticket)
                .where(_Ticket.run_id == run_id)
                .where(_Ticket.ticket_type != "manual_action")
                .order_by(_Ticket.id)
            )).all()
        if not tickets:
            return
        from app.services.ticket_sync import sync_tickets_to_destinations
        await sync_tickets_to_destinations(
            run_id,
            [
                {
                    "ticket_id": t.ticket_id,
                    "title": t.title,
                    "description": t.description,
                    "acceptance_criteria": t.acceptance_criteria,
                    "priority": t.priority,
                    "ticket_type": t.ticket_type,
                    "status": t.status,
                }
                for t in tickets
            ],
            workspace_id,
            team_name,
        )
    except Exception as exc:
        log.warning("runner: ticket sync failed for run %s: %s", run_id, exc)


async def _persist_agent_events(run_id: str, events: list[dict]) -> None:
    """Persist a list of agent.end payloads as AgentEvent rows."""
    if not events:
        return
    import json as _json
    import os as _os

    from sqlalchemy.ext.asyncio import create_async_engine as _cae
    from sqlmodel.ext.asyncio.session import AsyncSession as _ASession

    db_url = _os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")
    _eng = _cae(db_url, echo=False)
    try:
        async with _ASession(_eng, expire_on_commit=False) as _s:
            from app.models.run import AgentEvent as _AE
            for ev in events:
                _s.add(_AE(
                    run_id=run_id,
                    agent_name=ev.get("agent_name", ""),
                    duration_s=float(ev.get("duration_s", 0.0)),
                    tokens_in=int(ev.get("tokens_in", 0)),
                    tokens_out=int(ev.get("tokens_out", 0)),
                    cost_usd=float(ev.get("cost_usd", 0.0)),
                    produced_keys=_json.dumps(ev.get("produced_keys") or []),
                ))
            await _s.commit()
    except Exception as exc:
        log.warning("runner: agent event persistence failed for run %s: %s", run_id, exc)
    finally:
        await _eng.dispose()


async def _advance_sprint_after_run(
    client_label: str,
    run_status: str,
    workspace_id: Optional[int],
) -> None:
    """Auto-advance a sprint after one of its ticket runs completes.

    Called as a fire-and-forget future from _store_result.
    Parses the sprint/ticket from client_label, updates ticket status,
    finds newly-ready tickets, and dispatches them with dep context.

    client_label format: "sprint:{sprint_id}:ticket:{ticket_id}"
    """
    import re as _re

    from sqlmodel import select as _sel

    from app.core.database import engine as _eng
    from app.models.run import Sprint, Ticket
    from app.models.run import Workspace as _WS

    m = _re.match(r"sprint:([^:]+):ticket:([^:]+)", client_label)
    if not m:
        return
    sprint_id, ticket_id = m.group(1), m.group(2)

    try:
        async with AsyncSession(_eng, expire_on_commit=False) as session:
            # 1. Update this ticket's status based on run outcome
            ticket = (await session.exec(
                _sel(Ticket).where(Ticket.ticket_id == ticket_id)
            )).first()
            if not ticket:
                return

            ticket.status = "done" if run_status == "success" else "blocked"
            ticket.updated_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).replace(tzinfo=None)
            session.add(ticket)
            await session.commit()

            # 2. Load all tickets in this sprint and find newly-ready ones
            all_tickets = list((await session.exec(
                _sel(Ticket).where(Ticket.sprint_id == sprint_id).order_by(Ticket.backlog_order)
            )).all())

            all_ids = {t.ticket_id for t in all_tickets}
            done_ids = {t.ticket_id for t in all_tickets if t.status == "done"}
            failed_ids = {t.ticket_id for t in all_tickets if t.status == "blocked"}

            ready = [
                t for t in all_tickets
                if t.implementing_run_id is None          # not yet dispatched
                and t.status not in ("done", "blocked")
                and (set(t.depends_on or []) & all_ids).issubset(done_ids)
                and not (set(t.depends_on or []) & all_ids & failed_ids)
            ]

            if not ready:
                return

            # 3. Resolve team
            sprint = (await session.exec(
                _sel(Sprint).where(Sprint.sprint_id == sprint_id)
            )).first()
            team = (sprint.default_team if sprint else None) or "FullStackTeam"
            if sprint and not sprint.default_team and workspace_id is not None:
                ws = (await session.exec(_sel(_WS).where(_WS.id == workspace_id))).first()
                if ws and ws.default_team:
                    team = ws.default_team

            # 4. Dispatch each ready ticket
            from app.services.runner import dispatch as _dispatch  # lazy — avoids circular import
            for t in ready:
                # Build context: pass implementing_run_id of the last direct dep as replay context
                dep_run_id: Optional[str] = None
                if t.depends_on:
                    dep_tickets = [
                        dt for dt in all_tickets
                        if dt.ticket_id in t.depends_on and dt.implementing_run_id
                    ]
                    if dep_tickets:
                        dep_run_id = dep_tickets[-1].implementing_run_id

                request = t.title
                if t.description:
                    request += f"\n\n{t.description}"
                if t.acceptance_criteria:
                    request += f"\n\nAcceptance criteria:\n{t.acceptance_criteria}"

                try:
                    run_id = await _dispatch(
                        team, request,
                        workspace_id=workspace_id,
                        created_by=f"sprint:{sprint_id}",
                        client_label=f"sprint:{sprint_id}:ticket:{t.ticket_id}",
                        replay_run_id=dep_run_id,
                    )
                    if run_id:
                        t.implementing_run_id = run_id
                        session.add(t)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "sprint_runner: failed to dispatch ticket %s in sprint %s: %s",
                        t.ticket_id, sprint_id, exc,
                    )

            await session.commit()

    except Exception as exc:  # noqa: BLE001
        log.error("sprint_runner: _advance_sprint_after_run failed for %s: %s", client_label, exc)


def shutdown() -> None:
    """Shutdown the thread pool. Call during app teardown."""
    _executor.shutdown(wait=False, cancel_futures=True)
