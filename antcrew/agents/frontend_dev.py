from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import CodeArtifact
from antcrew.core.state import TeamState

_PLAN_SYSTEM = """\
You are a Senior Frontend Developer.
Given a development ticket, list ALL frontend files you need to create or modify — no content yet.

Determine the technology stack from the ticket or acceptance criteria.
If no stack is specified, default to React with TypeScript.

Respond ONLY with a valid JSON array (no markdown fences, no prose):
[
  {"file_path": "src/...", "description": "What this file does", "language": "tsx"},
  ...
]

Rules:
- List ONLY frontend files strictly required to fulfil the ticket.
- Do NOT include backend files (API routes, DB models, server code).
- No file contents — paths and descriptions only.
- Keep the list focused: omit files the ticket does not require.
- If the ticket is a backend-only task, return an empty array: []
"""

_FILE_SYSTEM = """\
You are a Senior Frontend Developer.
Generate the COMPLETE content for exactly ONE frontend file.

Common stacks and their conventions:
- React / TypeScript : .tsx components, hooks, functional style
- Vue 3              : .vue SFCs with <script setup> and TypeScript
- Angular            : .ts files with Angular decorators and modules
- Svelte             : .svelte files with reactive declarations
- Vanilla JS / TS   : plain .js or .ts, no framework

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{{
  "file_path": "src/...",
  "description": "What this file does",
  "language": "tsx",
  "content": "...full file content..."
}}

Rules:
- Write clean, idiomatic, production-quality code for the detected stack.
- Complete implementation — no TODOs or placeholders.
- Do NOT produce backend/server-side code.
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


def _parse_list(raw: str) -> list[dict]:
    stripped = _strip_fences(raw)
    if not stripped:
        return []
    try:
        return _json_loads(stripped)
    except Exception:
        return []


def _parse_obj(raw: str) -> dict:
    stripped = _strip_fences(raw)
    if not stripped:
        return {}
    try:
        result = _json_loads(stripped)
        if isinstance(result, dict):
            return result
        # LLM returned a list instead of an object — take the first element.
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result[0]
        return {}
    except Exception:
        return {}


class FrontendDevAgent(BaseAgent):
    name = "frontend_dev"
    role_description = "Implements open tickets as frontend code (stack detected from ticket)."
    conversational = True
    consumes: list[str] = ["tickets", "code_artifacts"]
    produces: list[str] = ["code_artifacts"]

    def run(self, state: TeamState) -> dict:
        tickets = state.get("tickets") or []

        if not tickets:
            return {
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[FrontendDev] No tickets."}],
            }

        query = " ".join(t.title for t in tickets[:5])
        repo_ctx = self._search_repo(query) + self._recall(query)
        plan_prompt = _PLAN_SYSTEM + repo_ctx
        file_prompt = _FILE_SYSTEM + repo_ctx

        new_artifacts: list[CodeArtifact] = []

        for ticket in tickets:
            ticket_json = ticket.model_dump_json(indent=2)

            # Phase 1 — plan: get the list of frontend files to create (no content).
            plan_raw = self.system(plan_prompt, f"Ticket:\n{ticket_json}")
            file_specs = _parse_list(plan_raw)

            sibling_paths = [s.get("file_path", "") for s in file_specs]

            # Phase 2 — generate each file individually.
            for spec in file_specs:
                context = (
                    f"Ticket:\n{ticket_json}\n\n"
                    f"File to generate:\n{json.dumps(spec, indent=2)}\n\n"
                    f"Other files in this ticket (for imports/references): {sibling_paths}"
                )
                file_raw = self.system(file_prompt, context)
                file_data = _parse_obj(file_raw)
                if file_data.get("content") and file_data.get("file_path"):
                    new_artifacts.append(
                        CodeArtifact(
                            ticket_id=ticket.id,
                            **{k: v for k, v in file_data.items() if k in CodeArtifact.model_fields},
                        )
                    )

        # Accumulate — keep backend artifacts already in state for this and past sprints.
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
        raw_artifacts: list[dict] = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([a.model_dump() for a in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the frontend code based on the feedback.",
            list[dict],
        )
        updated = [
            CodeArtifact(
                **{k: v for k, v in a.items() if k in CodeArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"code_artifacts": updated}
