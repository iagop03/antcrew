from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import CodeArtifact, TestArtifact, Ticket, coerce_list
from antcrew.core.state import TeamState

_TS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _extract_symbols_context(source: str, file_path: str) -> str:
    """Return a short context block listing the public symbols in *source*.

    For Python files we parse with ``ast`` to get exact names and signatures.
    For TypeScript/JavaScript we use regex to find exported names.
    The block is prepended to the QA prompt so the agent knows what to import.
    """
    suffix = Path(file_path).suffix.lower()
    if suffix == ".py":
        return _python_symbols(source)
    if suffix in _TS_EXTS:
        return _ts_symbols(source)
    return ""


_TS_EXPORTED = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|const|let|var)\s+(\w+)",
    re.MULTILINE,
)


def _ts_symbols(source: str) -> str:
    """Regex-scan TypeScript/JavaScript source and return exported names."""
    names = list(dict.fromkeys(m.group(1) for m in _TS_EXPORTED.finditer(source)))
    if not names:
        return ""
    return (
        "Public exports in this file (import exactly these names):\n"
        + "\n".join(f"  {n}" for n in names)
        + "\n\n"
    )


def _python_symbols(source: str) -> str:
    """Parse Python source and return a concise symbol listing."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    lines: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_"):
                args = [a.arg for a in node.args.args]
                lines.append(f"  def {node.name}({', '.join(args)})")
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                lines.append(f"  class {node.name}")

    if not lines:
        return ""
    return "Public symbols in this file (import exactly these names):\n" + "\n".join(lines) + "\n\n"

# File extensions that are worth testing; everything else is skipped.
_TESTABLE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".java", ".rs", ".rb", ".php", ".cs",
}

_SYSTEM = """\
You are a QA Engineer on a software development team.
Given ONE source file, write a focused test file that tests the ACTUAL
implementation — the functions, classes, and behaviours present in the code,
not a hypothetical spec.

Respond ONLY with a valid JSON array containing EXACTLY ONE test artifact object \
(no markdown fences, no prose):
[
  {
    "ticket_id": "TICKET-001",
    "file_path": "tests/test_...",
    "description": "What this test file covers",
    "language": "python",
    "content": "...full test file content...",
    "coverage_areas": ["unit", "integration"]
  }
]

Rules:
- One test file per source file — do NOT combine multiple source files.
- Import EXACTLY the names visible in the source (functions, classes, constants).
- Cover the 3-5 most important behaviours: happy path, key edge cases, one error path.
- Keep the test file under 120 lines.
- Use pytest for Python, Vitest/Jest for TypeScript/JavaScript.
- Mock external services and databases.
- Each test must be independently runnable.
- DO NOT test names that are not exported by the source file.
"""

_BUG_DETECTOR_SYSTEM = """\
You are a QA Engineer reviewing code for critical bugs.
Given code artifacts, determine if any CRITICAL bugs exist.

Respond ONLY with a JSON object:
{
  "has_critical_bugs": true/false,
  "critical_bug_count": <int>,
  "summary": "<one-line description of what was found>"
}
"""

_REFINE_SYSTEM = """\
You are a QA Engineer. The reviewer provided feedback on your test suite.
Update the tests to address the feedback while keeping everything else intact.

Current tests:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of test artifact objects (no markdown fences, no prose).
"""


class QAAgent(BaseAgent):
    name = "qa"
    role_description = "Writes tests for code artifacts and flags critical bugs."
    conversational = True
    consumes: list[str] = ["code_artifacts", "tickets", "test_artifacts"]
    produces: list[str] = ["test_artifacts", "metadata"]

    def run(self, state: TeamState) -> dict:
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        tickets = coerce_list(state, "tickets", Ticket)

        if not code_artifacts:
            return {
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[QA] No code artifacts to test."}],
                "metadata": {"has_critical_bugs": False, "no_critical_bugs": True},
            }

        # Filter to current sprint's tickets when available; otherwise process everything.
        current_ticket_ids = {t.id for t in tickets}
        sprint_artifacts = (
            [a for a in code_artifacts if a.ticket_id in current_ticket_ids]
            if current_ticket_ids
            else list(code_artifacts)
        )

        # Skip non-testable files (CSS, JSON, markdown, configs, etc.).
        testable = [
            a for a in sprint_artifacts
            if Path(a.file_path).suffix.lower() in _TESTABLE_EXTS
        ]

        ticket_map = {t.id: t for t in tickets}
        kb_ctx = str(state.get("_kb_context") or "")
        repo_query = "tests fixtures " + " ".join(a.file_path for a in testable[:4])
        system_prompt = _SYSTEM + kb_ctx + self._search_repo(repo_query)

        new_tests: list[TestArtifact] = []
        for a in testable:
            ticket = ticket_map.get(a.ticket_id)
            ticket_ctx = f"Ticket:\n{ticket.model_dump_json(indent=2)}\n\n" if ticket else ""
            symbols_ctx = _extract_symbols_context(a.content, a.file_path)
            art_json = json.dumps([a.model_dump()], indent=2)
            context = f"{ticket_ctx}{symbols_ctx}Source file:\n{art_json}"
            raw = self.system(system_prompt, context)
            stripped = _strip_fences(raw)
            try:
                raw_tests: list[dict] = _json_loads(stripped) if stripped else []
            except Exception:
                raw_tests = []
            new_tests.extend(
                TestArtifact(**{k: v for k, v in t.items() if k in TestArtifact.model_fields})
                for t in raw_tests
            )

        # Preserve tests from previous sprints; replace current sprint's.
        existing_tests = coerce_list(state, "test_artifacts", TestArtifact)
        preserved_tests = [t for t in existing_tests if t.ticket_id not in current_ticket_ids]
        test_artifacts = preserved_tests + new_tests

        artifacts_json = json.dumps([a.model_dump() for a in sprint_artifacts], indent=2)
        bug_result: dict = self.system_parsed(
            _BUG_DETECTOR_SYSTEM, f"Code Artifacts:\n{artifacts_json}", dict
        )
        has_critical = bool(bug_result.get("has_critical_bugs", False))

        return {
            "test_artifacts": test_artifacts,
            "current_agent": self.name,
            "metadata": {
                "has_critical_bugs": has_critical,
                "no_critical_bugs": not has_critical,
                "qa_summary": bug_result.get("summary", ""),
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[QA] {len(test_artifacts)} test files generated. "
                        f"Critical bugs: {'YES' if has_critical else 'none'}."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: list[TestArtifact], feedback: str) -> dict:
        raw_tests: list[dict] = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([t.model_dump() for t in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the tests based on the feedback.",
            list[dict],
        )
        updated = [
            TestArtifact(
                **{k: v for k, v in t.items() if k in TestArtifact.model_fields}
            )
            for t in raw_tests
        ]
        return {"test_artifacts": updated}
