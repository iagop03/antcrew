"""REST endpoints for pipeline runs."""
from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    WorkspaceContext,
    get_workspace_context,
    require_api_key,
    require_role,
)
from app.core.database import get_session
from app.core.exceptions import (
    RunNotAccessibleError,
    RunNotFoundError,
    RunNotRunningError,
    StateNotAvailableError,
)
from app.core.sse import _sse_broadcaster, _SSEBroadcaster  # noqa: F401
from app.models.run import Event as DBEvent
from app.models.run import Run
from app.services.runs import (
    cancel_run,
    get_run,
    get_run_events,
    get_run_stats,
    get_run_tickets,
    list_runs,
)

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(require_api_key)],
)


class RunUpload(BaseModel):
    """Pre-computed run result from a local `antcrew run --push-to` execution."""
    team: str
    request: str
    thread_id: str = "default"
    cost_usd: float = 0.0
    duration_s: Optional[float] = None
    state: Optional[dict] = None


def _assert_run_access(run: Run, ctx: WorkspaceContext) -> None:
    """Raise 403 if the API key is workspace-scoped and doesn't own this run."""
    from app.core.auth import ws_accessible
    if ctx.workspace_ids is not None and not ws_accessible(run.workspace_id, ctx):
        raise RunNotAccessibleError()


@router.post("/upload", status_code=201, response_model=Run,
             dependencies=[Depends(require_role("admin", "write"))])
async def upload_run(
    body: RunUpload,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Store a local CLI run result on the platform dashboard.

    Called by ``antcrew run --push-to <platform-url>`` after a successful local run.
    The run appears in the dashboard immediately with status ``success``.
    Tickets in ``state.tickets`` are upserted via the normal ticket pipeline.
    """
    from app.services.runner import AVAILABLE_TEAMS
    from app.services.runs import upsert_tickets_from_run

    if body.team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.team!r}. Available: {AVAILABLE_TEAMS}")
    if not body.request.strip():
        raise HTTPException(422, "request must not be empty")

    run = Run(
        run_id=str(uuid.uuid4()),
        thread_id=body.thread_id,
        team=body.team,
        request=body.request.strip(),
        status="success",
        cost_usd=body.cost_usd,
        duration_s=body.duration_s,
        state=body.state,
        workspace_id=ctx.workspace_id,
        created_by=ctx.created_by,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    if body.state:
        await upsert_tickets_from_run(session, run.run_id, body.state, workspace_id=run.workspace_id)
        await session.commit()

    return run


@router.get("/stats")
async def stats(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Aggregate counts and total cost. Scoped to the API key's workspace if set."""
    return await get_run_stats(session, workspace_ids=ctx.workspace_ids)


@router.get("/agent-stats")
async def agent_stats(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Aggregate per-agent cost/token/duration metrics across all workspace runs.

    Returns one entry per distinct agent_name, ordered by total spend descending.
    """
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.run import AgentEvent

    stmt = (
        sa_select(
            AgentEvent.agent_name,
            func.count(AgentEvent.id).label("call_count"),
            func.avg(AgentEvent.duration_s).label("avg_duration_s"),
            func.sum(AgentEvent.cost_usd).label("total_cost_usd"),
            func.avg(AgentEvent.cost_usd).label("avg_cost_usd"),
            func.avg(AgentEvent.tokens_in).label("avg_tokens_in"),
            func.avg(AgentEvent.tokens_out).label("avg_tokens_out"),
        )
        .join(Run, Run.run_id == AgentEvent.run_id)
        .group_by(AgentEvent.agent_name)
        .order_by(func.sum(AgentEvent.cost_usd).desc())
    )

    if ctx.workspace_ids is not None:
        if len(ctx.workspace_ids) == 1:
            stmt = stmt.where(Run.workspace_id == ctx.workspace_ids[0])
        else:
            stmt = stmt.where(Run.workspace_id.in_(ctx.workspace_ids))

    rows = (await session.execute(stmt)).all()
    return {
        "agents": [
            {
                "agent_name": r.agent_name,
                "call_count": int(r.call_count or 0),
                "avg_duration_s": round(float(r.avg_duration_s or 0), 2),
                "total_cost_usd": round(float(r.total_cost_usd or 0), 6),
                "avg_cost_usd": round(float(r.avg_cost_usd or 0), 6),
                "avg_tokens_in": int(r.avg_tokens_in or 0),
                "avg_tokens_out": int(r.avg_tokens_out or 0),
            }
            for r in rows
        ]
    }


@router.get("/", response_model=list[Run])
async def index(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    since_id: Optional[int] = Query(None, description="Cursor: return runs with id < since_id"),
    team: Optional[str] = None,
    team_prefix: Optional[str] = Query(None, description="Filter by team name prefix, e.g. 'pipeline:'"),
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    return await list_runs(
        session, limit=limit, offset=offset, team=team, team_prefix=team_prefix,
        status=status, since_id=since_id, workspace_ids=ctx.workspace_ids,
        client_label=ctx.client_label,
    )


@router.get("/compare-artifacts")
async def compare_artifacts(
    run_a: str = Query(..., description="First run_id"),
    run_b: str = Query(..., description="Second run_id"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return a file-level diff between artifacts of two engine runs.

    For each file present in either run, returns: status (added/removed/changed/unchanged),
    lines_added, lines_removed. File content is NOT returned — only statistics.
    Supports MemoryStore runs (state.code_artifacts) and FilesystemStore runs (output_dir).
    """
    import difflib

    run_a_obj = await get_run(session, run_a)
    run_b_obj = await get_run(session, run_b)
    if not run_a_obj:
        raise RunNotFoundError(run_a)
    if not run_b_obj:
        raise RunNotFoundError(run_b)
    _assert_run_access(run_a_obj, ctx)
    _assert_run_access(run_b_obj, ctx)

    def _collect_artifacts(run: "Run") -> dict[str, str]:
        """Return {file_path: content} for all artifacts in a run."""
        result: dict[str, str] = {}
        out_dir = _engine_output_dir(run)
        if out_dir and out_dir.exists():
            for p in sorted(out_dir.rglob("*")):
                if p.is_file() and not any(part in _ENGINE_SKIP_DIRS for part in p.parts):
                    try:
                        result[str(p.relative_to(out_dir))] = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass
        elif run.state:
            s = run.state
            for key in ("code_artifacts", "test_artifacts", "doc_artifacts"):
                for art in s.get(key) or []:
                    fp = art.get("file_path", "")
                    if fp:
                        result[fp] = art.get("content", "")
        return result

    arts_a = _collect_artifacts(run_a_obj)
    arts_b = _collect_artifacts(run_b_obj)
    all_paths = sorted(set(arts_a) | set(arts_b))

    diff_entries = []
    for path in all_paths:
        if path in arts_a and path not in arts_b:
            status = "removed"
            lines_added = lines_removed = 0
        elif path not in arts_a and path in arts_b:
            status = "added"
            lines_added = len(arts_b[path].splitlines())
            lines_removed = 0
        else:
            lines_a = arts_a[path].splitlines(keepends=True)
            lines_b = arts_b[path].splitlines(keepends=True)
            if lines_a == lines_b:
                status = "unchanged"
                lines_added = lines_removed = 0
            else:
                status = "changed"
                opcodes = difflib.SequenceMatcher(None, lines_a, lines_b).get_opcodes()
                lines_added = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag in ("insert", "replace"))
                lines_removed = sum(i2 - i1 for tag, i1, i2, _, _ in opcodes if tag in ("delete", "replace"))
        diff_entries.append({
            "file_path": path,
            "status": status,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
        })

    changed = sum(1 for e in diff_entries if e["status"] == "changed")
    added = sum(1 for e in diff_entries if e["status"] == "added")
    removed = sum(1 for e in diff_entries if e["status"] == "removed")
    return {
        "run_a": run_a,
        "run_b": run_b,
        "summary": {"changed": changed, "added": added, "removed": removed,
                    "unchanged": len(diff_entries) - changed - added - removed},
        "files": diff_entries,
    }


@router.get("/artifact-timeline")
async def artifact_timeline(
    limit: int = Query(20, ge=1, le=100, description="Max completed runs to scan"),
    team: Optional[str] = Query(None, description="Filter by team name"),
    artifact_type: str = Query(
        "all",
        description="Artifact key: all | code_artifacts | test_artifacts | doc_artifacts",
    ),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Workspace artifact evolution timeline.

    Returns completed runs in chronological order with per-file diffs between
    consecutive versions — showing how artifacts changed across runs without
    requiring the caller to specify run-ID pairs.

    Complements GET /runs/compare-artifacts (two specific runs) and mirrors the
    ArtifactHistory.timeline() API from the antcrew SDK using the platform DB
    as storage instead of a local JSON file.

    Each entry in ``timeline`` contains:
    - ``version``: 1-indexed counter (skips runs with no matching artifacts)
    - ``run_id``, ``team``, ``created_at``
    - ``files``: per-file diff vs previous version (status, lines_added, lines_removed)
    - ``lines_added`` / ``lines_removed``: workspace-wide totals for that version
    """
    import difflib

    from sqlalchemy import asc as _asc
    from sqlalchemy import select as _sa_select

    _ALL_KEYS = ("code_artifacts", "test_artifacts", "doc_artifacts")
    if artifact_type != "all" and artifact_type not in _ALL_KEYS:
        raise HTTPException(422, f"artifact_type must be 'all' or one of {_ALL_KEYS}")

    stmt = (
        _sa_select(Run)
        .where(Run.status.in_(["success", "done"]))
        .where(Run.state.isnot(None))
        .order_by(_asc(Run.id))
        .limit(limit)
    )
    if ctx.workspace_ids is not None:
        if len(ctx.workspace_ids) == 1:
            stmt = stmt.where(Run.workspace_id == ctx.workspace_ids[0])
        else:
            stmt = stmt.where(Run.workspace_id.in_(ctx.workspace_ids))
    if team:
        stmt = stmt.where(Run.team == team)

    rows = (await session.execute(stmt)).scalars().all()

    _keys = [artifact_type] if artifact_type != "all" else list(_ALL_KEYS)

    def _extract(run: Run) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in _keys:
            for art in (run.state or {}).get(key) or []:
                fp = art.get("file_path", "")
                if fp:
                    out[fp] = art.get("content", "")
        return out

    timeline: list[dict] = []
    prev: dict[str, str] = {}
    version = 0

    for run in rows:
        curr = _extract(run)
        if not curr:
            continue
        version += 1
        file_diffs: list[dict] = []
        total_added = total_removed = 0

        for path in sorted(set(prev) | set(curr)):
            if path in prev and path not in curr:
                status, la, lr = "removed", 0, 0
            elif path not in prev:
                la, lr = len(curr[path].splitlines()), 0
                status = "added"
            else:
                a_lines = prev[path].splitlines(keepends=True)
                b_lines = curr[path].splitlines(keepends=True)
                if a_lines == b_lines:
                    status, la, lr = "unchanged", 0, 0
                else:
                    status = "changed"
                    opcodes = difflib.SequenceMatcher(None, a_lines, b_lines).get_opcodes()
                    la = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag in ("insert", "replace"))
                    lr = sum(i2 - i1 for tag, i1, i2, _, _ in opcodes if tag in ("delete", "replace"))
            file_diffs.append({"file_path": path, "status": status, "lines_added": la, "lines_removed": lr})
            total_added += la
            total_removed += lr

        timeline.append({
            "version": version,
            "run_id": run.run_id,
            "team": run.team,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "files_total": len(curr),
            "files_changed": sum(1 for f in file_diffs if f["status"] != "unchanged"),
            "lines_added": total_added,
            "lines_removed": total_removed,
            "files": file_diffs,
        })
        prev = curr

    return {
        "runs_scanned": len(rows),
        "versions_with_artifacts": len(timeline),
        "artifact_type": artifact_type,
        "timeline": timeline,
    }


@router.get("/estimate")
async def estimate_run_cost(
    team: str = Query(..., description="Team name to estimate cost for"),
    limit: int = Query(20, ge=5, le=100, description="Sample size of recent successful runs"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Estimate the cost of a new run based on recent historical runs.

    Queries the last *limit* successful runs for the given team and returns
    percentile statistics (min, median, p75, max). Returns null fields when
    there is no history to draw from.

    Example::

        GET /runs/estimate?team=DevTeam
        → {
            "team": "DevTeam",
            "based_on_runs": 18,
            "min_usd": 0.0021,
            "median_usd": 0.0038,
            "p75_usd": 0.0055,
            "max_usd": 0.0092
          }
    """
    from sqlalchemy import select as _ssel

    sub = (
        _ssel(Run.cost_usd)
        .where(Run.team == team, Run.status == "success", Run.cost_usd > 0)
        .order_by(Run.id.desc())
        .limit(limit)
    )
    if ctx.workspace_ids is not None:
        if len(ctx.workspace_ids) == 1:
            sub = sub.where(Run.workspace_id == ctx.workspace_ids[0])
        else:
            sub = sub.where(Run.workspace_id.in_(ctx.workspace_ids))

    costs = [float(r[0]) for r in (await session.execute(sub)).fetchall()]

    if not costs:
        return {
            "team": team, "based_on_runs": 0,
            "min_usd": None, "median_usd": None, "p75_usd": None, "max_usd": None,
        }

    costs.sort()
    n = len(costs)

    def _pct(data: list[float], p: float) -> float:
        idx = (len(data) - 1) * p / 100
        lo, hi = int(idx), min(int(idx) + 1, len(data) - 1)
        return round(data[lo] + (data[hi] - data[lo]) * (idx - lo), 6)

    return {
        "team": team,
        "based_on_runs": n,
        "min_usd": round(costs[0], 6),
        "median_usd": _pct(costs, 50),
        "p75_usd": _pct(costs, 75),
        "max_usd": round(costs[-1], 6),
    }


@router.get("/{run_id}", response_model=Run)
async def detail(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    from app.core.run_cache import cache_run, get_cached_run
    cached = await get_cached_run(run_id)
    if cached:
        run = Run.model_validate(cached)
        _assert_run_access(run, ctx)
        return run

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    await cache_run(run_id, run.model_dump())
    return run


@router.get("/{run_id}/stats")
async def run_stats(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return cost and duration for a single run."""
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    return {
        "run_id": run_id,
        "cost_usd": run.cost_usd,
        "duration_s": run.duration_s,
        "status": run.status,
    }


@router.post("/{run_id}/cancel", response_model=Run,
             dependencies=[Depends(require_role("admin", "write"))])
async def cancel(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Mark a running run as cancelled. The background thread continues until it finishes
    naturally — this only updates the DB status immediately."""
    existing = await get_run(session, run_id)
    if not existing:
        raise RunNotFoundError(run_id)
    _assert_run_access(existing, ctx)
    run = await cancel_run(session, run_id)
    if run is None:
        raise RunNotRunningError(run_id, existing.status)
    return run


@router.get("/{run_id}/state")
async def state(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    """Return the full serialized RunResult state for a completed run."""
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    if run.state is None:
        raise StateNotAvailableError(run_id, run.status)
    return run.state


@router.get("/{run_id}/tickets")
async def tickets(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return tickets produced by a specific run."""
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    return await get_run_tickets(session, run_id)


_ENGINE_SKIP_DIRS = {".antcrew"}


def _engine_output_dir(run: "Run") -> Path | None:
    """Return the engine output_dir path if the run has one stored."""
    if run.team != "engine" or not run.state:
        return None
    d = run.state.get("output_dir")
    return Path(d) if d else None


async def _resolve_artifacts(entries: list[dict]) -> list[dict]:
    """Populate 'content' for entries that were offloaded to external artifact storage."""
    if not entries or not any("storage_key" in e for e in entries):
        return entries
    from app.services.artifact_storage import get_backend
    backend = get_backend()
    out = []
    for e in entries:
        if e.get("storage_key") and not e.get("content"):
            try:
                content = await backend.get(e["storage_key"])
            except Exception:
                content = None
            e = {**e, "content": content}
        out.append(e)
    return out


@router.get("/{run_id}/artifacts")
async def artifacts(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return generated artifacts for a completed run.

    For engine runs: lists files produced in output_dir (if persisted to disk).
    For team runs: returns code/devops/doc/test artifact lists from run state.
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    # Engine run path
    output_dir = _engine_output_dir(run)
    if run.team == "engine":
        if output_dir is None:
            # MemoryStore run: content is serialized into Run.state post-completion.
            s = run.state or {}
            if s.get("code_artifacts") or s.get("test_artifacts") or s.get("doc_artifacts"):
                return {
                    "run_id": run_id,
                    "status": run.status,
                    "engine": True,
                    "code_artifacts":   await _resolve_artifacts(s.get("code_artifacts")   or []),
                    "test_artifacts":   await _resolve_artifacts(s.get("test_artifacts")   or []),
                    "doc_artifacts":    await _resolve_artifacts(s.get("doc_artifacts")    or []),
                    "devops_artifacts": [],
                }
            return {"run_id": run_id, "status": run.status, "engine": True,
                    "artifacts": [], "note": "Run used in-memory store — files not persisted"}
        if not output_dir.exists():
            return {"run_id": run_id, "status": run.status, "engine": True,
                    "artifacts": [], "note": f"output_dir not found on server: {output_dir}"}
        file_list = [
            {"file_path": str(p.relative_to(output_dir)), "size_bytes": p.stat().st_size}
            for p in sorted(output_dir.rglob("*"))
            if p.is_file() and not any(part in _ENGINE_SKIP_DIRS for part in p.parts)
        ]
        return {"run_id": run_id, "status": run.status, "engine": True,
                "output_dir": str(output_dir), "artifacts": file_list}

    # Team run path (original behaviour)
    if run.state is None:
        raise StateNotAvailableError(run_id, run.status)
    s = run.state
    return {
        "run_id": run_id,
        "status": run.status,
        "code_artifacts":   await _resolve_artifacts(s.get("code_artifacts")   or []),
        "devops_artifacts": await _resolve_artifacts(s.get("devops_artifacts") or []),
        "doc_artifacts":    await _resolve_artifacts(s.get("doc_artifacts")    or []),
        "test_artifacts":   await _resolve_artifacts(s.get("test_artifacts")   or []),
    }


@router.get("/{run_id}/artifacts.zip")
async def artifacts_zip(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> StreamingResponse:
    """Download all artifacts as a ZIP archive.

    For engine runs: zips every file under output_dir (excluding .antcrew/ metadata).
    For team runs: zips code/test/devops/doc artifacts from run state.
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    buf = io.BytesIO()

    # Engine run path
    output_dir = _engine_output_dir(run)
    if run.team == "engine":
        if output_dir is None:
            s = run.state or {}
            raw_arts = (
                (s.get("code_artifacts") or [])
                + (s.get("test_artifacts") or [])
                + (s.get("doc_artifacts") or [])
            )
            if not raw_arts:
                raise HTTPException(
                    404, "Engine run used in-memory store — artifacts were not persisted to disk"
                )
            all_state_arts = await _resolve_artifacts(raw_arts)
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for art in all_state_arts:
                    path = art.get("file_path") or ""
                    content = art.get("content") or ""
                    if path:
                        zf.writestr(path.lstrip("/"), content)
            buf.seek(0)
            filename = f"antcrew-engine-{run_id[:12]}.zip"
            return StreamingResponse(
                buf, media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if not output_dir.exists():
            raise HTTPException(404, f"output_dir not found on server: {output_dir}")
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(output_dir.rglob("*")):
                if p.is_file() and not any(part in _ENGINE_SKIP_DIRS for part in p.parts):
                    zf.write(p, str(p.relative_to(output_dir)))
        buf.seek(0)
        filename = f"antcrew-engine-{run_id[:12]}.zip"
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Team run path (original behaviour)
    if run.state is None:
        raise StateNotAvailableError(run_id, run.status)
    s = run.state
    raw_all = (
        (s.get("code_artifacts") or [])
        + (s.get("test_artifacts") or [])
        + (s.get("devops_artifacts") or [])
        + (s.get("doc_artifacts") or [])
    )
    all_artifacts = await _resolve_artifacts(raw_all)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for art in all_artifacts:
            if isinstance(art, dict):
                path = art.get("file_path") or art.get("path") or ""
                content = art.get("content") or ""
            else:
                path = getattr(art, "file_path", "") or getattr(art, "path", "") or ""
                content = getattr(art, "content", "") or ""
            if path:
                zf.writestr(path.lstrip("/"), content)
    buf.seek(0)
    filename = f"antcrew-{run_id[:12]}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/blocking-tickets")
async def blocking_tickets(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return open blocking (manual-action) tickets for a run.

    When a run has status ``blocked``, this endpoint shows what a human needs
    to complete before the pipeline can continue.  Resolve each ticket via
    ``PATCH /tickets/{ticket_id}/status`` with ``{"status": "done"}``.
    """
    from sqlmodel import select as _sel

    from app.models.run import Ticket

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    result = await session.exec(
        _sel(Ticket)
        .where(Ticket.run_id == run_id, Ticket.blocking == True)  # noqa: E712
        .order_by(Ticket.created_at)
    )
    return {"run_id": run_id, "status": run.status, "blocking_tickets": list(result.all())}


@router.post(
    "/{run_id}/unblock",
    dependencies=[Depends(require_role("admin"))],
)
async def force_unblock(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Force-unblock a blocked run without requiring ticket resolution (admin only).

    Marks all open blocking tickets for this run as ``done`` and sets
    run.status back to ``running``.  Use this to recover from stuck pipelines
    or when the manual step was completed outside the platform.
    """
    from sqlmodel import select as _sel

    from app.models.run import Ticket
    from app.services.engine_runner import resolve_manual_action

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    if run.status != "blocked":
        raise HTTPException(409, f"Run {run_id!r} is not blocked (status={run.status!r})")

    tickets = (await session.exec(
        _sel(Ticket).where(
            Ticket.run_id == run_id,
            Ticket.blocking == True,  # noqa: E712
            Ticket.status != "done",
        )
    )).all()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for t in tickets:
        t.status = "done"
        t.updated_at = now
        session.add(t)
        resolve_manual_action(t.ticket_id)

    run.status = "running"
    session.add(run)
    await session.commit()
    return {"run_id": run_id, "unblocked_tickets": len(tickets), "status": "running"}


@router.get("/{run_id}/events", response_model=list[DBEvent])
async def events(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    return await get_run_events(session, run_id)


@router.get("/{run_id}/trace.ndjson")
async def trace_ndjson(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> StreamingResponse:
    """Export all events for a run as NDJSON (one JSON object per line).

    Suitable for SIEM ingestion (Splunk, Datadog, CloudWatch Logs) and offline analysis.
    Each line is a self-contained JSON object with at minimum::

        {"run_id": "...", "event_type": "...", "timestamp": 1234567890.0, "payload": {...}}

    The first line is always a ``run_meta`` record with run-level metadata.
    Streaming response; ``Content-Type: application/x-ndjson``.
    """
    import json as _json

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    db_events = await get_run_events(session, run_id)

    def _stream():
        # First line: run metadata record
        meta = {
            "record_type": "run_meta",
            "run_id": run.run_id,
            "team": run.team,
            "status": run.status,
            "cost_usd": run.cost_usd,
            "duration_s": run.duration_s,
            "tokens_in": run.tokens_in,
            "tokens_out": run.tokens_out,
            "model": run.model,
            "workspace_id": run.workspace_id,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        yield _json.dumps(meta, default=str) + "\n"
        for ev in db_events:
            record = {
                "record_type": "event",
                "run_id": ev.run_id,
                "event_type": ev.event_type,
                "timestamp": ev.timestamp,
                "thread_id": ev.thread_id,
                "payload": ev.payload,
            }
            yield _json.dumps(record, default=str) + "\n"

    filename = f"antcrew-trace-{run_id[:12]}.ndjson"
    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/activity")
async def activity(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[dict]:
    """Merged timeline of Event rows and HitlAuditEntry rows for a run, sorted by timestamp.

    Each item has the shape::

        {"ts": "<ISO-8601>", "type": "event"|"hitl", "kind": "<str>", "payload": {...}}

    Events use ``event_type`` as ``kind``; HITL entries use the audit ``action`` as ``kind``.
    """
    from datetime import timezone

    from sqlmodel import select as _sel

    from app.models.run import HitlAuditEntry, HitlReview

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    db_events = await get_run_events(session, run_id)

    # HITL audit entries are linked via HitlReview, which holds the run_id.
    reviews = (await session.exec(
        _sel(HitlReview).where(HitlReview.run_id == run_id)
    )).all()

    review_map: dict[str, Any] = {r.review_id: r for r in reviews}

    audit_entries: list[Any] = []
    if reviews:
        review_ids = [r.review_id for r in reviews]
        audit_entries = (await session.exec(
            _sel(HitlAuditEntry).where(HitlAuditEntry.review_id.in_(review_ids))
        )).all()

    items: list[dict] = []

    for ev in db_events:
        # Event.timestamp is a unix float; convert to UTC ISO-8601.
        ts_dt = datetime.fromtimestamp(ev.timestamp, tz=timezone.utc) if ev.timestamp else \
            ev.recorded_at.replace(tzinfo=timezone.utc)
        items.append({
            "ts": ts_dt.isoformat(),
            "type": "event",
            "kind": ev.event_type,
            "payload": ev.payload,
        })

    for ae in audit_entries:
        review = review_map.get(ae.review_id)
        ts_dt = ae.created_at.replace(tzinfo=timezone.utc)
        items.append({
            "ts": ts_dt.isoformat(),
            "type": "hitl",
            "kind": ae.action,
            "payload": {
                "review_id": ae.review_id,
                "actor_label": ae.actor_label,
                "note": ae.note,
                "agent_name": review.agent_name if review else None,
                "decision": review.decision if review else None,
            },
        })

    items.sort(key=lambda x: x["ts"])
    return items


class _ReplayRequest(BaseModel):
    model: Optional[str] = None          # override model; defaults to original
    goal: Optional[str] = None           # override goal description
    conditions: Optional[list[str]] = None  # override conditions list


@router.post(
    "/{run_id}/replay",
    status_code=202,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def replay(
    run_id: str,
    body: _ReplayRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Re-run a completed engine run with optional parameter overrides.

    Loads goal, model, conditions, and output_dir from the original run's state.
    The new run starts fresh (no resume) so artifacts are regenerated from scratch.
    Returns the new run_id immediately; the run executes in the background.
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    if run.team != "engine":
        raise HTTPException(400, "Replay is only supported for engine runs")

    state = run.state or {}
    if not state.get("goal"):
        raise HTTPException(422, "Original run has no goal metadata — cannot replay")

    from pathlib import Path as _Path

    from app.services.engine_runner import dispatch_engine

    orig_output_dir = state.get("output_dir")
    new_run_id = await dispatch_engine(
        goal=body.goal or state["goal"],
        model=body.model or "claude",
        conditions=body.conditions or state.get("conditions_expected") or [],
        full=True,
        output_dir=_Path(orig_output_dir).parent / uuid.uuid4().hex if orig_output_dir else None,
        workspace_id=run.workspace_id,
        created_by=ctx.created_by,
    )
    return {"run_id": new_run_id, "replayed_from": run_id}


@router.get("/margin")
async def margin_stats(
    days: int = Query(30, ge=1, le=365),
    client_label: Optional[str] = Query(None, description="Filter by client label"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return cost vs. billed breakdown per client label for margin analysis.

    Shows actual LLM cost (``cost_usd``), amount billed to the client (``billed_usd``),
    gross margin in USD and as a percentage.  Use ``PATCH /runs/{run_id}/billing`` to
    set the billed amount on individual runs.

    Scoped to the API key's workspace.  Admins can filter by ``client_label``; viewer
    keys automatically see only their own ``client_label``.

    Example::

        GET /runs/margin?days=30
        → {
            "period_days": 30,
            "clients": [
                {"client_label": "acme", "runs": 12, "cost_usd": 1.23,
                 "billed_usd": 6.00, "margin_usd": 4.77, "margin_pct": 79.5}
            ]
          }
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from datetime import timezone as _tz

    from sqlalchemy import func as _func
    from sqlalchemy import select as _ssel

    cutoff = _dt.now(_tz.utc) - _td(days=days)

    # Effective client label filter: viewer keys are always scoped to theirs
    effective_label = ctx.client_label or client_label

    stmt = (
        _ssel(
            Run.client_label,
            _func.count(Run.id).label("runs"),
            _func.sum(Run.cost_usd).label("cost_usd"),
            _func.sum(Run.billed_usd).label("billed_usd"),
        )
        .where(Run.created_at >= cutoff)
        .group_by(Run.client_label)
        .order_by(_func.sum(Run.cost_usd).desc())
    )

    if ctx.workspace_ids is not None:
        if len(ctx.workspace_ids) == 1:
            stmt = stmt.where(Run.workspace_id == ctx.workspace_ids[0])
        else:
            stmt = stmt.where(Run.workspace_id.in_(ctx.workspace_ids))

    if effective_label is not None:
        stmt = stmt.where(Run.client_label == effective_label)

    rows = (await session.execute(stmt)).all()

    clients = []
    for r in rows:
        cost = float(r.cost_usd or 0)
        billed = float(r.billed_usd or 0)
        margin_usd = round(billed - cost, 6)
        margin_pct = round((margin_usd / billed) * 100, 2) if billed > 0 else None
        clients.append({
            "client_label": r.client_label or "(untagged)",
            "runs": int(r.runs or 0),
            "cost_usd": round(cost, 6),
            "billed_usd": round(billed, 6) if r.billed_usd is not None else None,
            "margin_usd": margin_usd if r.billed_usd is not None else None,
            "margin_pct": margin_pct,
        })

    return {"period_days": days, "clients": clients}


class _BillingUpdate(BaseModel):
    billed_usd: float


@router.patch(
    "/{run_id}/billing",
    response_model=Run,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def update_billing(
    run_id: str,
    body: _BillingUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Run:
    """Set the amount billed to the client for a run.

    This is the number you invoiced the client for this specific pipeline run.
    The difference between ``billed_usd`` and ``cost_usd`` is your gross margin.
    Use ``GET /runs/margin`` to see aggregate margin per client label.
    """
    if body.billed_usd < 0:
        raise HTTPException(422, "billed_usd must be >= 0")
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    run.billed_usd = round(body.billed_usd, 6)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.get("/{run_id}/attestation")
async def attestation(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Any:
    """Download a cryptographically signed provenance document for a run.

    The attestation is a self-contained JSON file that proves:
    - Which agent configuration (governance hash) produced this output
    - The exact team composition at the time of the run
    - Platform version and timestamp

    The ``document_hash`` field is SHA-256 of the document body (excluding the hash
    field itself) — an auditor can recompute it to verify the file was not tampered with::

        import hashlib, json
        doc = json.load(open("attestation.json"))
        body = {k: v for k, v in doc.items() if k != "document_hash"}
        assert doc["document_hash"] == "sha256:" + hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    import json as _json

    from app.api.compliance import _build_attestation

    body = await _build_attestation(run, session)

    content = _json.dumps(body, indent=2, default=str)
    filename = f"attestation-{run_id[:12]}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/governance")
async def governance(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return governance hash map for all agents that participated in a run.

    The ``governance_hash`` is a deterministic SHA-256[:16] of each agent's
    configuration (name, role, stage, tool names).  Identical hashes across
    runs prove the agent config was not modified since the last security review.

    The ``team_hash`` is SHA-256[:16] of the sorted set of individual agent hashes
    — a single value that certifies the entire team composition hasn't changed::

        GET /runs/{run_id}/governance
        → {
            "run_id": "...",
            "team": "DevTeam",
            "team_hash": "sha256:abc123def456...",
            "agents": [
                {"agent_name": "BA", "governance_hash": "a1b2c3d4e5f6...", "stage": "analysis"},
                ...
            ]
          }
    """
    import hashlib as _hl

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    from sqlmodel import select as _sel

    from app.models.run import AgentEvent as _AgentEvent
    from app.models.run import Event as _DBEvent

    end_events = (await session.exec(
        _sel(_DBEvent)
        .where(_DBEvent.run_id == run_id, _DBEvent.event_type == "agent.end")
        .order_by(_DBEvent.id)
    )).all()

    agent_rows = (await session.exec(
        _sel(_AgentEvent).where(_AgentEvent.run_id == run_id).order_by(_AgentEvent.id)
    )).all()

    gov_map: dict[str, dict] = {}
    for ev in end_events:
        p = ev.payload or {}
        name = p.get("agent_name", "")
        if name:
            gov_map[name] = {
                "governance_hash": p.get("governance_hash", ""),
                "stage": p.get("stage", ""),
                "model": p.get("model", ""),
            }

    seen: set[str] = set()
    agents: list[dict] = []
    for row in agent_rows:
        if row.agent_name in seen:
            continue
        seen.add(row.agent_name)
        gov = gov_map.get(row.agent_name, {})
        agents.append({
            "agent_name": row.agent_name,
            "governance_hash": gov.get("governance_hash", ""),
            "stage": gov.get("stage", ""),
            "model": gov.get("model", ""),
        })

    from antcrew import __version__ as _ev

    hashes = sorted(a["governance_hash"] for a in agents if a["governance_hash"])
    team_hash = (
        "sha256:" + _hl.sha256("|".join(hashes).encode()).hexdigest()[:16]
        if hashes else ""
    )

    return {
        "run_id": run_id,
        "team": run.team,
        "engine_version": _ev,
        "team_hash": team_hash,
        "agents": agents,
    }


@router.get("/{run_id}/certificate")
async def certificate(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Any:
    """Download a Certified Agent certificate for a run.

    Compares each agent's ``governance_hash`` against the registered approved
    hashes for this workspace (POST /compliance/approved-hashes).

    ``certification_status`` values:

    - ``certified`` — every agent with a registered hash matched
    - ``drifted``   — one or more agents produced a hash that differs from the approved one
    - ``unchecked`` — no approved hashes are registered for this team; cannot certify

    The certificate is a self-contained JSON file signed with ``document_hash``
    (SHA-256 of the document body) and, when ATTESTATION_HMAC_SECRET is
    configured, ``hmac_sha256``.
    """
    import hashlib as _hl
    import hmac as _hmac
    import json as _json

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    ws_id = run.workspace_id

    # Collect governance hashes from agent.end events
    from sqlmodel import select as _sel

    from app.models.run import AgentEvent as _AgentEvent
    from app.models.run import Event as _DBEvent

    end_events = (await session.exec(
        _sel(_DBEvent)
        .where(_DBEvent.run_id == run_id, _DBEvent.event_type == "agent.end")
        .order_by(_DBEvent.id)
    )).all()
    agent_rows = (await session.exec(
        _sel(_AgentEvent).where(_AgentEvent.run_id == run_id).order_by(_AgentEvent.id)
    )).all()

    gov_map: dict[str, str] = {}
    for ev in end_events:
        p = ev.payload or {}
        name = p.get("agent_name", "")
        if name:
            gov_map[name] = p.get("governance_hash", "")

    # Load approved hashes for this workspace + team
    from app.models.compliance import ApprovedAgentHash as _AAH
    approved_rows = (await session.exec(
        _sel(_AAH).where(
            _AAH.workspace_id == ws_id,
            _AAH.team == run.team,
            _AAH.active.is_(True),
        )
    )).all()
    approved_map: dict[str, str] = {r.agent_name: r.governance_hash for r in approved_rows}

    seen: set[str] = set()
    agent_certs: list[dict] = []
    for row in agent_rows:
        if row.agent_name in seen:
            continue
        seen.add(row.agent_name)
        actual = gov_map.get(row.agent_name, "")
        approved = approved_map.get(row.agent_name)
        if approved is None:
            status = "unchecked"
        elif actual == approved:
            status = "certified"
        else:
            status = "drifted"
        agent_certs.append({
            "agent_name": row.agent_name,
            "governance_hash": actual,
            "approved_hash": approved,
            "status": status,
        })

    # Derive overall status
    statuses = {a["status"] for a in agent_certs}
    if "drifted" in statuses:
        overall = "drifted"
    elif approved_map and statuses <= {"certified"}:
        overall = "certified"
    else:
        overall = "unchecked"

    from antcrew import __version__ as _ev

    body: dict = {
        "schema_version": "1.0",
        "certificate_type": "agent_certification",
        "run_id": run_id,
        "team": run.team,
        "workspace_id": ws_id,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "certification_status": overall,
        "agents": agent_certs,
        "engine_version": _ev,
        "certified_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    body["document_hash"] = "sha256:" + _hl.sha256(
        _json.dumps(body, sort_keys=True).encode()
    ).hexdigest()

    _secret = __import__("os").environ.get("ATTESTATION_HMAC_SECRET", "")
    if _secret:
        body["hmac_sha256"] = "hmac-sha256:" + _hmac.new(
            _secret.encode("utf-8"),
            _json.dumps(body, sort_keys=True).encode("utf-8"),
            "sha256",
        ).hexdigest()

    content = _json.dumps(body, indent=2, default=str)
    filename = f"certificate-{run_id[:12]}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/fallbacks")
async def fallback_events(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return LLM fallback events for a run.

    Each entry corresponds to one model failing and the next in the chain being
    tried.  Empty list when FallbackLLM was not used or no fallback occurred.

    Schema per event::

        {
          "agent_name": "BA",
          "failed_model": "AnthropicModel",
          "next_model": "OpenAIModel",
          "error": "RateLimitError: ...",
          "ts": 1234567890.123
        }
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    from sqlmodel import select as _sel

    from app.models.run import Event as _DBEvent

    rows = (await session.exec(
        _sel(_DBEvent)
        .where(_DBEvent.run_id == run_id, _DBEvent.event_type == "llm.fallback")
        .order_by(_DBEvent.timestamp)
    )).all()

    events = [
        {
            "agent_name": r.payload.get("agent_name", ""),
            "failed_model": r.payload.get("failed_model", ""),
            "next_model": r.payload.get("next_model", ""),
            "error": r.payload.get("error", ""),
            "ts": r.timestamp,
        }
        for r in rows
    ]
    return {"run_id": run_id, "fallbacks": events, "count": len(events)}


@router.get("/{run_id}/stream")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> StreamingResponse:
    """SSE stream of run events with replay-on-reconnect support.

    Streams events as Server-Sent Events (``text/event-stream``).  Each event
    carries an ``id:`` field equal to the DB row primary key so clients can
    resume after a disconnect::

        GET /runs/{run_id}/stream
        Last-Event-ID: 42          # replay from event 43 onwards

    Event format::

        id: <db_row_id>
        event: <event_type>
        data: {"run_id": "...", "event_type": "...", "timestamp": ..., "payload": {...}}

    A final ``run.end`` event is emitted when the run reaches a terminal status
    (``success``, ``error``, or ``cancelled``).
    """
    import json as _json

    from sqlmodel import select as _sel
    from sqlmodel.ext.asyncio.session import AsyncSession as _AsyncSession

    from app.core.database import engine as _db_engine

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    since_id: int = 0
    if last_event_id and last_event_id.isdigit():
        since_id = int(last_event_id)

    async def _generate():
        q = await _sse_broadcaster.subscribe(run_id, since_id, _db_engine, _AsyncSession)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if msg["type"] == "catchup_needed":
                    # Another poller is already running; catch up events we missed.
                    async with _AsyncSession(_db_engine, expire_on_commit=False) as sess:
                        rows = (await sess.exec(
                            _sel(DBEvent)
                            .where(DBEvent.run_id == run_id, DBEvent.id > msg["since"],
                                   DBEvent.id <= msg["cursor"])
                            .order_by(DBEvent.id)
                            .limit(500)
                        )).all()
                    for ev in rows:
                        data = _json.dumps(
                            {"run_id": ev.run_id, "event_type": ev.event_type,
                             "timestamp": ev.timestamp, "thread_id": ev.thread_id,
                             "payload": ev.payload},
                            default=str,
                        )
                        yield f"id: {ev.id}\nevent: {ev.event_type}\ndata: {data}\n\n"

                elif msg["type"] == "event":
                    ev = msg["ev"]
                    data = _json.dumps(
                        {"run_id": ev.run_id, "event_type": ev.event_type,
                         "timestamp": ev.timestamp, "thread_id": ev.thread_id,
                         "payload": ev.payload},
                        default=str,
                    )
                    yield f"id: {ev.id}\nevent: {ev.event_type}\ndata: {data}\n\n"

                elif msg["type"] == "end":
                    terminal_data = _json.dumps({"run_id": run_id, "status": msg["status"]})
                    yield f"event: run.end\ndata: {terminal_data}\n\n"
                    break
        finally:
            await _sse_broadcaster.unsubscribe(run_id, q)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{run_id}/agents")
async def get_run_agents(
    run_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Return per-agent cost/token breakdown for a run.

    Each entry corresponds to one agent invocation captured from the agent.end event.
    Returns 404 if the run is not found or not accessible; empty list if no agent events
    have been recorded yet (e.g. run still in progress).
    """
    import json as _json

    from sqlmodel import select as _sel

    from app.models.run import AgentEvent

    run = await get_run(session, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    _assert_run_access(run, ctx)

    rows = (await session.exec(
        _sel(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.id)
    )).all()

    return {
        "run_id": run_id,
        "agents": [
            {
                "agent_name": r.agent_name,
                "duration_s": r.duration_s,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "cost_usd": r.cost_usd,
                "produced_keys": _json.loads(r.produced_keys or "[]"),
                "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
            }
            for r in rows
        ],
    }


@router.get("/{run_id}/comparison")
async def get_run_comparison(
    run_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Return ComparisonLLM results for a run, if the run used multi-model comparison.

    The comparison log is stored in the run's state under the key ``_comparison_log``
    by the runner after the team completes. Returns 404 if the run is not found;
    404 with detail "no comparison data" if the run did not use ComparisonLLM.
    """
    run = await get_run(session, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    _assert_run_access(run, ctx)

    cmp_log = (run.state or {}).get("_comparison_log")
    if not cmp_log:
        raise HTTPException(404, "No comparison data for this run")

    return {"run_id": run_id, "comparison_log": cmp_log}
