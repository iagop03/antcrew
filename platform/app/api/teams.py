"""Team governance history and eval score correlation endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context, require_api_key
from app.core.database import get_session
from app.core.license_gate import require_feature
from app.models.integrations import TeamSnapshot

router = APIRouter(
    prefix="/teams",
    tags=["teams"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{team}/history", dependencies=[Depends(require_feature("team_history"))])
async def team_history(
    team: str,
    days: int = Query(90, ge=1, le=365, description="Lookback window for eval scores"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Team governance hash timeline with eval score correlation.

    Returns all governance snapshots for the team (newest first). For each
    snapshot, shows the mean eval score recorded while that configuration was
    active. If a configuration change correlates with a score drop ≥10pp, a
    regression_warning is emitted so the team can identify which change caused
    the regression.

    Example::

        GET /teams/DevTeam/history?days=90
        → {
            "team": "DevTeam",
            "snapshots": [
              {
                "snapshot_id": 3,
                "team_hash": "4f2e8a1c9b3d7e5f",
                "created_at": "2026-08-10T09:00:00",
                "agents": [...],
                "eval_count": 5,
                "avg_score": 0.71
              },
              ...
            ],
            "regression_warnings": [
              {
                "team_hash": "4f2e8a1c9b3d7e5f",
                "changed_at": "2026-08-10T09:00:00",
                "previous_hash": "9a1c3f8b2e4d6c7a",
                "score_before": 0.84,
                "score_after": 0.71,
                "drop_pp": 13.0,
                "message": "Score dropped 13.0pp after configuration change on 2026-08-10."
              }
            ]
          }
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func as _func
    from sqlalchemy import select as _ssel

    from app.models.eval import EvalRun

    ws_id = ctx.workspace_id

    snapshots = (await session.exec(
        select(TeamSnapshot)
        .where(TeamSnapshot.team_name == team)
        .where(TeamSnapshot.workspace_id == ws_id)
        .order_by(TeamSnapshot.created_at.asc())
    )).all()

    if not snapshots:
        return {"team": team, "snapshots": [], "regression_warnings": []}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # For each snapshot compute mean eval score during its active period.
    # Period: [snapshot.created_at, next_snapshot.created_at or now]
    now = datetime.now(timezone.utc)

    enriched = []
    for i, snap in enumerate(snapshots):
        period_start = snap.created_at
        period_end = snapshots[i + 1].created_at if i + 1 < len(snapshots) else now

        stmt = (
            _ssel(
                _func.avg(EvalRun.overall_score).label("avg"),
                _func.count(EvalRun.id).label("cnt"),
            )
            .where(EvalRun.team == team)
            .where(EvalRun.overall_score.is_not(None))
            .where(EvalRun.created_at >= period_start)
            .where(EvalRun.created_at < period_end)
        )
        if ws_id is not None:
            stmt = stmt.where(EvalRun.workspace_id == ws_id)

        row = (await session.execute(stmt)).first()
        avg_score = float(row.avg) if row and row.avg is not None else None
        eval_count = int(row.cnt) if row and row.cnt else 0

        enriched.append({
            "snapshot_id": snap.id,
            "team_hash": snap.team_hash,
            "label": snap.label,
            "created_at": snap.created_at.isoformat(),
            "agents": snap.agents_json or [],
            "eval_count": eval_count,
            "avg_score": round(avg_score, 4) if avg_score is not None else None,
        })

    # Newest first for the response
    ordered = list(reversed(enriched))

    # Detect regression: consecutive snapshots where score dropped ≥10pp
    regression_warnings = []
    for i in range(len(ordered) - 1):
        curr = ordered[i]       # newer
        prev = ordered[i + 1]   # older
        if (
            curr["avg_score"] is not None
            and prev["avg_score"] is not None
            and curr["eval_count"] >= 2
            and prev["eval_count"] >= 2
        ):
            drop = prev["avg_score"] - curr["avg_score"]
            if drop >= 0.10:
                regression_warnings.append({
                    "team_hash": curr["team_hash"],
                    "changed_at": curr["created_at"],
                    "previous_hash": prev["team_hash"],
                    "score_before": prev["avg_score"],
                    "score_after": curr["avg_score"],
                    "drop_pp": round(drop * 100, 1),
                    "message": (
                        f"Score dropped {round(drop * 100, 1)}pp after configuration "
                        f"change on {curr['created_at'][:10]}. "
                        f"Previous stable hash: {prev['team_hash']}"
                    ),
                })

    return {
        "team": team,
        "snapshots": ordered,
        "regression_warnings": regression_warnings,
    }
