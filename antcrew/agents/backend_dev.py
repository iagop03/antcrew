from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import CodeArtifact, TicketStatus
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a Senior Backend Developer on a software development team.
Given a development ticket, produce the code changes needed to implement it.

Respond ONLY with a valid JSON array of code artifact objects (no markdown fences, no prose):
[
  {
    "file_path": "src/...",
    "description": "What this file/change does",
    "language": "python",
    "content": "...full file content..."
  },
  ...
]

Rules:
- Produce only the files strictly required to fulfil the ticket.
- Write clean, idiomatic code with no unnecessary comments.
- If a ticket requires multiple files, include all of them.
"""

_REFINE_SYSTEM = """\
You are a Senior Backend Developer. The reviewer provided feedback on your code.
Update the code artifacts to address the feedback while keeping everything else intact.

Current code artifacts:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of code artifact objects (no markdown fences, no prose).
Each object must include: ticket_id, file_path, description, language, content.
"""


class BackendDevAgent(BaseAgent):
    name = "backend_dev"
    role_description = "Implements open tickets as code artifacts."
    conversational = True

    def run(self, state: TeamState) -> dict:
        tickets = state.get("tickets") or []
        open_tickets = [t for t in tickets if t.status == TicketStatus.OPEN]

        if not open_tickets:
            return {
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[Dev] No open tickets."}],
            }

        query = " ".join(t.title for t in open_tickets[:5])
        system_prompt = _SYSTEM + self._search_repo(query) + self._recall(query)

        all_artifacts: list[CodeArtifact] = []
        updated_tickets = list(tickets)

        for ticket in open_tickets:
            raw = self.system(system_prompt, f"Ticket:\n{ticket.model_dump_json(indent=2)}")
            raw_artifacts: list[dict] = _json_loads(_strip_fences(raw))
            artifacts = [
                CodeArtifact(
                    ticket_id=ticket.id,
                    **{k: v for k, v in a.items() if k in CodeArtifact.model_fields},
                )
                for a in raw_artifacts
            ]
            all_artifacts.extend(artifacts)
            idx = next(i for i, t in enumerate(updated_tickets) if t.id == ticket.id)
            updated_tickets[idx] = ticket.model_copy(update={"status": TicketStatus.DONE})

        # Preserve artifacts from other sprints; replace only the ones we just generated.
        existing = state.get("code_artifacts") or []
        current_ids = {t.id for t in open_tickets}
        preserved = [a for a in existing if a.ticket_id not in current_ids]
        return {
            "code_artifacts": preserved + all_artifacts,
            "tickets": updated_tickets,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Dev] {len(all_artifacts)} files generated "
                        f"across {len(open_tickets)} tickets."
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
            "Revise the code based on the feedback.",
        )
        raw_artifacts: list[dict] = _json_loads(_strip_fences(raw))
        updated = [
            CodeArtifact(
                **{k: v for k, v in a.items() if k in CodeArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"code_artifacts": updated}
