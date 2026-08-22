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
You are a senior software engineer conducting a code review.
Given source files, the project architecture, and optional test results,
produce a structured review.

Output ONLY a valid JSON object — no markdown fences, no prose:
{
  "summary": "one paragraph overall assessment",
  "verdict": "approved",
  "findings": [
    {
      "file": "src/models.py",
      "severity": "critical",
      "message": "what the issue is",
      "suggestion": "how to fix it"
    }
  ]
}

severity levels (in order of importance):
  critical — code is broken or has a security vulnerability
  error    — significant bug or logic error
  warning  — suboptimal code, potential issue
  info     — style or minor improvement

verdict rules:
  "approved"      — zero critical or error findings
  "needs_changes" — at least one critical or error finding

Review criteria:
  - Correctness: does the code do what the spec says?
  - Security: injection, auth, data exposure?
  - Architecture alignment: does it match the documented design?
  - Code quality: clarity, naming, single responsibility
  - Edge cases: null inputs, empty collections, error paths

Be specific — reference exact file names and line numbers where possible.
"""


class CodeReviewer(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "code_reviewer",
        description = "Reviews source artifacts against architecture and produces a structured report.",
        needs       = frozenset([
            ConditionId("implementation_exists"),
            ConditionId("tests_pass"),
        ]),
        produces    = frozenset([ConditionId("code_reviewed")]),
        emits       = frozenset(["report"]),
        cost        = 2.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources   = store.list(ArtifactKind.SOURCE)
        arch      = store.read(ArtifactId("architecture"))
        tr        = store.read(ArtifactId("test_report"))

        if not sources:
            return CapabilityResult(errors=["no source artifacts to review"])

        files_text = "\n\n".join(
            f"--- {s.metadata.get('file_path', s.id)} ---\n{_head(s.content, 300)}"
            for s in sources
        )
        arch_text = arch.content if arch else "No architecture document available."
        test_text = ""
        if tr and isinstance(tr.content, dict):
            status    = "PASSED" if tr.content.get("passed") else "FAILED"
            test_text = f"\nTest results: {status}\n{tr.content.get('output', '')[-1000:]}"

        system = f"{_SYSTEM}\n\n## Project Architecture\n{arch_text}"
        user = (
            f"Goal: {goal.description}\n\n"
            f"Source files:\n{files_text}"
            f"{test_text}"
        )

        raw    = self._call_json(system, user)
        review = _safe_parse_review(raw)

        artifact = Artifact(
            id      = ArtifactId("review_report"),
            kind    = ArtifactKind.REPORT,
            content = review,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))


def _safe_parse_review(raw: str) -> dict:
    try:
        result = parse_json(raw)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    return {"summary": raw, "verdict": "needs_changes", "findings": []}
