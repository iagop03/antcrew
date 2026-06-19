from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import DocumentationArtifact
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a Technical Writer on a software development team.
Given a PRD, tickets, and code artifacts, generate the documentation files needed for the project.

Respond ONLY with a valid JSON array of documentation artifact objects (no markdown fences, no prose):
[
  {
    "file_path": "README.md",
    "title": "Project README",
    "doc_type": "readme",
    "format": "markdown",
    "content": "...full markdown content..."
  },
  ...
]

Always generate (at minimum):
1. README.md — project overview, prerequisites, installation, usage, environment variables.
2. docs/ARCHITECTURE.md — component breakdown, data flow, key design decisions (derived from the PRD and code).

Generate additionally when relevant:
- docs/API.md         : if the code contains HTTP endpoints.
- docs/ADR-001.md     : one Architecture Decision Record per major design choice in the PRD.
- docs/DEPLOYMENT.md  : if DevOps artifacts exist, explain how to deploy.

Rules:
- Use clear, concise prose. No filler sentences.
- Code blocks must use triple backtick fences with language hints.
- File paths must be relative to project root.
"""

_REFINE_SYSTEM = """\
You are a Technical Writer. The reviewer provided feedback on your documentation.
Update the documentation artifacts to address the feedback.

Current documentation:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of documentation artifact objects (no markdown fences, no prose).
Each object must include: file_path, title, doc_type, format, content.
"""


class DocWriterAgent(BaseAgent):
    name = "doc_writer"
    role_description = "Generates README, architecture docs, and API reference from pipeline artifacts."
    conversational = True

    def run(self, state: TeamState) -> dict:
        prd = state.get("prd")
        tickets = state.get("tickets") or []
        code_artifacts = state.get("code_artifacts") or []
        devops_artifacts = state.get("devops_artifacts") or []

        if not prd and not code_artifacts:
            return {
                "current_agent": self.name,
                "messages": [
                    {"role": "assistant", "content": "[DocWriter] Nothing to document yet."}
                ],
            }

        context_parts: list[str] = []
        if prd:
            context_parts.append(f"PRD:\n{prd.model_dump_json(indent=2)}")
        if tickets:
            context_parts.append(
                f"Tickets:\n{json.dumps([t.model_dump() for t in tickets], indent=2)}"
            )
        if code_artifacts:
            # Include file paths + descriptions (not full content — keeps context lean)
            summaries = [{"file_path": a.file_path, "description": a.description,
                          "language": a.language} for a in code_artifacts]
            context_parts.append(f"Code files:\n{json.dumps(summaries, indent=2)}")
        if devops_artifacts:
            summaries = [{"file_path": a.file_path, "description": a.description}
                         for a in devops_artifacts]
            context_parts.append(f"DevOps files:\n{json.dumps(summaries, indent=2)}")

        raw = self.system(_SYSTEM, "\n\n".join(context_parts))
        raw_artifacts: list[dict] = _json_loads(_strip_fences(raw))
        doc_artifacts = [
            DocumentationArtifact(
                **{k: v for k, v in a.items() if k in DocumentationArtifact.model_fields}
            )
            for a in raw_artifacts
        ]

        return {
            "doc_artifacts": doc_artifacts,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[DocWriter] {len(doc_artifacts)} documentation files: "
                        + ", ".join(a.file_path for a in doc_artifacts[:4])
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: list[DocumentationArtifact], feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([a.model_dump() for a in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the documentation based on the feedback.",
        )
        raw_artifacts: list[dict] = _json_loads(_strip_fences(raw))
        updated = [
            DocumentationArtifact(
                **{k: v for k, v in a.items() if k in DocumentationArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"doc_artifacts": updated}
