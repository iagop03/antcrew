"""Key-value memory store for agent state between runs.

Separate from BaseMemory (which is semantic/vector-based).
KV memory is simple and deterministic: agents read/write named values
that persist across runs via the AntCrew Platform.

Usage in a BaseAgent subclass:

    def run(self, state):
        prev = self._mem_get("last_summary")          # None on first run
        result = self.system("Summarize...", ...)
        self._mem_set("last_summary", result)          # saved for next run
        return {"summary": result, ...}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseKVMemory(ABC):
    """Abstract key-value memory backend."""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Return stored value for *key*, or None if absent."""

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* (overwrites)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove *key* (no-op if absent)."""

    @abstractmethod
    def all(self) -> dict[str, Any]:
        """Return a copy of all stored key-value pairs."""


class InMemoryKVMemory(BaseKVMemory):
    """In-process KV store backed by a plain dict.

    Used by the platform runner: pre-populated from the DB before a run,
    then its snapshot is persisted to the DB after the run completes.
    """

    def __init__(self, initial: Optional[dict] = None) -> None:
        self._store: dict[str, Any] = dict(initial or {})
        self._dirty: bool = False

    def get(self, key: str) -> Optional[Any]:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value
        self._dirty = True

    def delete(self, key: str) -> None:
        if key in self._store:
            del self._store[key]
            self._dirty = True

    def all(self) -> dict[str, Any]:
        return dict(self._store)

    @property
    def dirty(self) -> bool:
        """True if any write has happened since creation."""
        return self._dirty
