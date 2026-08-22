"""LLMCache — in-memory and SQLite-backed prompt-level cache for LLM responses.

Caches the full text response keyed by SHA-256 of (model_name, messages).
Streaming calls are never cached.

Usage:
    from antcrew_engine.models.cache import LLMCache, FileLLMCache
    from antcrew_engine.models.anthropic_model import AnthropicModel

    # In-memory (resets on restart)
    llm = AnthropicModel("claude-haiku-4-5-20251001").with_cache()

    # Persistent (survives restarts) — pass a path string or Path
    llm = AnthropicModel("claude-haiku-4-5-20251001").with_cache("~/.antcrew/cache.db")

    team = DevTeam(model=llm)
    team.run("Build login")   # fills the cache
    team.run("Build login")   # instant — 0 API calls

    print(llm.cache.stats())
    # {"hits": 6, "misses": 6, "hit_rate": 0.5, "size": 6, "max_size": 512}
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional


class LLMCache:
    """Thread-safe (GIL) in-memory cache keyed by SHA-256 of messages + model.

    When the cache is full the oldest entry is evicted (insertion-order FIFO
    via Python's dict ordering guarantee, Python 3.7+).
    """

    def __init__(self, max_size: int = 1024) -> None:
        self._store: dict[str, str] = {}
        self._max_size = max_size
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(
        self,
        messages: list,
        model_name: str,
        validate=None,
        agent_name: str = "",
    ) -> Optional[str]:
        """Return cached response if present and valid, else None.

        If *validate* is provided it is called with the cached string; a False
        return evicts the entry and records a miss so the caller re-fetches.
        """
        key = self._make_key(messages, model_name, agent_name)
        if key in self._store:
            value = self._store[key]
            if validate is not None and not validate(value):
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value
        self.misses += 1
        return None

    def set(self, messages: list, model_name: str, response: str, agent_name: str = "") -> None:
        """Store a response. Evicts the oldest entry when max_size is reached."""
        if len(self._store) >= self._max_size:
            oldest = next(iter(self._store))
            del self._store[oldest]
        self._store[self._make_key(messages, model_name, agent_name)] = response

    def clear(self) -> None:
        """Remove all cached entries. Does not reset hit/miss counters."""
        self._store.clear()

    def clear_agent(self, agent_name: str) -> int:
        """Remove all entries for a specific agent. Returns the number removed."""
        # In-memory cache has no agent_name metadata; subclasses override this.
        return 0

    def reset_stats(self) -> None:
        """Reset hit/miss counters to zero."""
        self.hits = 0
        self.misses = 0

    @property
    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict:
        """Return hit/miss/hit_rate/size/max_size summary."""
        total = self.hits + self.misses
        return {
            "hits":      self.hits,
            "misses":    self.misses,
            "hit_rate":  round(self.hits / total, 3) if total else 0.0,
            "size":      self.size,
            "max_size":  self._max_size,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(messages: list, model_name: str, agent_name: str = "") -> str:
        payload = json.dumps(
            [{"role": m.role, "content": m.content} for m in messages],
            sort_keys=True,
        )
        return hashlib.sha256(f"{agent_name}:{model_name}:{payload}".encode()).hexdigest()


class FileLLMCache(LLMCache):
    """LLMCache backed by SQLite — entries survive process restarts.

    On startup the most recent ``max_size`` entries are loaded from disk
    into memory so hot responses are still served from RAM.  Every new
    ``set()`` call is immediately flushed to the database.

    The SQLite file is created (including parent directories) automatically.

    Usage::

        llm = AnthropicModel().with_cache("~/.antcrew/cache.db")
        # equivalently:
        llm.cache = FileLLMCache("~/.antcrew/cache.db", max_size=2048)
    """

    def __init__(self, path: str | Path, *, max_size: int = 4096) -> None:
        super().__init__(max_size=max_size)
        self._db_path = Path(path).expanduser().resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._init_db()
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def get(self, messages: list, model_name: str, validate=None, agent_name: str = "") -> Optional[str]:
        """Like LLMCache.get() but also removes the SQLite row when invalid."""
        key = self._make_key(messages, model_name, agent_name)
        value = super().get(messages, model_name, validate=validate, agent_name=agent_name)
        if value is None and key not in self._store:
            # Parent evicted the in-memory entry — purge from DB too
            self._conn.execute("DELETE FROM llm_cache WHERE key = ?", (key,))
            self._conn.commit()
        return value

    def set(self, messages: list, model_name: str, response: str, agent_name: str = "") -> None:
        """Store in memory and persist to SQLite atomically."""
        super().set(messages, model_name, response, agent_name=agent_name)
        key = self._make_key(messages, model_name, agent_name)
        self._conn.execute(
            "INSERT OR REPLACE INTO llm_cache (key, agent_name, response, created_at) VALUES (?, ?, ?, ?)",
            (key, agent_name, response, time.time()),
        )
        self._conn.commit()

    def clear(self) -> None:
        """Clear both the in-memory store and the SQLite table."""
        super().clear()
        self._conn.execute("DELETE FROM llm_cache")
        self._conn.commit()

    def clear_agent(self, agent_name: str) -> int:
        """Delete all cached entries for a specific agent from disk and memory.

        Returns the number of entries deleted.
        """
        cur = self._conn.execute(
            "DELETE FROM llm_cache WHERE agent_name = ? RETURNING key", (agent_name,)
        )
        deleted_keys = {row[0] for row in cur.fetchall()}
        self._conn.commit()
        for key in deleted_keys:
            self._store.pop(key, None)
        return len(deleted_keys)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the SQLite connection explicitly."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                key        TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL DEFAULT '',
                response   TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        # Migrate older databases that lack the agent_name column.
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(llm_cache)")}
        if "agent_name" not in cols:
            self._conn.execute("ALTER TABLE llm_cache ADD COLUMN agent_name TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def _load_from_disk(self) -> None:
        """Warm the in-memory store from the most recent DB entries."""
        rows = self._conn.execute(
            "SELECT key, response FROM llm_cache ORDER BY created_at DESC LIMIT ?",
            (self._max_size,),
        ).fetchall()
        # Insert oldest-first so FIFO eviction stays correct.
        for key, response in reversed(rows):
            self._store[key] = response
