"""Built-in Validators for common conditions.

These cover the deterministic, artifact-existence-based checks.
Conditions that require LLM reasoning (e.g. "architecture is consistent")
should be implemented as dedicated Validator subclasses.

All validators are pure: they read the store, never write.
"""
from __future__ import annotations

from antcrew_engine.engine import ArtifactId, ConditionId, ValidatorResult
from antcrew_engine.engine.store import ArtifactStore


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
# Task-graph validators
# ---------------------------------------------------------------------------

class AllTasksCompletedValidator:
    """Satisfied when the task_graph exists and every task has status='done'."""

    condition_id        = ConditionId("implementation_exists")
    global_scope        = False
    relevant_artifacts  = frozenset([ArtifactId("task_graph")])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        tg = store.read(ArtifactId("task_graph"))
        if tg is None:
            return ValidatorResult(self.condition_id, satisfied=False,
                                   observations={"task_graph_exists": False})
        tasks     = tg.content.get("tasks", []) if isinstance(tg.content, dict) else []
        done      = sum(1 for t in tasks if t.get("status") == "done")
        total     = len(tasks)
        satisfied = bool(total) and done == total
        return ValidatorResult(
            condition_id = self.condition_id,
            satisfied    = satisfied,
            observations = {"tasks_done": done, "tasks_total": total},
            metrics      = {"completion_ratio": done / total if total else 0.0},
        )


# ---------------------------------------------------------------------------
# Test validators
# ---------------------------------------------------------------------------

class TestsExistValidator:
    """Satisfied when at least one TEST artifact is present in the store."""

    condition_id        = ConditionId("tests_exist")
    global_scope        = True   # depends on count of artifacts, not one specific id
    relevant_artifacts  = frozenset()

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        from antcrew_engine.engine import ArtifactKind
        tests     = store.list(ArtifactKind.TEST)
        satisfied = bool(tests)
        return ValidatorResult(
            condition_id = self.condition_id,
            satisfied    = satisfied,
            observations = {"test_count": len(tests)},
        )


class TestsPassValidator:
    """Satisfied when test_report artifact exists and reports passed=True."""

    condition_id        = ConditionId("tests_pass")
    global_scope        = False
    relevant_artifacts  = frozenset([ArtifactId("test_report")])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        report = store.read(ArtifactId("test_report"))
        if report is None:
            return ValidatorResult(self.condition_id, satisfied=False,
                                   observations={"test_report_exists": False})
        content   = report.content if isinstance(report.content, dict) else {}
        passed    = bool(content.get("passed"))
        returncode = content.get("returncode", -1)
        return ValidatorResult(
            condition_id = self.condition_id,
            satisfied    = passed,
            observations = {"passed": passed, "returncode": returncode},
        )


# ---------------------------------------------------------------------------
# Code review validator
# ---------------------------------------------------------------------------

class CodeReviewedValidator:
    """Satisfied when review_report exists and verdict is 'approved'."""

    condition_id        = ConditionId("code_reviewed")
    global_scope        = False
    relevant_artifacts  = frozenset([ArtifactId("review_report")])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        report = store.read(ArtifactId("review_report"))
        if report is None:
            return ValidatorResult(self.condition_id, satisfied=False,
                                   observations={"review_report_exists": False})
        content  = report.content if isinstance(report.content, dict) else {}
        verdict  = content.get("verdict", "needs_changes")
        approved = verdict == "approved"
        findings = content.get("findings", [])
        criticals = sum(1 for f in findings if f.get("severity") in ("critical", "error"))
        return ValidatorResult(
            condition_id = self.condition_id,
            satisfied    = approved,
            observations = {
                "verdict":          verdict,
                "total_findings":   len(findings),
                "critical_findings": criticals,
            },
        )


# ---------------------------------------------------------------------------
# Dependency validator
# ---------------------------------------------------------------------------

class DependenciesInstalledValidator:
    """Satisfied when venv_config artifact exists and the venv is present on disk."""

    condition_id        = ConditionId("dependencies_installed")
    global_scope        = False
    relevant_artifacts  = frozenset([ArtifactId("venv_config")])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        from pathlib import Path as _Path
        config = store.read(ArtifactId("venv_config"))
        if config is None:
            return ValidatorResult(self.condition_id, satisfied=False,
                                   observations={"venv_config_exists": False})
        content   = config.content if isinstance(config.content, dict) else {}
        venv_path = content.get("venv_path")
        ok        = bool(venv_path) and _Path(venv_path).exists()
        return ValidatorResult(
            condition_id = self.condition_id,
            satisfied    = ok,
            observations = {
                "venv_path":   venv_path,
                "venv_exists": ok,
                "install_ok":  content.get("install_ok", False),
            },
        )


# ---------------------------------------------------------------------------
# Documentation validator
# ---------------------------------------------------------------------------

class DocumentationExistsValidator:
    """Satisfied when the 'documentation' artifact exists and is non-empty."""

    condition_id        = ConditionId("documentation_exists")
    global_scope        = False
    relevant_artifacts  = frozenset([ArtifactId("documentation")])

    def validate(self, store: ArtifactStore) -> ValidatorResult:
        doc = store.read(ArtifactId("documentation"))
        satisfied = bool(doc and doc.content)
        return ValidatorResult(
            condition_id = self.condition_id,
            satisfied    = satisfied,
            observations = {"documentation_exists": satisfied},
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
