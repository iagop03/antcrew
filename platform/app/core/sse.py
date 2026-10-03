"""SSE fan-out broadcaster — one DB poll per run_id, shared across N concurrent clients.

Multi-worker deployment note:
  Each worker independently polls the DB for run_ids it serves. Because events are written
  to a shared DB, any worker can serve SSE for any run regardless of which worker executed
  the pipeline. Sticky sessions are NOT required for correctness; they only reduce redundant
  DB polls when multiple workers happen to serve the same run concurrently.

Optional Redis relay (REDIS_PUBSUB_URL env var):
  When set, workers elect a leader per run_id via Redis SETNX.  The leader polls the DB and
  publishes events to Redis channel antcrew:sse:{run_id}.  Follower workers subscribe to that
  channel and fan out events to their local SSE queues, avoiding redundant DB polls.

  Leader key: antcrew:sse:leader:{run_id}  TTL 30 s (refreshed each poll cycle).

Requires: redis[asyncio] when REDIS_PUBSUB_URL is set.
"""
from __future__ import annotations

import asyncio
import json
import os


class _SSEBroadcaster:
    """One DB poll per run_id per worker, fanned out to N concurrent SSE clients.

    When Redis is configured, workers elect a leader per run_id:
    - Leader: polls DB and publishes to Redis.
    - Follower: subscribes to Redis and fans out to local queues.

    Without Redis: every worker polls the DB independently (original behaviour).
    """

    _LEADER_TTL = 30          # seconds before leader key expires if not refreshed
    _LEADER_REFRESH = 8.0     # leader refreshes its key every N poll cycles (× 1 s poll)

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        self._pollers: dict[str, asyncio.Task] = {}
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._cursors: dict[str, int] = {}

    async def subscribe(
        self, run_id: str, since_id: int, db_engine, Session
    ) -> asyncio.Queue:
        """Return a queue that receives dicts {type, ev?/status?}.

        If no poller exists for run_id, one is started from since_id.
        If one already exists, the subscriber gets a catch-up signal first.
        """
        async with self._lock:
            q: asyncio.Queue = asyncio.Queue(maxsize=500)
            subs = self._queues.setdefault(run_id, [])
            subs.append(q)
            if run_id not in self._pollers:
                self._cursors[run_id] = since_id
                task = asyncio.create_task(
                    self._start_poller(run_id, db_engine, Session),
                    name=f"sse-poll-{run_id[:8]}",
                )
                self._pollers[run_id] = task
            else:
                cursor = self._cursors.get(run_id, since_id)
                q.put_nowait({"type": "catchup_needed", "since": since_id, "cursor": cursor})
        return q

    async def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._queues.get(run_id, [])
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                self._queues.pop(run_id, None)
                self._cursors.pop(run_id, None)
                task = self._pollers.pop(run_id, None)
                if task and not task.done():
                    task.cancel()

    # ------------------------------------------------------------------
    # Poller entry point — decides leader vs follower
    # ------------------------------------------------------------------

    async def _start_poller(self, run_id: str, db_engine, Session) -> None:
        url = _redis_url()
        if not url:
            # No Redis — every worker polls the DB directly (original behaviour).
            await self._poll(run_id, db_engine, Session)
            return

        # Try to become leader with SETNX.
        is_leader = await _redis_try_leader(run_id, self._LEADER_TTL)
        if is_leader:
            await self._poll_as_leader(run_id, db_engine, Session, url)
        else:
            await self._listen_as_follower(run_id, db_engine, Session, url)

    # ------------------------------------------------------------------
    # Leader: polls DB, publishes to Redis, refreshes leader key
    # ------------------------------------------------------------------

    async def _poll_as_leader(self, run_id: str, db_engine, Session, redis_url: str) -> None:
        from sqlmodel import select as _sel

        from app.models.run import Event as _DBEvent
        from app.models.run import Run
        _TERMINAL = {"success", "error", "cancelled", "interrupted"}
        _poll_count = 0
        while True:
            try:
                await asyncio.sleep(1.0)
                async with self._lock:
                    if run_id not in self._queues:
                        break
                    since = self._cursors.get(run_id, 0)

                async with Session(db_engine, expire_on_commit=False) as sess:
                    rows = (await sess.exec(
                        _sel(_DBEvent)
                        .where(_DBEvent.run_id == run_id, _DBEvent.id > since)
                        .order_by(_DBEvent.id)
                        .limit(200)
                    )).all()
                    fresh_run = (await sess.exec(
                        _sel(Run).where(Run.run_id == run_id)
                    )).first()

                if rows:
                    async with self._lock:
                        self._cursors[run_id] = rows[-1].id
                        subs = list(self._queues.get(run_id, []))
                    payload = [
                        {"ev_id": ev.id, "ev_type": ev.event_type, "ev_data": ev.payload}
                        for ev in rows
                    ]
                    for ev in rows:
                        msg = {"type": "event", "ev": ev}
                        for q in subs:
                            try:
                                q.put_nowait(msg)
                            except asyncio.QueueFull:
                                pass
                    await _redis_publish(run_id, payload, redis_url)

                _poll_count += 1
                if _poll_count % self._LEADER_REFRESH == 0:
                    # Refresh leader key to prevent expiry during long runs.
                    await _redis_refresh_leader(run_id, self._LEADER_TTL, redis_url)

                if fresh_run and fresh_run.status in _TERMINAL:
                    async with self._lock:
                        subs = list(self._queues.get(run_id, []))
                    for q in subs:
                        try:
                            q.put_nowait({"type": "end", "status": fresh_run.status})
                        except asyncio.QueueFull:
                            pass
                    end_payload = {"type": "end", "status": fresh_run.status}
                    await _redis_publish(run_id, end_payload, redis_url)
                    await _redis_release_leader(run_id, redis_url)
                    async with self._lock:
                        self._queues.pop(run_id, None)
                        self._cursors.pop(run_id, None)
                        self._pollers.pop(run_id, None)
                    break

            except asyncio.CancelledError:
                await _redis_release_leader(run_id, redis_url)
                break
            except Exception:
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Follower: subscribes to Redis, fans out to local queues
    # ------------------------------------------------------------------

    async def _listen_as_follower(self, run_id: str, db_engine, Session, redis_url: str) -> None:
        """Subscribe to Redis channel and fan out messages to local SSE queues."""
        _TERMINAL = {"success", "error", "cancelled", "interrupted"}
        try:
            import redis.asyncio as aioredis  # type: ignore[import]
        except ImportError:
            # redis not installed — fall back to DB polling
            await self._poll(run_id, db_engine, Session)
            return

        channel = f"antcrew:sse:{run_id}"
        try:
            client = aioredis.from_url(redis_url)
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
            try:
                while True:
                    async with self._lock:
                        if run_id not in self._queues:
                            break
                    msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0), timeout=5.0)
                    if msg is None:
                        continue
                    try:
                        data = json.loads(msg["data"])
                    except Exception:
                        continue

                    async with self._lock:
                        subs = list(self._queues.get(run_id, []))
                        cursor = self._cursors.get(run_id, 0)

                    if isinstance(data, dict) and data.get("type") == "end":
                        for q in subs:
                            try:
                                q.put_nowait({"type": "end", "status": data.get("status", "success")})
                            except asyncio.QueueFull:
                                pass
                        async with self._lock:
                            self._queues.pop(run_id, None)
                            self._cursors.pop(run_id, None)
                            self._pollers.pop(run_id, None)
                        break
                    elif isinstance(data, list):
                        # List of {ev_id, ev_type, ev_data} published by leader
                        from app.models.run import Event as _DBEvent
                        new_cursor = cursor
                        for item in data:
                            ev_id = item.get("ev_id", 0)
                            if ev_id <= cursor:
                                continue  # already seen
                            # Reconstruct a lightweight event-like object for local queues
                            mock_ev = _MockEvent(
                                id=ev_id,
                                run_id=run_id,
                                event_type=item.get("ev_type", ""),
                                payload=item.get("ev_data", {}),
                            )
                            for q in subs:
                                try:
                                    q.put_nowait({"type": "event", "ev": mock_ev})
                                except asyncio.QueueFull:
                                    pass
                            new_cursor = max(new_cursor, ev_id)
                        if new_cursor > cursor:
                            async with self._lock:
                                self._cursors[run_id] = new_cursor
            finally:
                await pubsub.unsubscribe(channel)
                await client.aclose()
        except asyncio.CancelledError:
            pass
        except Exception:
            # Fallback to DB polling if Redis fails
            await self._poll(run_id, db_engine, Session)

    # ------------------------------------------------------------------
    # Original DB poll (no Redis / fallback)
    # ------------------------------------------------------------------

    async def _poll(self, run_id: str, db_engine, Session) -> None:
        from sqlmodel import select as _sel

        from app.models.run import Event as _DBEvent
        from app.models.run import Run
        _TERMINAL = {"success", "error", "cancelled", "interrupted"}
        while True:
            try:
                await asyncio.sleep(1.0)
                async with self._lock:
                    if run_id not in self._queues:
                        break
                    since = self._cursors.get(run_id, 0)

                async with Session(db_engine, expire_on_commit=False) as sess:
                    rows = (await sess.exec(
                        _sel(_DBEvent)
                        .where(_DBEvent.run_id == run_id, _DBEvent.id > since)
                        .order_by(_DBEvent.id)
                        .limit(200)
                    )).all()
                    fresh_run = (await sess.exec(
                        _sel(Run).where(Run.run_id == run_id)
                    )).first()

                if rows:
                    async with self._lock:
                        self._cursors[run_id] = rows[-1].id
                        subs = list(self._queues.get(run_id, []))
                    for ev in rows:
                        msg = {"type": "event", "ev": ev}
                        for q in subs:
                            try:
                                q.put_nowait(msg)
                            except asyncio.QueueFull:
                                pass
                    await _redis_publish(run_id, [
                        {"ev_id": ev.id, "ev_type": ev.event_type, "ev_data": ev.payload}
                        for ev in rows
                    ])

                if fresh_run and fresh_run.status in _TERMINAL:
                    async with self._lock:
                        subs = list(self._queues.get(run_id, []))
                    for q in subs:
                        try:
                            q.put_nowait({"type": "end", "status": fresh_run.status})
                        except asyncio.QueueFull:
                            pass
                    await _redis_publish(run_id, {"type": "end", "status": fresh_run.status})
                    async with self._lock:
                        self._queues.pop(run_id, None)
                        self._cursors.pop(run_id, None)
                        self._pollers.pop(run_id, None)
                    break

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)


class _MockEvent:
    """Lightweight stand-in for a DB Event row when replaying from Redis messages."""
    __slots__ = ("id", "run_id", "event_type", "payload")

    def __init__(self, *, id: int, run_id: str, event_type: str, payload: dict) -> None:
        self.id = id
        self.run_id = run_id
        self.event_type = event_type
        self.payload = payload


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

_REDIS_URL: str | None = None
_REDIS_URL_LOADED = False


def _redis_url() -> str | None:
    global _REDIS_URL, _REDIS_URL_LOADED
    if not _REDIS_URL_LOADED:
        _REDIS_URL = os.environ.get("REDIS_PUBSUB_URL", "").strip() or None
        _REDIS_URL_LOADED = True
    return _REDIS_URL


async def _redis_publish(run_id: str, payload: object, url: str | None = None) -> None:
    """Best-effort publish to Redis. Silently ignored if Redis is not configured."""
    if url is None:
        url = _redis_url()
    if not url:
        return
    try:
        import redis.asyncio as aioredis  # type: ignore[import]
        client = aioredis.from_url(url)
        await client.publish(f"antcrew:sse:{run_id}", json.dumps(payload))
        await client.aclose()
    except Exception:
        pass


async def _redis_try_leader(run_id: str, ttl: int) -> bool:
    """SETNX leader key. Returns True if this worker is now leader."""
    url = _redis_url()
    if not url:
        return True  # No Redis → every worker is its own leader (DB poll mode)
    try:
        import redis.asyncio as aioredis  # type: ignore[import]
        client = aioredis.from_url(url)
        key = f"antcrew:sse:leader:{run_id}"
        result = await client.set(key, "1", nx=True, ex=ttl)
        await client.aclose()
        return bool(result)
    except Exception:
        return True  # Redis unavailable → fall back to DB poll


async def _redis_refresh_leader(run_id: str, ttl: int, url: str) -> None:
    try:
        import redis.asyncio as aioredis  # type: ignore[import]
        client = aioredis.from_url(url)
        await client.expire(f"antcrew:sse:leader:{run_id}", ttl)
        await client.aclose()
    except Exception:
        pass


async def _redis_release_leader(run_id: str, url: str) -> None:
    try:
        import redis.asyncio as aioredis  # type: ignore[import]
        client = aioredis.from_url(url)
        await client.delete(f"antcrew:sse:leader:{run_id}")
        await client.aclose()
    except Exception:
        pass


_sse_broadcaster = _SSEBroadcaster()
