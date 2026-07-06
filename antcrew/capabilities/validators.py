"""Built-in Validators for common conditions.

These cover the deterministic, artifact-existence-based checks.
Conditions that require LLM reasoning (e.g. "architecture is consistent")
should be implemented as dedicated Validator subclasses.

All validators are pure: they read the store, never write.
"""
from __future__ import annotations

from antcrew.engine import ArtifactId, ConditionId, ValidatorResult
from antcrew.engine.store import ArtifactStore


class ArtifactExistsValidator:
    """Satisfied when a specific artifact id is present in the store.

    Covers: requirements_exists, architecture_exists, task_graph_exists, etc.
    """

    global_scope = False

    def __init__(self, artifact_id: ArtifactId, condition_id: ConditionId) -> None:
        self._artifact_id      = artifact_id
        self.condition_id      = condition_id
        self.relevant_artifacts = frozenset([artifact_id])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        exists = store.has(self._artifact_id)
        return ValidatorResult(
            condition_id  = self.condition_id,
            satisfied     = exists,
            observations  = {f"{self._artifact_id}_exists": exists},
        )


class ArtifactNotEmptyValidator:
    """Satisfied when an artifact exists AND has non-empty string content."""

    global_scope = False

    def __init__(self, artifact_id: ArtifactId, condition_id: ConditionId) -> None:
        self._artifact_id       = artifact_id
        self.condition_id       = condition_id
        self.relevant_artifacts = frozenset([artifact_id])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        artifact = store.read(self._artifact_id)
        satisfied = bool(artifact and artifact.content)
        return ValidatorResult(
            condition_id  = self.condition_id,
            satisfied     = satisfied,
            observations  = {f"{self._artifact_id}_non_empty": satisfied},
        )


# ---------------------------------------------------------------------------
# Convenience factory: one validator per (artifact_id, condition_id) pair
# ---------------------------------------------------------------------------

def artifact_validators(*pairs: tuple[str, str]) -> list[ArtifactExistsValidator]:
    """Build a list of ArtifactExistsValidators from (artifact_id, condition_id) pairs.

    Usage:
        validators = artifact_validators(
            ("requirements",  "requirements_exists"),
            ("architecture",  "architecture_exists"),
            ("task_graph",    "task_graph_exists"),
        )
    """
    return [
        ArtifactExistsValidator(ArtifactId(aid), ConditionId(cid))
        for aid, cid in pairs
    ]
