from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import (
    PRD,
    ArtifactContract,
    CodeArtifact,
    ContractError,
    DevOpsArtifact,
    DocumentationArtifact,
    Ticket,
    coerce_list,
)
from antcrew.core.state import TeamState

_PRD_CONTRACT: ArtifactContract[PRD] = ArtifactContract("prd", PRD)

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
    consumes: list[str] = ["prd", "tickets", "code_artifacts", "devops_artifacts"]
    produces: list[str] = ["doc_artifacts"]

    def run(self, state: TeamState) -> dict:
        try:
            prd = _PRD_CONTRACT.extract(state)
        except ContractError:
            prd = None
        tickets = coerce_list(state, "tickets", Ticket)
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        devops_artifacts = coerce_list(state, "devops_artifacts", DevOpsArtifact)

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

        kb_ctx = str(state.get("_kb_context") or "")
        raw_artifacts: list[dict] = self.system_parsed(_SYSTEM + kb_ctx, "\n\n".join(context_parts), list[dict])
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
        raw_artifacts: list[dict] = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([a.model_dump() for a in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the documentation based on the feedback.",
            list[dict],
        )
        updated = [
            DocumentationArtifact(
                **{k: v for k, v in a.items() if k in DocumentationArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"doc_artifacts": updated}
