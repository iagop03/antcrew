from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import ContentPiece
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a Creative Strategist and Content Planner. Given a content request, produce a content brief.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "title": "<catchy, specific title>",
  "target_audience": "<who this is for>",
  "tone": "<professional|conversational|educational|persuasive|inspirational>",
  "outline": [
    "<section 1: heading and one-sentence description>",
    "<section 2: heading and one-sentence description>",
    ...
  ]
}

Rules:
- outline must have 4-7 sections.
- title should be specific and compelling, not generic.
- target_audience: describe the reader (role, level, context).
- tone must be one of the five options listed.
"""

_REFINE_SYSTEM = """\
You are a Creative Strategist. The reviewer provided feedback on your content brief.
Update the brief to address the feedback while keeping everything else intact.

Current brief:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated content brief JSON (no markdown fences, no prose).
"""


class IdeaAgent(BaseAgent):
    name = "idea"
    role_description = "Creates a structured content brief from a content request."
    conversational = True

    def run(self, state: TeamState) -> dict:
        request = state.get("request") or ""
        raw = self.system(_SYSTEM, f"Content request:\n{request}")
        data: dict = _json_loads(_strip_fences(raw))
        brief = ContentPiece(
            title=data.get("title", request[:80]),
            target_audience=data.get("target_audience", "general audience"),
            tone=data.get("tone", "professional"),
            outline=data.get("outline", []),
        )
        return {
            "content_piece": brief,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Idea] '{brief.title}' — "
                        f"{len(brief.outline)}-section outline for '{brief.target_audience}'."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: ContentPiece, feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Revise the content brief based on the feedback.",
        )
        data: dict = _json_loads(_strip_fences(raw))
        updated = ContentPiece(
            title=data.get("title", artifact.title),
            target_audience=data.get("target_audience", artifact.target_audience),
            tone=data.get("tone", artifact.tone),
            outline=data.get("outline", artifact.outline),
        )
        return {"content_piece": updated}
