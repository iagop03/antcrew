"""Celery periodic tasks that replace the asyncio scheduler loops.

These tasks run under celery-beat (single process), eliminating the
double-fire issue that occurs when multiple uvicorn workers each launch
their own asyncio scheduler loops.

Each task creates its own SQLAlchemy engine scoped to the asyncio.run()
call, avoiding the event-loop sharing problem that arises when reusing
the module-level engine across different loops.
"""
from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

log = logging.getLogger(__name__)


def _make_engine():
    """Create a fresh async engine for use inside asyncio.run().

    The module-level engine in database.py is bound to the FastAPI event loop;
    Celery tasks run in separate threads with their own loops, so we create an
    independent engine here and dispose of it after each task.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.database import DB_URL, _connect_args, _pool_kwargs
    return create_async_engine(DB_URL, connect_args=_connect_args, **_pool_kwargs)


@celery_app.task(name="antcrew.eval_scheduler", bind=True, max_retries=3)
def run_eval_scheduler(self):
    """Dispatch due EvalSchedule entries. Runs every 60 s via celery-beat."""
    from app.api.eval_schedules import dispatch_due_schedules

    async def _run():
        engine = _make_engine()
        try:
            return await dispatch_due_schedules(engine)
        finally:
            await engine.dispose()

    try:
        n = asyncio.run(_run())
        if n:
            log.info("eval scheduler dispatched %d run(s)", n)
        return n
    except Exception as exc:
        log.warning("eval scheduler error: %s", exc)
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(name="antcrew.run_scheduler", bind=True, max_retries=3)
def run_run_scheduler(self):
    """Dispatch due RunSchedule entries. Runs every 60 s via celery-beat."""
    from app.api.run_schedules import dispatch_due_run_schedules

    async def _run():
        engine = _make_engine()
        try:
            return await dispatch_due_run_schedules(engine)
        finally:
            await engine.dispose()

    try:
        n = asyncio.run(_run())
        if n:
            log.info("run scheduler dispatched %d engine run(s)", n)
        return n
    except Exception as exc:
        log.warning("run scheduler error: %s", exc)
        raise self.retry(exc=exc, countdown=10)
