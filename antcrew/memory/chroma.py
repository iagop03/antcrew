"""ChromaDB-backed semantic memory store.

Install: pip install antcrew[memory]
"""
from __future__ import annotations

import uuid
from typing import Optional

from antcrew.memory.store import BaseMemory, MemoryResult


class ChromaMemory(BaseMemory):
    """Persistent semantic memory using ChromaDB with embedding support.

    Uses ChromaDB's default embedding model (all-MiniLM-L6-v2 via onnxruntime).
    Stores to disk at `path`; survives restarts.

    Usage:
        memory = ChromaMemory()                         # .antcrew_memory/ in cwd
        memory = ChromaMemory(path="/data/antcrew_db")  # custom path
        memory = ChromaMemory(collection="project_x")   # isolate by project
    """

    def __init__(
        self,
        path: str = ".antcrew_memory",
        collection: str = "antcrew",
    ) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError(
                "chromadb is required for ChromaMemory.\n"
                "Install it: pip install antcrew[memory]"
            ) from exc

        self._client = chromadb.PersistentClient(path=path)
        self._col = self._client.get_or_create_collection(collection)

    def add(self, text: str, metadata: dict) -> str:
        entry_id = uuid.uuid4().hex
        # ChromaDB requires all metadata values to be str/int/float/bool
        safe_meta = {
            k: str(v) if not isinstance(v, (str, int, float, bool)) else v
            for k, v in metadata.items()
        }
        self._col.add(documents=[text], metadatas=[safe_meta], ids=[entry_id])
        return entry_id

    def search(
        self,
        query: str,
        *,
        n: int = 5,
        filter: Optional[dict] = None,
    ) -> list[MemoryResult]:
        total = self._col.count()
        if total == 0:
            return []

        kwargs: dict = {
            "query_texts": [query],
            "n_results":   min(n, total),
        }
        if filter:
            kwargs["where"] = filter

        results = self._col.query(**kwargs)
        items: list[MemoryResult] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # ChromaDB returns cosine distance [0, 2]; convert to similarity [0, 1]
            score = max(0.0, 1.0 - dist / 2.0)
            items.append(MemoryResult(text=doc, metadata=meta, score=score))
        return items

    def count(self) -> int:
        return self._col.count()

    def clear(self) -> None:
        name = self._col.name
        self._client.delete_collection(name)
        self._col = self._client.get_or_create_collection(name)
