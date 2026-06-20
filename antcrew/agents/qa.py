from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import TestArtifact
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a QA Engineer on a software development team.
Given the code artifacts for ONE ticket, write comprehensive tests.

Respond ONLY with a valid JSON array of test artifact objects (no markdown fences, no prose):
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
- Cover happy paths AND edge cases AND error conditions.
- Use pytest for Python code, Vitest/Jest for TypeScript/JavaScript.
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

    def run(self, state: TeamState) -> dict:
        code_artifacts = state.get("code_artifacts") or []
        tickets = state.get("tickets") or []

        if not code_artifacts:
            return {
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[QA] No code artifacts to test."}],
                "metadata": {"has_critical_bugs": False, "no_critical_bugs": True},
            }

        # Group artifacts by ticket so each LLM call stays small
        by_ticket: dict[str, list] = {}
        for a in code_artifacts:
            by_ticket.setdefault(a.ticket_id, []).append(a)

        ticket_map = {t.id: t for t in tickets}
        repo_query = "tests fixtures " + " ".join(a.file_path for a in code_artifacts[:4])
        system_prompt = _SYSTEM + self._search_repo(repo_query)

        test_artifacts: list[TestArtifact] = []
        for ticket_id, arts in by_ticket.items():
            ticket = ticket_map.get(ticket_id)
            ticket_ctx = f"Ticket:\n{ticket.model_dump_json(indent=2)}\n\n" if ticket else ""
            arts_json = json.dumps([a.model_dump() for a in arts], indent=2)
            context = f"{ticket_ctx}Code Artifacts:\n{arts_json}"
            raw = self.system(system_prompt, context)
            raw_tests: list[dict] = _json_loads(_strip_fences(raw))
            test_artifacts.extend(
                TestArtifact(**{k: v for k, v in t.items() if k in TestArtifact.model_fields})
                for t in raw_tests
            )

        artifacts_json = json.dumps([a.model_dump() for a in code_artifacts], indent=2)
        bug_raw = self.system(
            _BUG_DETECTOR_SYSTEM, f"Code Artifacts:\n{artifacts_json}"
        )
        bug_result: dict = _json_loads(_strip_fences(bug_raw))
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
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([t.model_dump() for t in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the tests based on the feedback.",
        )
        raw_tests: list[dict] = _json_loads(_strip_fences(raw))
        updated = [
            TestArtifact(
                **{k: v for k, v in t.items() if k in TestArtifact.model_fields}
            )
            for t in raw_tests
        ]
        return {"test_artifacts": updated}
