from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import CodeArtifact, DevOpsArtifact, Ticket, coerce_list
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a DevOps Engineer on a software development team.
Given the code artifacts produced by developers, generate the infrastructure and deployment files needed to ship them.

Respond ONLY with a valid JSON array of DevOps artifact objects (no markdown fences, no prose):
[
  {
    "file_path": "Dockerfile",
    "description": "What this file does",
    "language": "dockerfile",
    "content": "...full file content..."
  },
  ...
]

Always generate (at minimum):
1. Dockerfile — production-ready, multi-stage if beneficial.
2. .github/workflows/ci.yml — GitHub Actions pipeline that installs deps, runs tests, builds image.

Generate additionally when relevant:
- docker-compose.yml     : if the app has multiple services (DB, cache, etc.)
- kubernetes/            : Deployment + Service manifests if the app needs container orchestration
- .dockerignore          : always useful

Rules:
- Detect the runtime from the code artifacts (Python, Node.js, Go, etc.) and choose the correct base image.
- Use specific image tags, never "latest".
- CI pipeline must run tests before building the Docker image.
- Keep files minimal and production-ready.
"""

_REFINE_SYSTEM = """\
You are a DevOps Engineer. The reviewer provided feedback on your infrastructure files.
Update the DevOps artifacts to address the feedback while keeping everything else intact.

Current DevOps artifacts:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of DevOps artifact objects (no markdown fences, no prose).
Each object must include: file_path, description, language, content.
"""


class DevOpsAgent(BaseAgent):
    name = "devops"
    role_description = "Generates Dockerfile, CI/CD pipelines, and infra config from code artifacts."
    conversational = True
    consumes: list[str] = ["code_artifacts", "tickets"]
    produces: list[str] = ["devops_artifacts"]

    def run(self, state: TeamState) -> dict:
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        tickets = coerce_list(state, "tickets", Ticket)

        if not code_artifacts:
            return {
                "current_agent": self.name,
                "messages": [
                    {"role": "assistant", "content": "[DevOps] No code artifacts to process."}
                ],
            }

        context = (
            f"Tickets:\n{json.dumps([t.model_dump() for t in tickets], indent=2)}\n\n"
            f"Code Artifacts:\n{json.dumps([a.model_dump() for a in code_artifacts], indent=2)}"
        )
        raw_artifacts: list[dict] = self.system_parsed(_SYSTEM, context, list[dict])
        devops_artifacts = [
            DevOpsArtifact(
                **{k: v for k, v in a.items() if k in DevOpsArtifact.model_fields}
            )
            for a in raw_artifacts
        ]

        return {
            "devops_artifacts": devops_artifacts,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[DevOps] {len(devops_artifacts)} infrastructure files generated: "
                        + ", ".join(a.file_path for a in devops_artifacts[:4])
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: list[DevOpsArtifact], feedback: str) -> dict:
        raw_artifacts: list[dict] = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([a.model_dump() for a in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the DevOps configuration based on the feedback.",
            list[dict],
        )
        updated = [
            DevOpsArtifact(
                **{k: v for k, v in a.items() if k in DevOpsArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"devops_artifacts": updated}
