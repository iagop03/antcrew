"""ReviewFixer — applies code-review findings to source files.

Runs after CodeReviewer exhausts its retry budget (retry_limits={"code_reviewer": 2}).
The `resets_retries` tag gives CodeReviewer a fresh budget after each fix cycle so the
loop is: review → (review fails N times) → fix → review again → ...
"""
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

_SYSTEM = """\
You are a senior software engineer applying fixes from a code review.
Given source files and a code review report with critical/error findings, rewrite
ONLY the affected files to resolve every critical and error finding.

Output ONLY valid JSON — no markdown fences, no prose:
{
  "files": [
    {
      "file_path": "src/models.py",
      "content": "...complete corrected file content..."
    }
  ]
}

Rules:
- Include ONLY files that need changes based on critical/error findings
- Output COMPLETE file contents — never truncate or use placeholders
- Do not introduce new syntax errors
- Preserve working code — change only what the review flagged
- If no critical/error findings exist, return {"files": []}
"""


class ReviewFixer(BaseExecutor):
    """Rewrites source files to fix critical/error code review findings.

    Cost=3.5 ensures CodeReviewer (2.0) always runs before ReviewFixer.
    The `resets_retries` tag clears CodeReviewer's consecutive-stagnation count
    after each fix so it can re-review with a fresh retry budget.
    """

    descriptor = CapabilityDescriptor(
        name        = "review_fixer",
        description = "Applies fixes from a 'needs_changes' code review to source files.",
        needs       = frozenset([
            ConditionId("implementation_exists"),
        ]),
        produces    = frozenset([ConditionId("code_reviewed")]),
        emits       = frozenset(["source"]),
        cost        = 3.5,
        tags        = frozenset(["resets_retries"]),
    )

    def _run(self, store, goal) -> CapabilityResult:
        review = store.read(ArtifactId("review_report"))
        if not review or not isinstance(review.content, dict):
            return CapabilityResult(errors=["review_report not found — run CodeReviewer first"])

        if review.content.get("verdict") == "approved":
            return CapabilityResult(errors=["code is already approved — nothing to fix"])

        findings = [
            f for f in review.content.get("findings", [])
            if f.get("severity") in ("critical", "error")
        ]
        if not findings:
            return CapabilityResult(
                errors=["no critical/error findings — check retry_limits configuration"]
            )

        sources = store.list(ArtifactKind.SOURCE)
        if not sources:
            return CapabilityResult(errors=["no source artifacts to fix"])

        arch = store.read(ArtifactId("architecture"))
        arch_text = arch.content if arch else "No architecture document."

        findings_text = "\n".join(
            f"- [{f.get('severity','?').upper()}] {f.get('file','?')}: "
            f"{f.get('message','')}"
            + (f"\n  Suggestion: {f.get('suggestion','')}" if f.get("suggestion") else "")
            for f in findings
        )
        files_block = "\n\n".join(
            f"--- {s.metadata.get('file_path', str(s.id))} ---\n{s.content}"
            for s in sources
        )
        system = f"{_SYSTEM}\n\n## Project Architecture\n{_head(arch_text, 100)}"
        user = (
            f"Goal: {goal.description}\n\n"
            f"Code review findings to fix:\n{findings_text}\n\n"
            f"Source files:\n{files_block}"
        )

        raw    = self._call_json(system, user)
        parsed = _safe_parse_files(raw)

        if not parsed:
            return CapabilityResult(errors=[f"ReviewFixer returned no files: {raw[:200]}"])

        # Build a path→artifact lookup for fast matching
        path_to_art = {
            art.metadata.get("file_path", str(art.id)): art
            for art in sources
        }

        existing:  list[Artifact] = []
        new_files: list[Artifact] = []
        for entry in parsed:
            fp      = entry.get("file_path", "")
            content = entry.get("content", "")
            if not fp or not content:
                continue
            orig = path_to_art.get(fp)
            if orig is not None:
                existing.append(Artifact(
                    id       = orig.id,
                    kind     = ArtifactKind.SOURCE,
                    content  = content,
                    metadata = {**orig.metadata, "file_path": fp},
                ))
            else:
                new_files.append(Artifact(
                    id       = ArtifactId(fp.replace("/", "_").replace(".", "_")),
                    kind     = ArtifactKind.SOURCE,
                    content  = content,
                    metadata = {"file_path": fp},
                ))

        if not existing and not new_files:
            return CapabilityResult(errors=["ReviewFixer produced no valid file entries"])

        return CapabilityResult(delta=ArtifactDelta(
            modified = tuple(existing),
            created  = tuple(new_files),
        ))


def _safe_parse_files(raw: str) -> list[dict]:
    try:
        result = parse_json(raw)
        if isinstance(result, dict):
            return result.get("files", [])
        if isinstance(result, list):
            return result
    except Exception:
        pass
    return []
