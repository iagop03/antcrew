from __future__ import annotations

import re
from pathlib import Path

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from ._utils import parse_json
from .base import BaseExecutor

_SYSTEM = """\
You are a senior developer fixing bugs revealed by failing tests.
Given: the project goal, architecture, failing pytest output, and all source files.
Task: identify the root cause and generate fixed file contents.

Output ONLY a valid JSON array of files to change — no markdown fences, no prose:
[
  {"file_path": "src/models.py", "content": "...complete fixed file content..."},
  ...
]

Rules:
- Only include files that actually need changes
- Write the COMPLETE file content — no TODOs, no truncation
- Fix ONLY what the test output shows is failing; do not refactor unrelated code
- Do NOT modify or include test files
- If the bug requires a new helper file, include it
"""


class BugFixer(BaseExecutor):
    """Reads failing test output and generates source-code fixes.

    Does not re-run tests itself — relies on TestRunner to verify the fix in
    the next EngineLoop iteration.  The 'resets_retries' tag tells the EngineLoop
    to give TestRunner a fresh retry budget after BugFixer writes new code.
    """

    descriptor = CapabilityDescriptor(
        name        = "bug_fixer",
        description = "Analyses failing test output and rewrites source files to fix the bugs.",
        needs       = frozenset([
            ConditionId("tests_exist"),
            ConditionId("implementation_exists"),
        ]),
        produces    = frozenset([ConditionId("tests_pass")]),
        emits       = frozenset(["source"]),
        cost        = 3.0,   # higher than TestRunner (0.5) so TestRunner runs first
        tags        = frozenset(["resets_retries"]),
    )

    def _run(self, store, goal) -> CapabilityResult:
        test_report = store.read(ArtifactId("test_report"))
        if not test_report:
            return CapabilityResult(errors=["test_report not found — run TestRunner first"])

        report = test_report.content if isinstance(test_report.content, dict) else {}
        if report.get("passed"):
            return CapabilityResult(errors=["tests already pass — nothing to fix"])

        test_output = _compress_test_output(report.get("output", ""))
        sources = store.list(ArtifactKind.SOURCE)
        if not sources:
            return CapabilityResult(errors=["no source artifacts found in store"])

        path_to_art = {
            (art.metadata.get("file_path") or str(art.id)): art
            for art in sources
        }

        arch = store.read(ArtifactId("architecture"))
        arch_text = arch.content if arch else "No architecture document available."

        # Only send files referenced in the traceback — reduces context ~10×
        failing_files = _extract_failing_files(test_output)
        if failing_files:
            relevant = {
                fp: art for fp, art in path_to_art.items()
                if any(fp in f or f.endswith(fp) or Path(fp).name in f for f in failing_files)
            }
            if not relevant:
                relevant = path_to_art  # no match — send everything
        else:
            relevant = path_to_art

        file_linenos = _extract_file_linenos(test_output)

        def _get_lineno(fp: str) -> "int | None":
            from pathlib import Path as _Path
            for k, n in file_linenos.items():
                if fp in k or k.endswith(fp) or _Path(k).name == _Path(fp).name:
                    return n
            return None

        files_block = "\n\n".join(
            f"### {fp}\n```\n{_extract_context(art.content, _get_lineno(fp))}\n```"
            for fp, art in relevant.items()
        )

        system = f"{_SYSTEM}\n\n## Project Architecture\n{arch_text}"
        user = (
            f"Goal: {goal.description}\n\n"
            f"Failing test output:\n```\n{test_output}\n```\n\n"
            f"Source files:\n{files_block}"
        )

        raw = self._call_json(system, user)
        fixes = _safe_parse_list(raw)
        if not fixes:
            return CapabilityResult(errors=["LLM returned no fixes"])

        modified: list[Artifact] = []
        for fix in fixes:
            fp = (fix.get("file_path") or "").strip()
            new_content = (fix.get("content") or "").strip()
            if not fp or not new_content:
                continue
            original = path_to_art.get(fp)
            if original is not None:
                modified.append(Artifact(
                    id=original.id,
                    kind=ArtifactKind.SOURCE,
                    content=new_content,
                    metadata=original.metadata,
                ))
            else:
                modified.append(Artifact(
                    id=ArtifactId(f"src/{fp}"),
                    kind=ArtifactKind.SOURCE,
                    content=new_content,
                    metadata={"file_path": fp},
                ))

        if not modified:
            return CapabilityResult(errors=["no valid file fixes could be parsed from LLM output"])

        return CapabilityResult(delta=ArtifactDelta(modified=tuple(modified)))


def _compress_test_output(raw: str, max_chars: int = 2_000) -> str:
    """Extract high-signal lines from pytest output; fall back to tail if nothing matches."""
    lines = raw.splitlines()
    keep  = []
    for line in lines:
        s = line.strip()
        if (s.startswith("FAILED ") or s.startswith("ERROR ")
                or re.match(r"E\s+\w", s)
                or re.match(r'File ".+\.py", line \d+', s)
                or re.match(r"\w[^\s:]+\.py:\d+:", s)):
            keep.append(line)
    compressed = "\n".join(keep)
    if len(compressed) > max_chars:
        compressed = compressed[:max_chars]
    return compressed if compressed.strip() else raw[-max_chars:]


def _extract_file_linenos(test_output: str) -> "dict[str, int]":
    """Parse traceback-style lines and return {file_path: line_number} mappings."""
    result: dict[str, int] = {}
    for m in re.finditer(r'File\s+"([^"]+\.py)",\s+line\s+(\d+)', test_output):
        result[m.group(1)] = int(m.group(2))
    for m in re.finditer(r"^(\S[^\s:]+\.py):(\d+):", test_output, re.MULTILINE):
        fp = m.group(1)
        if "/" in fp or "\\" in fp:
            result[fp] = int(m.group(2))
    return result


def _extract_context(content: str, lineno: "int | None", context: int = 40) -> str:
    """Return ±context lines around lineno; fall back to first 80 lines."""
    lines = content.splitlines() if isinstance(content, str) else []
    if not lineno or lineno < 1:
        return "\n".join(lines[:80])
    start  = max(0, lineno - 1 - context)
    end    = min(len(lines), lineno - 1 + context)
    chunk  = lines[start:end]
    prefix = f"# ... ({start} lines before)\n" if start > 0 else ""
    suffix = f"\n# ... ({len(lines) - end} lines after)" if end < len(lines) else ""
    return prefix + "\n".join(chunk) + suffix


def _safe_parse_list(raw: str) -> list[dict]:
    try:
        result = parse_json(raw)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _extract_failing_files(test_output: str) -> set[str]:
    """Extract source file paths mentioned in a pytest --tb=short traceback."""
    paths: set[str] = set()
    # 'File "/tmp/.../src/models.py", line 42, in ...'
    for m in re.finditer(r'File\s+"([^"]+\.py)"', test_output):
        paths.add(m.group(1))
    # 'src/models.py:42: AssertionError' (short tb style)
    for m in re.finditer(r'^(\S[^\s:]+\.py):\d+:', test_output, re.MULTILINE):
        candidate = m.group(1)
        if "/" in candidate or "\\" in candidate:
            paths.add(candidate)
    return paths
