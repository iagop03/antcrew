"""Celery application factory.

Instantiated only when CELERY_BROKER_URL is set. All tasks live in app/tasks/.

Startup:
    celery -A app.celery_app worker --loglevel=info --concurrency=4

See docs/platform/configuration.md for CELERY_BROKER_URL and related settings.
"""
from __future__ import annotations

import os

from celery import Celery  # type: ignore[import]

broker_url = os.environ.get("CELERY_BROKER_URL", "")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", broker_url)

celery_app = Celery(
    "antcrew",
    broker=broker_url or None,
    backend=result_backend or None,
    include=["app.tasks.run_tasks", "app.tasks.scheduler_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,          # re-queue on worker crash
    worker_prefetch_multiplier=1,  # fair dispatch — one task per worker at a time
    beat_schedule={
        "eval-scheduler": {
            "task": "antcrew.eval_scheduler",
            "schedule": 60.0,  # seconds — matches the asyncio loop cadence
        },
        "run-scheduler": {
            "task": "antcrew.run_scheduler",
            "schedule": 60.0,
        },
    },
    beat_scheduler="celery.beat:PersistentScheduler",
)
