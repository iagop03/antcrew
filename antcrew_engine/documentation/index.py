"""DocumentationIndex: semantic search over documentation chunks.

Uses ChromaDB when available; falls back to an in-memory keyword index
so the module works without heavy ML dependencies installed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SearchResult:
    id: str
    content: str
    metadata: dict[str, Any]
    similarity_score: float


class DocumentationIndex:
    """Semantic search engine backed by ChromaDB (or a simple keyword fallback)."""

    def __init__(
        self,
        cache_dir: str = "./.antcrew_docs",
        embedding_model: str = "text-embedding-3-small",
        chunk_size: int = 1000,
    ) -> None:
        self.cache_dir = cache_dir
        self.embedding_model = embedding_model
        self.chunk_size = chunk_size
        self._backend: _Backend = self._init_backend()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_document(
        self,
        doc_id: str,
        content: str,
        metadata: dict,
        chunks: list[str] | None = None,
    ) -> None:
        if chunks is None:
            chunks = self._chunk(content)
        self._backend.add(doc_id, content, chunks, metadata)

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_metadata: dict | None = None,
    ) -> list[dict]:
        return self._backend.search(query, top_k, filter_metadata)

    def delete_document(self, doc_id: str) -> None:
        self._backend.delete(doc_id)

    def rebuild_index(self) -> None:
        self._backend.rebuild()

    def get_index_size(self) -> dict:
        return self._backend.size()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _init_backend(self) -> "_Backend":
        try:
            return _ChromaBackend(self.cache_dir, self.embedding_model)
        except Exception:
            return _MemoryBackend()

    def _chunk(self, text: str) -> list[str]:
        """Split text into chunks of at most chunk_size characters, on word boundaries."""
        words = text.split()
        chunks: list[str] = []
        current: list[str] = []
        char_count = 0
        for word in words:
            current.append(word)
            char_count += len(word) + 1
            if char_count >= self.chunk_size:
                chunks.append(" ".join(current))
                current = []
                char_count = 0
        if current:
            chunks.append(" ".join(current))
        return chunks or [text]


# ---------------------------------------------------------------------------
# Backend: ChromaDB
# ---------------------------------------------------------------------------

class _Backend:
    def add(self, doc_id: str, content: str, chunks: list[str], metadata: dict) -> None: ...
    def search(self, query: str, top_k: int, filter_metadata: dict | None) -> list[dict]: ...
    def delete(self, doc_id: str) -> None: ...
    def rebuild(self) -> None: ...
    def size(self) -> dict: ...


class _ChromaBackend(_Backend):
    def __init__(self, cache_dir: str, embedding_model: str) -> None:
        import chromadb

        path = Path(cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))

        ef = None
        if embedding_model.startswith("text-embedding-"):
            try:
                import os
                from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                api_key = os.environ.get("OPENAI_API_KEY", "")
                if api_key:
                    ef = OpenAIEmbeddingFunction(api_key=api_key, model_name=embedding_model)
            except Exception:
                pass

        kwargs: dict[str, Any] = {"name": "antcrew_docs"}
        if ef is not None:
            kwargs["embedding_function"] = ef
        self._collection = self._client.get_or_create_collection(**kwargs)
        self._full_content: dict[str, str] = {}  # doc_id → full text (for retrieval)

    def add(self, doc_id: str, content: str, chunks: list[str], metadata: dict) -> None:
        self.delete(doc_id)  # remove stale chunks
        self._full_content[doc_id] = content
        ids = [f"{doc_id}__chunk_{i}" for i in range(len(chunks))]
        metas = [{**metadata, "doc_id": doc_id, "chunk_index": i} for i in range(len(chunks))]
        self._collection.add(ids=ids, documents=chunks, metadatas=metas)

    def search(self, query: str, top_k: int, filter_metadata: dict | None) -> list[dict]:
        count = self._collection.count()
        if count == 0:
            return []
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(top_k * 2, count),  # over-fetch to de-duplicate by doc
                where=filter_metadata or None,
            )
        except Exception:
            return []

        seen: set[str] = set()
        out: list[dict] = []
        for i, chunk_id in enumerate(results["ids"][0]):
            doc_id = chunk_id.rsplit("__chunk_", 1)[0]
            if doc_id in seen:
                continue
            seen.add(doc_id)
            distance = (results.get("distances") or [[]])[0][i] if results.get("distances") else 0.0
            out.append({
                "id": doc_id,
                "content": self._full_content.get(doc_id, results["documents"][0][i]),
                "metadata": results["metadatas"][0][i],
                "similarity_score": max(0.0, 1.0 - distance),
            })
            if len(out) >= top_k:
                break
        return out

    def delete(self, doc_id: str) -> None:
        self._full_content.pop(doc_id, None)
        try:
            existing = self._collection.get(where={"doc_id": doc_id})
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass

    def rebuild(self) -> None:
        self._full_content.clear()
        self._collection.delete(where={})

    def size(self) -> dict:
        return {"total_chunks": self._collection.count(), "backend": "chromadb"}


# ---------------------------------------------------------------------------
# Backend: In-memory keyword search (no external deps)
# ---------------------------------------------------------------------------

class _MemoryBackend(_Backend):
    def __init__(self) -> None:
        self._docs: list[dict] = []

    def add(self, doc_id: str, content: str, chunks: list[str], metadata: dict) -> None:
        self.delete(doc_id)
        self._docs.append({"id": doc_id, "content": content, "chunks": chunks, "metadata": metadata})

    def search(self, query: str, top_k: int, filter_metadata: dict | None) -> list[dict]:
        query_words = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[float, dict]] = []

        for doc in self._docs:
            if filter_metadata:
                if not all(doc["metadata"].get(k) == v for k, v in filter_metadata.items()):
                    continue
            doc_words = set(re.findall(r"\w+", doc["content"].lower()))
            if not query_words:
                score = 0.0
            else:
                score = len(query_words & doc_words) / len(query_words)
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": d["id"],
                "content": d["content"],
                "metadata": d["metadata"],
                "similarity_score": s,
            }
            for s, d in scored[:top_k]
        ]

    def delete(self, doc_id: str) -> None:
        self._docs = [d for d in self._docs if d["id"] != doc_id]

    def rebuild(self) -> None:
        self._docs.clear()

    def size(self) -> dict:
        return {"total_docs": len(self._docs), "backend": "memory"}
