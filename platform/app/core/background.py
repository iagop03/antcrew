"""Background task loops for antcrew-platform.

All functions run as long-lived asyncio tasks started in the FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import select

log = logging.getLogger(__name__)


async def _do_run_retention(engine) -> int:
    """Purge Run rows (and their Events, Tickets, and HITL records) for workspaces
    that have data_retention_days set. Only deletes terminal runs (success/error/cancelled).
    Returns the total number of Run rows deleted.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete as sa_delete
    from sqlmodel import col
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.review import HitlAuditEntry, HitlReview, HitlReviewAssignee
    from app.models.run import Event as DBEvent
    from app.models.run import Run, Ticket
    from app.models.workspace import Workspace

    deleted = 0
    async with AsyncSession(engine, expire_on_commit=False) as session:
        ws_rows = (await session.exec(
            select(Workspace).where(Workspace.data_retention_days.isnot(None))
        )).all()

        now = datetime.now(timezone.utc)
        for ws in ws_rows:
            cutoff = now - timedelta(days=ws.data_retention_days)
            runs = (await session.exec(
                select(Run)
                .where(Run.workspace_id == ws.id)
                .where(Run.created_at <= cutoff)
                .where(col(Run.status).in_(["success", "error", "cancelled"]))
                .limit(500)
            )).all()
            if not runs:
                continue

            run_ids = [r.run_id for r in runs]

            reviews = (await session.exec(
                select(HitlReview).where(col(HitlReview.run_id).in_(run_ids))
            )).all()
            review_ids = [r.review_id for r in reviews]

            if review_ids:
                await session.execute(
                    sa_delete(HitlAuditEntry).where(col(HitlAuditEntry.review_id).in_(review_ids))
                )
                await session.execute(
                    sa_delete(HitlReviewAssignee).where(col(HitlReviewAssignee.review_id).in_(review_ids))
                )
                await session.execute(
                    sa_delete(HitlReview).where(col(HitlReview.run_id).in_(run_ids))
                )

            await session.execute(
                sa_delete(DBEvent).where(col(DBEvent.run_id).in_(run_ids))
            )
            await session.execute(
                sa_delete(Ticket).where(col(Ticket.run_id).in_(run_ids))
            )
            await session.execute(
                sa_delete(Run).where(col(Run.run_id).in_(run_ids))
            )

            deleted += len(runs)

        if deleted:
            await session.commit()

    return deleted


async def _do_retention(engine, cutoff) -> tuple[int, int]:
    """Delete stale rows older than *cutoff*. Returns (deliveries_deleted, events_deleted).

    Only terminal webhook deliveries (delivered, failed) are eligible — pending/retrying
    rows are kept regardless of age.
    """
    from sqlmodel import col
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.run import Event as DBEvent
    from app.models.run import WebhookDelivery

    async with AsyncSession(engine, expire_on_commit=False) as session:
        stale_deliveries = (await session.exec(
            select(WebhookDelivery)
            .where(WebhookDelivery.created_at <= cutoff)
            .where(col(WebhookDelivery.status).in_(["delivered", "failed"]))
        )).all()
        for d in stale_deliveries:
            await session.delete(d)

        stale_events = (await session.exec(
            select(DBEvent).where(DBEvent.recorded_at <= cutoff)
        )).all()
        for e in stale_events:
            await session.delete(e)

        if stale_deliveries or stale_events:
            await session.commit()

    return len(stale_deliveries), len(stale_events)


async def _hitl_cleanup_loop() -> None:
    """Mark stale pending reviews as 'timeout' every 5 minutes."""
    import os as _os
    from datetime import timedelta

    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine as _engine
    from app.models.run import HitlAuditEntry, HitlReview

    timeout_s = float(_os.environ.get("HITL_TIMEOUT_S", "3600"))
    log.info("hitl cleanup started (timeout=%.0fs)", timeout_s)
    while True:
        await asyncio.sleep(300)
        try:
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_s)
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                result = await session.exec(
                    select(HitlReview).where(
                        HitlReview.status == "pending",
                        HitlReview.created_at <= cutoff,
                    )
                )
                stale = result.all()
                now = datetime.now(timezone.utc)
                for r in stale:
                    r.status = "timeout"
                    r.resolved_at = now
                    session.add(r)
                    session.add(HitlAuditEntry(
                        review_id=r.review_id,
                        actor_label=None,
                        action="timed_out",
                        note=f"Auto-timed-out after {timeout_s:.0f}s",
                    ))
                if stale:
                    await session.commit()
                    log.info("hitl cleanup: marked %d stale review(s) as timeout", len(stale))
        except Exception as exc:
            log.warning("hitl cleanup error: %s", exc)


async def _rotate_tracelog(retention_days: int) -> None:
    """Rotate the TraceLog SQLite file when it is older than *retention_days*.

    Rotation: gzip the file to <path>.YYYYMMDD.gz and delete the original,
    allowing the next run to create a fresh database. The gzip archive preserves
    the full trace history for compliance / replay without eating disk indefinitely.
    Skips silently when ANTCREW_TRACELOG_ENABLED is false or the file does not exist.
    """
    import gzip
    import os as _os
    import shutil
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    if _os.environ.get("ANTCREW_TRACELOG_ENABLED", "true").lower() in ("0", "false", "no"):
        return

    tl_path = Path(_os.environ.get("ANTCREW_TRACELOG_PATH", "./antcrew_trace.db"))
    if not tl_path.exists():
        return

    mtime = datetime.fromtimestamp(tl_path.stat().st_mtime, tz=timezone.utc)
    age_days = (datetime.now(timezone.utc) - mtime).days
    if age_days < retention_days:
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    archive_path = tl_path.with_suffix(f".{stamp}.db.gz")
    with tl_path.open("rb") as f_in, gzip.open(archive_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    tl_path.unlink()
    log.info(
        "tracelog rotated: %s → %s (%d days old)",
        tl_path.name, archive_path.name, age_days,
    )


async def _data_retention_loop() -> None:
    """Delete terminal WebhookDelivery and old Event rows on a daily cadence.

    Retention window is configurable via DATA_RETENTION_DAYS (default: 30).
    """
    import os as _os
    from datetime import timedelta

    from app.core.database import engine as _engine

    retention_days = int(_os.environ.get("DATA_RETENTION_DAYS", "30"))
    log.info("data retention started (retention=%dd)", retention_days)
    while True:
        await asyncio.sleep(3600)
        try:
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            deleted_d, deleted_e = await _do_retention(_engine, cutoff)
            if deleted_d or deleted_e:
                log.info(
                    "data retention: deleted %d deliveries, %d events",
                    deleted_d, deleted_e,
                )
        except Exception as exc:
            log.warning("data retention error: %s", exc)

        try:
            deleted_runs = await _do_run_retention(_engine)
            if deleted_runs:
                log.info("data retention: purged %d run(s) per workspace policy", deleted_runs)
        except Exception as exc:
            log.warning("run retention error: %s", exc)

        try:
            await _rotate_tracelog(retention_days)
        except Exception as exc:
            log.warning("tracelog rotation error: %s", exc)


async def _velocity_check_loop() -> None:
    """Warn when a managed workspace spends faster than MANAGED_VELOCITY_USD_PER_HOUR.

    Runs every 5 minutes. Threshold configurable via env var (default $5/h).
    These log warnings are the primary signal — block the workspace via the
    admin panel if the velocity looks like abuse.
    """
    import os as _os
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func as _func
    from sqlalchemy import select as _sa_select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine as _engine
    from app.models.run import Run
    from app.models.workspace import Workspace

    threshold = float(_os.environ.get("MANAGED_VELOCITY_USD_PER_HOUR", "5.0"))
    interval  = int(_os.environ.get("MANAGED_VELOCITY_CHECK_MINUTES", "5"))
    window    = timedelta(hours=1)

    log.info(
        "velocity check started (threshold=$%.2f/h, check every %dm)",
        threshold, interval,
    )
    while True:
        await asyncio.sleep(interval * 60)
        try:
            cutoff = datetime.now(timezone.utc) - window
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                rows = (await session.exec(
                    _sa_select(
                        Run.workspace_id,
                        _func.sum(Run.cost_usd).label("velocity_usd"),
                        _func.count(Run.run_id).label("run_count"),
                    )
                    .join(Workspace, Workspace.id == Run.workspace_id)
                    .where(Workspace.llm_key_mode == "managed")
                    .where(Workspace.is_blocked.is_(False))
                    .where(Run.created_at >= cutoff)
                    .where(Run.cost_usd.isnot(None))
                    .group_by(Run.workspace_id)
                    .having(_func.sum(Run.cost_usd) > threshold)
                )).all()

                for r in rows:
                    log.warning(
                        "velocity_alert ws=%d  $%.4f billed in last hour  "
                        "(%d run(s), threshold=$%.2f) — "
                        "review at /admin → Workspaces and block if abusive",
                        r.workspace_id, float(r.velocity_usd), r.run_count, threshold,
                    )
        except Exception as exc:
            log.warning("velocity check error: %s", exc)


async def _eval_scheduler_loop() -> None:
    """Fire due EvalSchedule entries every 60 seconds."""
    from app.api.eval_schedules import dispatch_due_schedules
    from app.core.database import engine as _engine
    log.info("eval scheduler started")
    while True:
        await asyncio.sleep(60)
        try:
            n = await dispatch_due_schedules(_engine)
            if n:
                log.info("eval scheduler dispatched %d run(s)", n)
        except Exception as exc:
            log.warning("eval scheduler error: %s", exc)


async def _run_scheduler_loop() -> None:
    """Fire due RunSchedule entries every 60 seconds."""
    from app.api.run_schedules import dispatch_due_run_schedules
    from app.core.database import engine as _engine
    log.info("run scheduler started")
    while True:
        await asyncio.sleep(60)
        try:
            n = await dispatch_due_run_schedules(_engine)
            if n:
                log.info("run scheduler dispatched %d engine run(s)", n)
        except Exception as exc:
            log.warning("run scheduler error: %s", exc)


async def _budget_alert_loop() -> None:
    """Fire Slack budget alerts when a workspace reaches 80% or 100% of max_cost_usd.

    Runs hourly. Alerts are deduplicated in-memory per process restart — if the
    platform restarts, alerts will re-fire on the next check. Each threshold fires
    once per process lifetime, not once per crossing, to avoid flood on slow spend.
    """
    import httpx as _httpx
    from sqlalchemy import func as _func
    from sqlalchemy import select as _sa_select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine as _engine
    from app.models.run import Run
    from app.models.workspace import Workspace

    _fired: set[str] = set()  # "ws:{id}:{threshold}" e.g. "ws:3:80" | "ws:3:100"

    log.info("budget alert loop started")
    while True:
        await asyncio.sleep(3600)
        try:
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                ws_rows = (await session.exec(
                    select(Workspace).where(
                        Workspace.max_cost_usd.isnot(None),
                        Workspace.is_blocked.is_(False),
                    )
                )).all()

                for ws in ws_rows:
                    if not ws.max_cost_usd:
                        continue
                    # Use live SUM from Run table for accuracy
                    row = (await session.execute(
                        _sa_select(_func.coalesce(_func.sum(Run.cost_usd), 0.0))
                        .where(Run.workspace_id == ws.id)
                        .where(Run.cost_usd.isnot(None))
                    )).scalar()
                    total = float(row or 0.0)
                    pct = total / ws.max_cost_usd

                    for threshold, label in ((0.80, "80%"), (1.00, "100%")):
                        key = f"ws:{ws.id}:{int(threshold * 100)}"
                        if pct >= threshold and key not in _fired:
                            _fired.add(key)
                            log.warning(
                                "budget_alert ws=%d slug=%s  %.2f%% of $%.2f consumed "
                                "($%.4f used)",
                                ws.id, ws.slug, pct * 100, ws.max_cost_usd, total,
                            )
                            if ws.slack_webhook_url:
                                emoji = ":rotating_light:" if threshold >= 1.0 else ":warning:"
                                text = (
                                    f"{emoji} *Budget alert — {ws.name}* "
                                    f"has consumed {label} of its ${ws.max_cost_usd:.2f} limit "
                                    f"(${total:.4f} used). "
                                    + ("New runs are now blocked." if threshold >= 1.0 else "")
                                )
                                try:
                                    async with _httpx.AsyncClient(timeout=10.0) as hc:
                                        await hc.post(ws.slack_webhook_url, json={"text": text})
                                except Exception as slack_exc:
                                    log.warning(
                                        "budget_alert: Slack webhook failed for ws=%d: %s",
                                        ws.id, slack_exc,
                                    )
        except Exception as exc:
            log.warning("budget alert error: %s", exc)


async def _compliance_digest_loop() -> None:
    """Send daily compliance digest emails to compliance_viewer API key holders.

    Runs every 24 hours. For each workspace with compliance_pack_enabled, queries
    API keys with role='compliance_viewer' that have an email address set, then
    sends them a digest of runs completed in the past 24 hours.

    Requires SMTP_HOST to be configured — silently skips when email is not set up.
    """
    from datetime import datetime, timedelta, timezone

    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine as _engine
    from app.models.run import ApiKey, Run
    from app.models.workspace import Workspace

    log.info("compliance digest loop started")
    # Stagger first run by 10 minutes to let the app finish starting up
    await asyncio.sleep(600)
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                workspaces = (await session.exec(
                    select(Workspace).where(Workspace.compliance_pack_enabled.is_(True))
                )).all()

                for ws in workspaces:
                    # Find compliance_viewer API keys with emails
                    viewer_keys = (await session.exec(
                        select(ApiKey).where(
                            ApiKey.workspace_id == ws.id,
                            ApiKey.role == "compliance_viewer",
                            ApiKey.email.isnot(None),
                            ApiKey.revoked_at.is_(None),
                        )
                    )).all()
                    if not viewer_keys:
                        continue

                    # Find completed runs in past 24h
                    runs = (await session.exec(
                        select(Run).where(
                            Run.workspace_id == ws.id,
                            Run.status == "done",
                            Run.finished_at >= cutoff,
                        ).order_by(Run.finished_at.desc())
                    )).all()
                    if not runs:
                        continue

                    run_dicts = [
                        {
                            "run_id": r.run_id,
                            "team": r.team,
                            "status": r.status,
                            "cost_usd": r.cost_usd,
                        }
                        for r in runs
                    ]

                    from app.services.email import send_compliance_digest
                    base_url = __import__("os").environ.get("BASE_URL", "")
                    for key in viewer_keys:
                        await send_compliance_digest(
                            to_email=key.email,
                            workspace_name=ws.name,
                            workspace_slug=ws.slug,
                            runs=run_dicts,
                            base_url=base_url,
                        )
                    log.info(
                        "compliance digest: sent to %d viewer(s) for ws=%d (%d runs)",
                        len(viewer_keys), ws.id, len(runs),
                    )
        except Exception as exc:
            log.warning("compliance digest error: %s", exc)
        await asyncio.sleep(86400)  # 24 hours


async def _discovery_session_cleanup_loop() -> None:
    """Delete stale DiscoverySession rows every 6 hours.

    A session is eligible if updated_at has not changed in DISCOVERY_SESSION_TTL_DAYS
    (default 7). Covers both abandoned in-progress sessions and completed ones.
    """
    import os as _os
    from datetime import datetime, timedelta, timezone

    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.core.database import engine as _engine
    from app.models.discovery import DiscoverySession

    ttl_days = int(_os.environ.get("DISCOVERY_SESSION_TTL_DAYS", "7"))
    log.info("discovery session cleanup started (ttl=%dd)", ttl_days)
    while True:
        await asyncio.sleep(21600)  # 6 hours
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                stale = (await session.exec(
                    select(DiscoverySession).where(DiscoverySession.updated_at <= cutoff)
                )).all()
                for s in stale:
                    await session.delete(s)
                if stale:
                    await session.commit()
                    log.info("discovery cleanup: deleted %d stale session(s)", len(stale))
        except Exception as exc:
            log.warning("discovery cleanup error: %s", exc)
