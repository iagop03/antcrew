from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _strip_fences
from antcrew.core.artifacts import CodeArtifact
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a Senior Frontend Developer on a software development team.
Given a development ticket, produce the frontend code needed to implement it.

Determine the technology stack from the ticket description or acceptance criteria.
If no stack is specified, default to React with TypeScript.

Common stacks and their conventions:
- React / TypeScript : .tsx components, hooks, functional style
- Vue 3              : .vue SFCs with <script setup> and TypeScript
- Angular            : .ts files with Angular decorators and modules
- Svelte             : .svelte files with reactive declarations
- Vanilla JS / TS   : plain .js or .ts, no framework

Respond ONLY with a valid JSON array of code artifact objects (no markdown fences, no prose):
[
  {
    "file_path": "src/...",
    "description": "What this file/component does",
    "language": "<detected language>",
    "content": "...full file content..."
  },
  ...
]

Rules:
- Produce only the files strictly required to fulfil the ticket.
- Do NOT generate backend files (API routes, DB models, etc.).
- Write clean, idiomatic code that fits the detected stack.
"""

_REFINE_SYSTEM = """\
You are a Senior Frontend Developer. The reviewer provided feedback on your frontend code.
Update the code artifacts to address the feedback while keeping the same technology stack.

Current code artifacts:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of code artifact objects (no markdown fences, no prose).
Each object must include: ticket_id, file_path, description, language, content.
"""


class FrontendDevAgent(BaseAgent):
    name = "frontend_dev"
    role_description = "Implements open tickets as frontend code (stack detected from ticket)."
    conversational = True

    def run(self, state: TeamState) -> dict:
        tickets = state.get("tickets") or []

        if not tickets:
            return {
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[FrontendDev] No tickets."}],
            }

        query = " ".join(t.title for t in tickets[:5])
        system_prompt = _SYSTEM + self._search_repo(query) + self._recall(query)

        new_artifacts: list[CodeArtifact] = []

        for ticket in tickets:
            raw = self.system(system_prompt, f"Ticket:\n{ticket.model_dump_json(indent=2)}")
            raw_artifacts: list[dict] = json.loads(_strip_fences(raw))
            new_artifacts.extend(
                CodeArtifact(
                    ticket_id=ticket.id,
                    **{k: v for k, v in a.items() if k in CodeArtifact.model_fields},
                )
                for a in raw_artifacts
            )

        # Accumulate — don't overwrite backend artifacts already in state
        existing = state.get("code_artifacts") or []
        return {
            "code_artifacts": existing + new_artifacts,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[FrontendDev] {len(new_artifacts)} frontend files generated "
                        f"across {len(tickets)} tickets."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: list[CodeArtifact], feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([a.model_dump() for a in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the frontend code based on the feedback.",
        )
        raw_artifacts: list[dict] = json.loads(_strip_fences(raw))
        updated = [
            CodeArtifact(
                **{k: v for k, v in a.items() if k in CodeArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"code_artifacts": updated}
