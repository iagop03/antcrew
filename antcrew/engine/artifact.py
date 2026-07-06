"""Artifact: the unit of project state.

An Artifact is any piece of project output — a spec document, a source file,
a task graph, a test report.  It is identified by an ArtifactId (not by its
filesystem path), so storage is a detail of the ArtifactStore implementation.

ArtifactDelta describes what changed after a Capability executed.  The Store
applies the delta; the Operator and Validators never modify artifacts directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, NewType

ArtifactId = NewType("ArtifactId", str)


class ArtifactKind(str, Enum):
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    TASK_GRAPH   = "task_graph"
    SOURCE       = "source"
    TEST         = "test"
    DOCUMENTATION = "documentation"
    CONFIG       = "config"
    REPORT       = "report"
    GENERIC      = "generic"


@dataclass(frozen=True)
class Artifact:
    id: ArtifactId
    kind: ArtifactKind
    content: Any                          # str, dict, bytes — kind determines schema
    location: Path | None = None          # filesystem path, if persisted
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactDelta:
    """Describes what a Capability produced.  Applied to the Store atomically."""

    created:  tuple[Artifact, ...]                    = ()
    modified: tuple[Artifact, ...]                    = ()
    deleted:  tuple[ArtifactId, ...]                  = ()
    renamed:  tuple[tuple[ArtifactId, ArtifactId], ...] = ()  # (old_id, new_id)

    @property
    def touched(self) -> frozenset[ArtifactId]:
        """All artifact ids affected by this delta — used for incremental validation."""
        return frozenset(
            [a.id for a in self.created]
            + [a.id for a in self.modified]
            + list(self.deleted)
            + [new for _, new in self.renamed]
        )

    def is_empty(self) -> bool:
        return not (self.created or self.modified or self.deleted or self.renamed)


EMPTY_DELTA = ArtifactDelta()
