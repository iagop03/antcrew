"""ProjectState: an immutable snapshot of what the project is right now.

ProjectState is derived by Validators from the ArtifactStore.
It is never modified — each EngineLoop iteration produces a new snapshot.

The EngineLoop reasons over ProjectState.  It never reads the Store directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .goal import ConditionId, DesiredProjectState


@dataclass(frozen=True)
class ProjectState:
    satisfied:      frozenset[ConditionId]
    observations:   dict[str, Any]          # named observations from validators
    metrics:        dict[str, float]         # quantitative measurements
    timestamp:      datetime = field(default_factory=lambda: datetime.now(UTC))
    is_invalid:     bool = False
    invalid_reason: str | None = None

    def satisfies(self, desired: DesiredProjectState) -> bool:
        return desired.is_satisfied_by(self.satisfied)

    def gap(self, desired: DesiredProjectState) -> frozenset[ConditionId]:
        return frozenset(c.id for c in desired.conditions if c.id not in self.satisfied)

    def __repr__(self) -> str:
        n = len(self.satisfied)
        return f"ProjectState(satisfied={n}, invalid={self.is_invalid})"
