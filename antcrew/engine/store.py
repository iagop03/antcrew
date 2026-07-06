"""ArtifactStore: the project's source of truth.

ArtifactStore is a Protocol — the engine never depends on a concrete
implementation.  Today: MemoryStore.  Tomorrow: FilesystemStore, GitStore,
S3Store, RemoteStore — all interchangeable without touching any other module.

Capabilities read and write through the store.
Validators read through the store.
The Operator never touches the store directly.
"""
from __future__ import annotations

import dataclasses
from typing import Protocol, runtime_checkable

from .artifact import Artifact, ArtifactId, ArtifactDelta, ArtifactKind


@runtime_checkable
class ArtifactStore(Protocol):
    def read(self, id: ArtifactId) -> Artifact | None: ...
    def write(self, artifact: Artifact) -> None: ...
    def delete(self, id: ArtifactId) -> None: ...
    def list(self, kind: ArtifactKind | None = None) -> list[Artifact]: ...
    def has(self, id: ArtifactId) -> bool: ...
    def apply(self, delta: ArtifactDelta) -> None: ...


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

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"MemoryStore({len(self._data)} artifacts)"
