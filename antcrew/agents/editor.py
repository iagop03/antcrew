from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _strip_fences
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a professional Editor. Given a draft content piece, refine it for clarity, flow,
and impact while preserving the author's voice and the specified tone.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "body": "<the edited content, in Markdown>",
  "word_count": <integer>,
  "edit_notes": ["<change 1>", "<change 2>", ...]
}

Rules:
- Improve sentence variety, eliminate redundancy, and sharpen transitions.
- Fix grammar and punctuation errors.
- Do NOT change the outline structure or heading hierarchy.
- Do NOT alter the tone or target audience calibration.
- edit_notes: brief list of the substantive changes made (3-5 items).
"""

_REFINE_SYSTEM = """\
You are a professional Editor. The reviewer provided feedback on your edited content.
Apply the feedback as an additional round of editing while preserving the existing improvements.

Current edited content:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the updated JSON object:
{{ "body": "...", "word_count": <int>, "edit_notes": ["..."] }}
(no markdown fences, no prose outside the JSON).
"""


class EditorAgent(BaseAgent):
    name = "editor"
    role_description = "Refines a content draft for clarity, flow, and polish."
    conversational = True

    def run(self, state: TeamState) -> dict:
        piece = state.get("content_piece")
        if piece is None or not piece.body:
            return {
                "errors": ["EditorAgent: no content_piece body to edit"],
                "current_agent": self.name,
            }
        raw = self.system(_SYSTEM, f"Draft content:\n{piece.model_dump_json(indent=2)}")
        data: dict = json.loads(_strip_fences(raw))
        updated = piece.model_copy(
            update={
                "body": data.get("body", piece.body),
                "word_count": data.get("word_count", piece.word_count),
            }
        )
        edit_notes: list[str] = data.get("edit_notes", [])
        return {
            "content_piece": updated,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Editor] '{updated.title}' edited — "
                        f"{len(edit_notes)} changes: {'; '.join(edit_notes[:3])}."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact, feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Apply the additional feedback as another editing pass.",
        )
        data: dict = json.loads(_strip_fences(raw))
        updated = artifact.model_copy(update={
            "body": data.get("body", artifact.body),
            "word_count": data.get("word_count", artifact.word_count),
        })
        return {"content_piece": updated}
