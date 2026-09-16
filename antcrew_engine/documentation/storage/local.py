"""Local filesystem storage backend."""
from __future__ import annotations

import json
from pathlib import Path

from .base import BaseStorage


class LocalFileStorage(BaseStorage):
    def __init__(self, root: str = "./documentation") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta_dir = self.root / ".antcrew_meta"
        self._meta_dir.mkdir(parents=True, exist_ok=True)

    def _safe(self, doc_id: str) -> str:
        return doc_id.replace("/", "__").replace("\\", "__")

    def save(self, doc_id: str, content: bytes, metadata: dict) -> None:
        safe = self._safe(doc_id)
        (self.root / safe).write_bytes(content)
        (self._meta_dir / f"{safe}.json").write_text(
            json.dumps(metadata, default=str), encoding="utf-8"
        )

    def load(self, doc_id: str) -> bytes:
        return (self.root / self._safe(doc_id)).read_bytes()

    def list_documents(self, prefix: str | None = None) -> list[str]:
        result: list[str] = []
        for p in self.root.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            doc_id = p.name.replace("__", "/")
            if prefix is None or doc_id.startswith(prefix):
                result.append(doc_id)
        return sorted(result)

    def delete(self, doc_id: str) -> None:
        safe = self._safe(doc_id)
        for path in (self.root / safe, self._meta_dir / f"{safe}.json"):
            if path.exists():
                path.unlink()

    def load_metadata(self, doc_id: str) -> dict:
        meta_path = self._meta_dir / f"{self._safe(doc_id)}.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding="utf-8"))
        return {}
