"""RequirementsElicitationExecutor: analyse existing docs for gaps, ambiguities, and contradictions.

Given a goal and optionally a set of documents from the workspace, this capability:
  1. Reads all indexed docs via DocumentationReader
  2. Asks the LLM to identify ambiguities, contradictions, and missing information
  3. Returns a structured JSON artifact with categorised issues and clarifying questions

Output JSON schema::

    {
      "summary": "One-paragraph diagnosis",
      "ambiguities": [{"id": "A1", "doc_ref": "...", "issue": "...", "question": "..."}],
      "contradictions": [{"id": "C1", "docs": ["...", "..."], "issue": "...", "question": "..."}],
      "missing_info": [{"id": "M1", "area": "...", "issue": "...", "question": "..."}],
      "clarifying_questions": ["Q1: ...", "Q2: ..."]
    }
"""
from __future__ import annotations

import json

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from .base import BaseExecutor

_SYSTEM = """\
You are a senior business analyst specialising in requirements elicitation for legacy modernisation projects.

You will be given:
  - A goal description
  - One or more documents (functional specs, technical docs, COBOL source, data dictionaries, etc.)

Your task is to identify:
  1. **Ambiguities** — statements that are vague, open to multiple interpretations, or lack necessary detail
  2. **Contradictions** — statements in different documents (or in the same document) that conflict
  3. **Missing information** — areas the documents do not address but that are needed to implement the goal

Return a single JSON object with this exact structure:
{
  "summary": "<one paragraph>",
  "ambiguities": [
    {"id": "A1", "doc_ref": "<doc id or section>", "issue": "<what is ambiguous>", "question": "<clarifying question>"}
  ],
  "contradictions": [
    {"id": "C1", "docs": ["<ref1>", "<ref2>"], "issue": "<what conflicts>", "question": "<clarifying question>"}
  ],
  "missing_info": [
    {"id": "M1", "area": "<area of concern>", "issue": "<what is missing>", "question": "<clarifying question>"}
  ],
  "clarifying_questions": ["<Q1>", "<Q2>"]
}

Rules:
- Be specific. Quote the relevant text where possible.
- `clarifying_questions` is a flat deduplicated list of the most important questions to ask the client.
- Return ONLY the JSON object. No markdown fences.
"""


class RequirementsElicitationExecutor(BaseExecutor):
    """Analyse workspace documents for requirements gaps and produce structured JSON."""

    descriptor = CapabilityDescriptor(
        name        = "requirements_elicitation",
        description = "Reads workspace docs and identifies ambiguities, contradictions, and missing requirements.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("requirements_elicited")]),
        emits       = frozenset(["elicitation_report"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        doc_context = self._build_doc_context(goal.description)
        user = f"Goal: {goal.description}\n\n{doc_context}"

        raw = self._call(_SYSTEM, user)

        # Parse JSON — graceful fallback if LLM returns fences
        report: dict = {}
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.splitlines()[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            report = json.loads(cleaned)
        except json.JSONDecodeError:
            report = {"raw": raw, "parse_error": True}

        artifact = Artifact(
            id      = ArtifactId("elicitation_report"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = json.dumps(report, ensure_ascii=False, indent=2),
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))

    # ------------------------------------------------------------------

    def _build_doc_context(self, query: str) -> str:
        """Pull all available documents as LLM context."""
        if self._documentation is None:
            return ""
        try:
            from antcrew_engine.documentation import DocumentationReader
            reader = DocumentationReader(self._documentation)
            # Full listing as a table + top semantic matches
            listing = reader.list_docs_as_text()
            search_ctx = reader.search_as_text(query, top_k=8)
            return f"## Available documents\n\n{listing}\n\n{search_ctx}"
        except Exception:
            return self._doc_context(query, max_chars=6000)
