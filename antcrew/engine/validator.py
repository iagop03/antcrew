"""Validator: pure observation of project state.

Validators inspect the ArtifactStore and derive whether a Condition
is satisfied.  They are the only components that translate raw artifacts
into the ProjectState the Operator reasons over.

Invariants (never break these):
  - A Validator NEVER modifies the ArtifactStore.
  - A Validator NEVER modifies any Artifact.
  - A Validator NEVER calls another Validator.

Incremental validation:
  - global_scope=False  → only re-run when relevant_artifacts were touched
                          by the last CapabilityResult.
  - global_scope=True   → always re-run (expensive; use sparingly).

ValidatorResult carries observations and metrics in addition to the
boolean outcome — the Operator logs these and may use them for decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .artifact import ArtifactId
from .goal import ConditionId
from .store import ArtifactStore


@dataclass(frozen=True)
class ValidatorResult:
    condition_id:  ConditionId
    satisfied:     bool
    observations:  dict[str, Any]   = field(default_factory=dict)
    metrics:       dict[str, float] = field(default_factory=dict)


@runtime_checkable
class Validator(Protocol):
    condition_id:       ConditionId
    global_scope:       bool                  # True → runs on every cycle
    relevant_artifacts: frozenset[ArtifactId] # triggers re-run when touched

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        """Observe the store and return a result.  Must not modify anything."""
        ...
