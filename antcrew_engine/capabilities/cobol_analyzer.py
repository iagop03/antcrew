"""COBOLAnalyzerExecutor: structural and architectural analysis of COBOL programs.

Combines deterministic CobolParser output with LLM synthesis to produce:
  - Program map (divisions, sections, data items, paragraphs per file)
  - Dependency graph (CALL/COPY relationships)
  - Complexity metrics (data volume, paragraph count)
  - Modernisation risks and suggested refactors

Input: COBOL files from workspace (via DocumentationReader) or a goal referencing
specific programs. Output: JSON artifact `cobol_analysis`.

Output JSON schema::

    {
      "programs": [
        {
          "program_id": "PAYROLL",
          "source_file": "PAYROLL.cbl",
          "data_items_count": 142,
          "paragraphs": ["INIT-SECTION", "CALC-GROSS", ...],
          "calls": ["CALC-TAX", "WRITE-PAY-STUB"],
          "copies": ["EMPLOYEE-RECORD", "TAX-TABLES"],
          "working_storage_fields": ["WS-EMP-ID", "WS-GROSS-PAY", ...]
        }
      ],
      "dependency_graph": {"PAYROLL": ["CALC-TAX", "WRITE-PAY-STUB"]},
      "complexity_notes": ["..."],
      "modernisation_risks": ["..."],
      "suggested_refactors": ["..."],
      "summary": "<one paragraph>"
    }
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from antcrew_engine.engine import ArtifactStore, Goal


_SYSTEM = """\
You are a COBOL architect with 20+ years of experience in mainframe modernisation.

You will receive a structural summary of one or more COBOL programs produced by
a static parser. The summary includes divisions, sections, data items, paragraphs,
COPY statements, and CALL statements.

Your task is to produce a JSON object with this exact structure:
{
  "dependency_graph": {"PROGRAM_A": ["PROGRAM_B", "COPYBOOK_C"], ...},
  "complexity_notes": ["<note 1>", "<note 2>"],
  "modernisation_risks": ["<risk 1>", "<risk 2>"],
  "suggested_refactors": ["<refactor 1>", "<refactor 2>"],
  "summary": "<one paragraph diagnosis of the codebase>"
}

Guidelines:
- `complexity_notes`: flag programs with >100 data items, >50 paragraphs, deep PERFORM nesting patterns
- `modernisation_risks`: highlight implicit GOTO-like patterns, FILLER-heavy layouts, hard-coded literals
- `suggested_refactors`: suggest paragraph extraction, WORKING-STORAGE reduction, COPY consolidation
- `summary`: synthesise the overall health and main challenges
- Return ONLY the JSON object. No markdown fences.
"""


class COBOLAnalyzerExecutor(BaseExecutor):
    """Analyse COBOL source files for structure, dependencies, and modernisation risks."""

    descriptor = CapabilityDescriptor(
        name        = "cobol_analyzer",
        description = "Parses COBOL source files and returns structural analysis with dependency graph and risks.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("cobol_analyzed")]),
        emits       = frozenset(["cobol_analysis"]),
        cost        = 1.5,
    )

    def _run(self, store, goal: "Goal") -> CapabilityResult:
        program_summaries = self._collect_program_summaries()

        if not program_summaries:
            artifact = Artifact(
                id      = ArtifactId("cobol_analysis"),
                kind    = ArtifactKind.REQUIREMENTS,
                content = json.dumps({
                    "programs": [],
                    "dependency_graph": {},
                    "complexity_notes": [],
                    "modernisation_risks": [],
                    "suggested_refactors": [],
                    "summary": "No COBOL files found in workspace documents.",
                }, ensure_ascii=False, indent=2),
            )
            return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))

        parser_context = self._format_parser_output(program_summaries)
        user = f"Goal: {goal.description}\n\nParser output:\n\n{parser_context}"
        raw = self._call(_SYSTEM, user)

        llm_analysis: dict = {}
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.splitlines()[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            llm_analysis = json.loads(cleaned)
        except json.JSONDecodeError:
            llm_analysis = {"raw_llm_output": raw, "parse_error": True}

        result = {
            "programs": [s["meta"] for s in program_summaries],
            **llm_analysis,
        }

        artifact = Artifact(
            id      = ArtifactId("cobol_analysis"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = json.dumps(result, ensure_ascii=False, indent=2),
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))

    # ------------------------------------------------------------------

    def _collect_program_summaries(self) -> list[dict]:
        """Return list of {meta, text} dicts for each COBOL document in the workspace."""
        if self._documentation is None:
            return []

        from antcrew_engine.documentation import DocumentationReader
        from antcrew_engine.documentation.parsers.cobol import CobolParser

        reader = DocumentationReader(self._documentation)
        cobol_docs = [
            d for d in reader.list_docs()
            if d.doc_type in ("cobol", "cobol_source", "copybook")
            or (d.source_file or "").lower().endswith((".cbl", ".cob", ".cpy", ".copy"))
        ]

        parser = CobolParser()
        summaries: list[dict] = []

        for doc in cobol_docs:
            try:
                content = reader.read(doc.doc_id)
                suffix = Path(doc.source_file or doc.doc_id).suffix or ".cbl"
                with tempfile.NamedTemporaryFile(
                    suffix=suffix, delete=False, mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    parsed = parser.parse(tmp_path)
                finally:
                    import os
                    os.unlink(tmp_path)

                meta = {
                    "program_id": parsed.metadata.get("program_id") or doc.doc_id,
                    "source_file": doc.source_file or doc.doc_id,
                    "data_items_count": parsed.metadata.get("data_items_count", 0),
                    "paragraphs": parsed.metadata.get("paragraphs", []),
                    "calls": parsed.metadata.get("called_programs", []),
                    "copies": parsed.metadata.get("copybooks", []),
                    "working_storage_fields": parsed.metadata.get("working_storage_fields", []),
                }
                summaries.append({"meta": meta, "text": parsed.content})
            except Exception:
                pass

        return summaries

    def _format_parser_output(self, summaries: list[dict]) -> str:
        parts: list[str] = []
        for s in summaries:
            m = s["meta"]
            parts.append(
                f"=== {m['program_id']} ({m['source_file']}) ===\n"
                f"Data items: {m['data_items_count']}  |  "
                f"Paragraphs: {len(m['paragraphs'])}  |  "
                f"CALL: {', '.join(m['calls']) or 'none'}  |  "
                f"COPY: {', '.join(m['copies']) or 'none'}\n"
                f"WS fields (top-level): {', '.join(m['working_storage_fields'][:20]) or 'none'}\n\n"
                + s["text"][:1500]
            )
        return "\n\n".join(parts)
