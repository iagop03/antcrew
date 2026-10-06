"""In-process LRU cache for completed (immutable) runs.

Completed runs never change, so we cache them indefinitely up to MAX_SIZE
entries. The cache lives in the process — no external dep required.

With Redis (REDIS_URL set), terminal runs are also stored there with a 1-hour
TTL so the cache survives pod restarts and is shared across workers.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Any, Optional

_TERMINAL = frozenset({"success", "error", "cancelled", "interrupted"})
_MAX_SIZE = int(os.getenv("RUN_CACHE_SIZE", "1000"))
_REDIS_TTL = int(os.getenv("RUN_CACHE_TTL_S", "3600"))
_REDIS_URL: Optional[str] = os.environ.get("REDIS_URL") or None

# In-memory LRU store: run_id → serialised run dict
_lru: OrderedDict[str, dict] = OrderedDict()
_redis_conn: Any = None


def _redis_key(run_id: str) -> str:
    return f"antcrew:run:{run_id}"


async def _get_redis() -> Optional[Any]:
    global _redis_conn
    if not _REDIS_URL:
        return None
    if _redis_conn is None:
        try:
            import redis.asyncio as aioredis
            _redis_conn = aioredis.from_url(_REDIS_URL, decode_responses=True)
        except Exception:
            return None
    return _redis_conn


def _put_lru(run_id: str, data: dict) -> None:
    _lru[run_id] = data
    _lru.move_to_end(run_id)
    while len(_lru) > _MAX_SIZE:
        _lru.popitem(last=False)


async def get_cached_run(run_id: str) -> Optional[dict]:
    """Return cached run data or None if not cached."""
    if run_id in _lru:
        _lru.move_to_end(run_id)
        return _lru[run_id]

    rc = await _get_redis()
    if rc:
        try:
            raw = await rc.get(_redis_key(run_id))
            if raw:
                data: dict = json.loads(raw)
                _put_lru(run_id, data)
                return data
        except Exception:
            pass

    return None


async def cache_run(run_id: str, run_data: dict) -> None:
    """Cache a terminal run's data. No-op for non-terminal statuses."""
    if run_data.get("status") not in _TERMINAL:
        return

    _put_lru(run_id, run_data)

    rc = await _get_redis()
    if rc:
        try:
            await rc.set(_redis_key(run_id), json.dumps(run_data, default=str), ex=_REDIS_TTL)
        except Exception:
            pass
