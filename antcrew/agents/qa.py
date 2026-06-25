from __future__ import annotations

import json
from pathlib import Path

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import TestArtifact
from antcrew.core.state import TeamState

# File extensions that are worth testing; everything else is skipped.
_TESTABLE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".java", ".rs", ".rb", ".php", ".cs",
}

_SYSTEM = """\
You are a QA Engineer on a software development team.
Given ONE source file, write a focused test file for it.

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
- Cover the 3-5 most important behaviours: happy path, key edge cases, one error path.
- Keep the test file under 120 lines.
- Use pytest for Python, Vitest/Jest for TypeScript/JavaScript.
- Mock external services and databases.
- Each test must be independently runnable.
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
        code_artifacts = state.get("code_artifacts") or []
        tickets = state.get("tickets") or []

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
        repo_query = "tests fixtures " + " ".join(a.file_path for a in testable[:4])
        system_prompt = _SYSTEM + self._search_repo(repo_query)

        new_tests: list[TestArtifact] = []
        for a in testable:
            ticket = ticket_map.get(a.ticket_id)
            ticket_ctx = f"Ticket:\n{ticket.model_dump_json(indent=2)}\n\n" if ticket else ""
            art_json = json.dumps([a.model_dump()], indent=2)
            context = f"{ticket_ctx}Source file:\n{art_json}"
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
        existing_tests = state.get("test_artifacts") or []
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
