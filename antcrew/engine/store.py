"""ArtifactStore: the project's source of truth.

ArtifactStore is a Protocol — the engine never depends on a concrete
implementation.  Implementations: MemoryStore (fast, ephemeral),
FilesystemStore (persists to disk across runs).

Capabilities read and write through the store.
Validators read through the store.
The Operator never touches the store directly.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .artifact import Artifact, ArtifactId, ArtifactDelta, ArtifactKind


@runtime_checkable
class ArtifactStore(Protocol):
    def read(self, id: ArtifactId) -> Artifact | None: ...
    def write(self, artifact: Artifact) -> None: ...
    def delete(self, id: ArtifactId) -> None: ...
    def list(self, kind: ArtifactKind | None = None) -> list[Artifact]: ...
    def has(self, id: ArtifactId) -> bool: ...
    def apply(self, delta: ArtifactDelta) -> None: ...
    def filesystem_path(self) -> "Path | None": ...


class MemoryStore:
    """In-memory ArtifactStore — suitable for fast iteration and tests."""

    def __init__(self) -> None:
        self._data: dict[ArtifactId, Artifact] = {}

    def read(self, id: ArtifactId) -> Artifact | None:
        return self._data.get(id)

    def write(self, artifact: Artifact) -> None:
        self._data[artifact.id] = artifact

    def delete(self, id: ArtifactId) -> None:
        self._data.pop(id, None)

    def list(self, kind: ArtifactKind | None = None) -> list[Artifact]:
        if kind is None:
            return list(self._data.values())
        return [a for a in self._data.values() if a.kind == kind]

    def has(self, id: ArtifactId) -> bool:
        return id in self._data

    def apply(self, delta: ArtifactDelta) -> None:
        for artifact in delta.created:
            self._data[artifact.id] = artifact
        for artifact in delta.modified:
            self._data[artifact.id] = artifact
        for aid in delta.deleted:
            self._data.pop(aid, None)
        for old_id, new_id in delta.renamed:
            if old_id in self._data:
                old = self._data.pop(old_id)
                self._data[new_id] = dataclasses.replace(old, id=new_id)

    def filesystem_path(self) -> Path | None:
        return None

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"MemoryStore({len(self._data)} artifacts)"


# ---------------------------------------------------------------------------
# FilesystemStore
# ---------------------------------------------------------------------------

# Kinds whose content should be stored as raw text (not JSON-serialised).
_TEXT_KINDS = frozenset([
    ArtifactKind.SOURCE,
    ArtifactKind.TEST,
    ArtifactKind.DOCUMENTATION,
    ArtifactKind.CONFIG,
    ArtifactKind.GENERIC,
])

_MANIFEST_REL = Path(".antcrew") / "manifest.json"


class FilesystemStore:
    """Disk-backed ArtifactStore.

    Content is written to natural file paths inside *root*.
    Metadata (kind, file mapping) lives in *root*/.antcrew/manifest.json so the
    store can reconstruct full Artifact objects on read without scanning the tree.

    Thread-safety: single-process, single-thread (no locking).
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._manifest_path = self._root / _MANIFEST_REL
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._manifest_cache: dict[str, dict[str, Any]] | None = None  # write-through cache
        self._content_cache:  dict[str, Artifact] = {}                 # read-through content cache

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        if self._manifest_cache is not None:
            return self._manifest_cache
        if not self._manifest_path.exists():
            self._manifest_cache = {}
        else:
            try:
                self._manifest_cache = json.loads(
                    self._manifest_path.read_text(encoding="utf-8")
                )
            except Exception:
                self._manifest_cache = {}
        return self._manifest_cache

    def _save_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:
        self._manifest_cache = manifest
        self._manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )

    def _file_path(self, artifact_id: str, entry: dict[str, Any]) -> Path:
        rel = entry.get("file_path") or artifact_id
        return self._root / rel

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def read(self, id: ArtifactId) -> Artifact | None:
        key = str(id)
        if key in self._content_cache:
            return self._content_cache[key]
        manifest = self._load_manifest()
        entry = manifest.get(key)
        if entry is None:
            return None
        path = self._file_path(key, entry)
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        kind = ArtifactKind(entry["kind"])
        content: Any = raw
        if kind not in _TEXT_KINDS:
            try:
                content = json.loads(raw)
            except Exception:
                pass
        artifact = Artifact(
            id=id,
            kind=kind,
            content=content,
            location=path,
            metadata=entry.get("metadata", {}),
        )
        self._content_cache[key] = artifact
        return artifact

    def write(self, artifact: Artifact) -> None:
        manifest = self._load_manifest()
        rel = artifact.metadata.get("file_path") or str(artifact.id)
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            artifact.content
            if isinstance(artifact.content, str)
            else json.dumps(artifact.content, indent=2, default=str)
        )
        path.write_text(body, encoding="utf-8")
        manifest[str(artifact.id)] = {
            "kind":      artifact.kind.value,
            "file_path": rel,
            "metadata":  dict(artifact.metadata),
        }
        self._save_manifest(manifest)
        self._content_cache[str(artifact.id)] = dataclasses.replace(artifact, location=path)

    def delete(self, id: ArtifactId) -> None:
        key = str(id)
        self._content_cache.pop(key, None)
        manifest = self._load_manifest()
        entry = manifest.pop(key, None)
        if entry:
            path = self._file_path(key, entry)
            if path.exists():
                path.unlink(missing_ok=True)
            self._save_manifest(manifest)

    def list(self, kind: ArtifactKind | None = None) -> list[Artifact]:
        manifest = self._load_manifest()
        results = []
        for aid, entry in manifest.items():
            if kind is not None and entry.get("kind") != kind.value:
                continue
            artifact = self.read(ArtifactId(aid))
            if artifact is not None:
                results.append(artifact)
        return results

    def has(self, id: ArtifactId) -> bool:
        return str(id) in self._load_manifest()

    def apply(self, delta: ArtifactDelta) -> None:
        for artifact in delta.created:
            self.write(artifact)
        for artifact in delta.modified:
            self.write(artifact)
        for aid in delta.deleted:
            self.delete(aid)
        for old_id, new_id in delta.renamed:
            old = self.read(old_id)
            if old is not None:
                self.write(dataclasses.replace(old, id=new_id, location=None))
            self.delete(old_id)

    def filesystem_path(self) -> Path:
        return self._root

    @property
    def root(self) -> Path:
        """Alias kept for backwards compatibility. Prefer filesystem_path()."""
        return self._root

    def __len__(self) -> int:
        return len(self._load_manifest())

    def __repr__(self) -> str:
        return f"FilesystemStore({self._root})"
