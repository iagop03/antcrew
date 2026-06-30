"""CoherenceAgent — cross-file consistency pass after code generation.

One LLM call that receives ALL generated code files together and verifies:
- Import paths between files (no missing or invented modules)
- Function/class names referenced across files match their definitions
- Shared types and return types are consistent

The agent returns a corrected list of code artifacts.  Files that need no
changes are returned unchanged.  The pass is optional (not in the default
DevTeam flow) but can be inserted between backend_dev and qa.
"""
from __future__ import annotations

import json
import logging

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import CodeArtifact, coerce_list
from antcrew.core.state import TeamState

log = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 40_000  # safety cap: drop least-important files if over limit

_SYSTEM = """\
You are a Senior Software Engineer performing a cross-file consistency review.

You will receive a set of code files that were generated together as part of
one feature.  Your job is to find and fix three categories of problems:

1. **Broken imports** — a file imports a name that does not exist in the
   referenced module (wrong name, wrong path, or the name was never defined).
2. **Signature mismatches** — a caller passes arguments that don't match the
   function's actual parameter list.
3. **Type inconsistencies** — a function is annotated to return type A but is
   used as if it returns type B elsewhere.

DO NOT rewrite working code.  Only change what is strictly necessary to make
the files consistent with each other.  Keep all existing logic intact.

Respond ONLY with a valid JSON array of ALL artifact objects (no markdown
fences, no prose), including both corrected and unchanged files.  Each object:
  ticket_id, file_path, description, language, content
"""

_NO_ISSUES = """\
Respond ONLY with a valid JSON array of ALL artifact objects unchanged
(same format as input) — no markdown fences, no prose.
"""


class CoherenceAgent(BaseAgent):
    """Verify and fix cross-file consistency in a set of code artifacts."""

    name = "coherence"
    role_description = "Checks that imports, signatures, and types are consistent across all generated files."
    conversational = False
    consumes: list[str] = ["code_artifacts"]
    produces: list[str] = ["code_artifacts", "coherence_issues"]

    def run(self, state: TeamState) -> dict:
        artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        if len(artifacts) < 2:
            # Nothing to cross-check with a single file
            return {
                "current_agent": self.name,
                "coherence_issues": [],
                "messages": [{"role": "assistant", "content": "[Coherence] Single file — skipped."}],
            }

        files_json = _build_context(artifacts)
        prompt = (
            f"Review these {len(artifacts)} files for cross-file consistency issues.\n\n"
            f"Files:\n{files_json}"
        )
        raw = self.system(_SYSTEM, prompt)
        stripped = _strip_fences(raw)
        try:
            raw_list: list[dict] = _json_loads(stripped) or []
        except Exception:
            raw_list = []

        updated = _parse_artifacts(raw_list, artifacts)
        issues = _detect_changes(artifacts, updated)

        log.info(
            "coherence: %d files reviewed, %d corrected",
            len(artifacts), len(issues),
        )
        return {
            "code_artifacts": updated,
            "current_agent": self.name,
            "coherence_issues": issues,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Coherence] {len(artifacts)} files reviewed. "
                        + (f"{len(issues)} consistency issue(s) corrected." if issues else "No issues found.")
                    ),
                }
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_context(artifacts: list[CodeArtifact]) -> str:
    """Serialise artifacts to JSON, truncating to stay under *_MAX_CONTEXT_CHARS*."""
    full = json.dumps([a.model_dump() for a in artifacts], indent=2)
    if len(full) <= _MAX_CONTEXT_CHARS:
        return full
    # Truncate content of large files — keep file_path / description intact
    truncated = []
    budget = _MAX_CONTEXT_CHARS
    for a in artifacts:
        d = a.model_dump()
        if len(d.get("content", "")) > budget // max(len(artifacts), 1):
            d["content"] = d["content"][: budget // len(artifacts)] + "\n# [truncated]"
        truncated.append(d)
    return json.dumps(truncated, indent=2)


def _parse_artifacts(raw: list[dict], originals: list[CodeArtifact]) -> list[CodeArtifact]:
    """Convert raw dicts back to CodeArtifact, falling back to originals on error."""
    if not raw:
        return originals

    original_map = {a.file_path: a for a in originals}
    result: list[CodeArtifact] = []
    returned_paths: set[str] = set()

    for d in raw:
        if not isinstance(d, dict):
            continue
        fp = d.get("file_path", "")
        if not fp:
            continue
        returned_paths.add(fp)
        try:
            result.append(CodeArtifact(**{k: v for k, v in d.items() if k in CodeArtifact.model_fields}))
        except Exception:
            if fp in original_map:
                result.append(original_map[fp])

    # Re-add any files the agent omitted (should not happen, but be safe)
    for orig in originals:
        if orig.file_path not in returned_paths:
            result.append(orig)

    return result


def _detect_changes(originals: list[CodeArtifact], updated: list[CodeArtifact]) -> list[str]:
    """Return list of file paths where content changed."""
    orig_map = {a.file_path: a.content for a in originals}
    return [a.file_path for a in updated if orig_map.get(a.file_path) != a.content]
