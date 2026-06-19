from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import ResearchDocument, ResearchSection
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a Research Analyst. Given a research topic or question, produce a structured research document.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "title": "<document title>",
  "topic": "<the research topic>",
  "key_findings": ["<finding 1>", "<finding 2>", ...],
  "sections": [
    {
      "heading": "<section heading>",
      "content": "<detailed section content>"
    },
    ...
  ],
  "sources": ["<source 1>", "<source 2>", ...]
}

Rules:
- Produce 3-6 sections covering background, analysis, implications, and conclusions.
- key_findings must be concise, actionable statements (one sentence each).
- sources: cite real domains/publications if known; otherwise use descriptive placeholders
  like "Industry report: Gartner 2024" or "Academic: IEEE proceedings".
- Be thorough and evidence-based in section content.
"""

_REFINE_SYSTEM = """\
You are a Research Analyst. The reviewer provided feedback on your research document.
Update the document to address the feedback while keeping everything else intact.

Current research document:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated research document JSON (no markdown fences, no prose).
"""


class ResearcherAgent(BaseAgent):
    name = "researcher"
    role_description = "Produces a structured research document from a topic or question."
    conversational = True

    def run(self, state: TeamState) -> dict:
        request = state.get("request") or ""
        raw = self.system(_SYSTEM, f"Research topic:\n{request}")
        data: dict = _json_loads(_strip_fences(raw))
        sections = [
            ResearchSection(heading=s["heading"], content=s["content"])
            for s in data.get("sections", [])
        ]
        doc = ResearchDocument(
            title=data.get("title", request[:80]),
            topic=data.get("topic", request),
            key_findings=data.get("key_findings", []),
            sections=sections,
            sources=data.get("sources", []),
        )
        return {
            "research_document": doc,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Researcher] '{doc.title}' — "
                        f"{len(doc.sections)} sections, "
                        f"{len(doc.key_findings)} key findings."
                    ),
                }
            ],
        }

    def refine(self, state: TeamState, artifact: ResearchDocument, feedback: str) -> dict:
        raw = self.system(
            _REFINE_SYSTEM.format(
                artifact_json=artifact.model_dump_json(indent=2),
                feedback=feedback,
            ),
            "Revise the research document based on the feedback.",
        )
        data: dict = _json_loads(_strip_fences(raw))
        sections = [
            ResearchSection(heading=s["heading"], content=s["content"])
            for s in data.get("sections", [])
        ]
        doc = ResearchDocument(
            title=data.get("title", artifact.title),
            topic=data.get("topic", artifact.topic),
            key_findings=data.get("key_findings", artifact.key_findings),
            sections=sections,
            sources=data.get("sources", artifact.sources),
        )
        return {"research_document": doc}
