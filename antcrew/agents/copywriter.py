from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import ArtifactContract, ContentPiece, ContractError, ResearchDocument
from antcrew.core.state import TeamState

_PIECE_CONTRACT: ArtifactContract[ContentPiece] = ArtifactContract("content_piece", ContentPiece)
_DOC_CONTRACT: ArtifactContract[ResearchDocument] = ArtifactContract("research_document", ResearchDocument)

_SYSTEM = """\
You are a professional Copywriter. Given a content brief (title, audience, tone, outline),
write a complete, polished piece of content.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "body": "<the full content, in Markdown>",
  "word_count": <integer>
}

Rules:
- Follow the outline exactly, using the section headings from the brief.
- Match the specified tone throughout.
- Write for the target audience — calibrate vocabulary and depth accordingly.
- body must be valid Markdown: use ##/### headings, bullet lists, bold where appropriate.
- word_count must match the actual word count of "body".
"""

_REFINE_SYSTEM = """\
You are a professional Copywriter. The reviewer provided feedback on your draft.
Update the content to address the feedback while keeping the same structure and tone.

Current content:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the updated JSON object: {{ "body": "...", "word_count": <int> }}
(no markdown fences, no prose outside the JSON).
"""


class CopywriterAgent(BaseAgent):
    name = "copywriter"
    role_description = "Writes the full content body from a content brief."
    conversational = True
    consumes: list[str] = ["content_piece", "research_document"]
    produces: list[str] = ["content_piece"]

    def run(self, state: TeamState) -> dict:
        try:
            piece = _PIECE_CONTRACT.extract(state)
        except ContractError:
            piece = None
        if piece is None:
            # Fallback: build a minimal brief from a research document (ResearchTeam pipeline)
            try:
                doc = _DOC_CONTRACT.extract(state)
            except ContractError:
                doc = None
            if doc:
                piece = ContentPiece(
                    title=doc.title,
                    target_audience="researchers and practitioners",
                    tone="professional",
                    outline=[s.heading for s in doc.sections],
                )
            else:
                return {
                    "errors": ["CopywriterAgent: no content_piece or research_document in state"],
                    "current_agent": self.name,
                }
        data: dict = self.system_parsed(_SYSTEM, f"Content brief:\n{piece.model_dump_json(indent=2)}", dict)
        updated = piece.model_copy(
            update={
                "body": data.get("body", ""),
                "word_count": data.get("word_count"),
            }
        )
        return {
            "content_piece": updated,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Copywriter] '{updated.title}' written — "
                        f"~{updated.word_count or '?'} words."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact, feedback: str) -> dict:
        data: dict = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Revise the content based on the feedback.",
            dict,
        )
        updated = artifact.model_copy(update={
            "body": data.get("body", artifact.body),
            "word_count": data.get("word_count", artifact.word_count),
        })
        return {"content_piece": updated}
