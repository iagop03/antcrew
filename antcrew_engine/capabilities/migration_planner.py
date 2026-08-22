from __future__ import annotations

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from ._utils import head as _head
from ._utils import parse_json
from .base import BaseExecutor

_SCHEMA_FILENAMES = frozenset(["models.py", "schema.py"])
_SCHEMA_EXTENSIONS = frozenset([".sql"])
_SCHEMA_CONTENT_MARKERS = ["CREATE TABLE", "SQLModel", "declarative_base"]

_SYSTEM = """\
You are a database migration expert. Given source code containing schema definitions \
and the project goal, produce a structured migration plan.

Output a JSON object with this structure:
{
  "summary": "One-sentence description of what changes",
  "steps": [
    {
      "order": 1,
      "description": "Human-readable step description",
      "sql_up": "SQL to apply this step (or null if not SQL)",
      "sql_down": "SQL to reverse this step (or null if not SQL / not reversible)",
      "is_destructive": false,
      "requires_downtime": false
    }
  ],
  "risk_level": "low|medium|high",
  "hitl_required": true,
  "notes": "Any important caveats or pre-conditions"
}

Rules:
- Every step MUST have a sql_down that reverses it, or explicitly null with an explanation in notes
- Mark requires_downtime=true for any ALTER TABLE ADD COLUMN NOT NULL without DEFAULT, \
any DROP TABLE/COLUMN, or table renames
- Mark is_destructive=true for any DROP TABLE, DROP COLUMN, or data-modifying steps
- Set hitl_required=true if risk_level is "medium" or "high"
- Output ONLY the JSON — no markdown fences, no prose\
"""

_SCHEMA_LINES_LIMIT = 200


class MigrationPlanner(BaseExecutor):
    """Plans reversible database schema migrations from source models and the project goal.

    Scans SOURCE artifacts for schema definitions (models.py, schema.py, *.sql,
    or files containing CREATE TABLE / SQLModel / declarative_base), then uses the
    LLM to produce a structured JSON migration plan.
    """

    descriptor = CapabilityDescriptor(
        name        = "migration_planner",
        description = "Plans reversible database schema migrations from source models and the project goal.",
        needs       = frozenset([ConditionId("implementation_exists")]),
        produces    = frozenset([ConditionId("migration_plan_ready")]),
        emits       = frozenset(["docs"]),
        cost        = 1.8,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        schema_files = _find_schema_files(sources)

        if not schema_files:
            return CapabilityResult(
                errors=["No schema files found. Run CodeGenerator first or provide schema files."]
            )

        schema_block = _build_schema_block(schema_files)
        user = (
            f"Goal: {goal.description}\n\n"
            f"Schema files:\n{schema_block}"
        )

        raw = self._call_json(_SYSTEM, user)
        plan = _safe_parse(raw)

        plan_artifact = Artifact(
            id       = ArtifactId("migration_plan"),
            kind     = ArtifactKind.DOCUMENTATION,
            content  = plan,
            metadata = {"file_path": ".antcrew/migration_plan.json"},
        )
        return CapabilityResult(delta=ArtifactDelta(created=(plan_artifact,)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_schema_file(artifact) -> bool:
    """Return True if this artifact looks like a schema/model definition."""
    fp = artifact.metadata.get("file_path", "")
    name = fp.split("/")[-1].split("\\")[-1]

    # Match by filename
    if name in _SCHEMA_FILENAMES:
        return True

    # Match by extension
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in _SCHEMA_EXTENSIONS:
        return True

    # Match by content markers
    if isinstance(artifact.content, str):
        for marker in _SCHEMA_CONTENT_MARKERS:
            if marker in artifact.content:
                return True

    return False


def _find_schema_files(sources) -> list:
    return [art for art in sources if _is_schema_file(art)]


def _build_schema_block(schema_files: list) -> str:
    parts = []
    for art in schema_files:
        fp = art.metadata.get("file_path", str(art.id))
        truncated = _head(art.content, _SCHEMA_LINES_LIMIT)
        parts.append(f"### {fp}\n{truncated}")
    return "\n\n".join(parts)


def _safe_parse(raw: str) -> dict:
    """Parse the LLM response, returning a best-effort dict on failure."""
    try:
        result = parse_json(raw)
        if isinstance(result, dict):
            return result
        return {"raw": raw, "parse_error": "Response was not a JSON object"}
    except Exception as exc:
        return {"raw": raw[:_SCHEMA_LINES_LIMIT * 80], "parse_error": str(exc)}
