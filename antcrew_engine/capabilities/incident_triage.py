"""IncidentTriageExecutor: maps a natural-language incident description to candidate programs.

Given an incident text and a COBOLIndex, the executor:
  1. Enumerates all indexed programs and their DB2 access patterns.
  2. Asks the LLM which programs are likely involved in the incident.
  3. Returns a ``triage_analysis`` artifact with candidate programs, reasoning,
     and affected tables.

Output JSON schema::

    {
      "incident": "<original incident text>",
      "candidate_programs": ["REHPOL", "VALDAT"],
      "affected_tables":    ["POLIZAS", "MOVIMIENTOS"],
      "risk_area":          "Date calculation in policy rehabilitation",
      "reasoning":          "<why these programs were selected>"
    }
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

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
    from antcrew.augment.cobol.index import COBOLIndex
    from antcrew_engine.engine import ArtifactStore, Goal


_SYSTEM = """\
You are a mainframe COBOL expert specializing in incident triage.

You will receive:
  1. An incident description from an operations or support team.
  2. A catalog of all COBOL programs in the system with their DB2 table access patterns.

Your task: identify the programs most likely involved in or affected by the incident.

Return ONLY a JSON object with this exact structure:
{
  "candidate_programs": ["PROGRAM1", "PROGRAM2"],
  "affected_tables":    ["TABLE1", "TABLE2"],
  "risk_area":          "<short description of the risk area, e.g. 'Date calculation in rehab'>",
  "reasoning":          "<2-4 sentences explaining why these programs are candidates>"
}

Guidelines:
- candidate_programs: list ONLY programs from the provided catalog (use exact names, uppercase).
- affected_tables: list DB2 tables likely involved, from the catalog or implied by the incident.
- Start with the most likely root-cause program, then add directly called or related programs.
- If the incident mentions dates, calculations, or validations, include utility programs that handle those.
- Return ONLY the JSON object. No markdown fences, no prose.
"""


class IncidentTriageExecutor(BaseExecutor):
    """Triage an incident text against a COBOL program inventory."""

    descriptor = CapabilityDescriptor(
        name        = "incident_triage",
        description = "Maps incident text to candidate COBOL programs using LLM + program catalog.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("incident_triaged")]),
        emits       = frozenset(["triage_analysis"]),
        cost        = 1.0,
    )

    def __init__(self, llm=None, *, cobol_index: "Optional[COBOLIndex]" = None) -> None:
        super().__init__(llm)
        self._cobol_index = cobol_index

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        incident = goal.description

        if self._cobol_index is None:
            return CapabilityResult(errors=["IncidentTriageExecutor: no cobol_index provided."])

        catalog = self._build_catalog()
        if not catalog:
            return CapabilityResult(errors=["IncidentTriageExecutor: no programs indexed."])

        user = (
            f"## Incident\n\n{incident}\n\n"
            f"## Program catalog\n\n{catalog}"
        )
        raw = self._call_json(_SYSTEM, user)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {
                "candidate_programs": [],
                "affected_tables": [],
                "risk_area": "unknown",
                "reasoning": raw,
            }

        result = {"incident": incident, **parsed}

        artifact = Artifact(
            id      = ArtifactId("triage_analysis"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = json.dumps(result, ensure_ascii=False, indent=2),
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))

    def _build_catalog(self) -> str:
        idx = self._cobol_index
        programs = idx.all_programs()
        if not programs:
            return ""

        lines: list[str] = []
        for prog in programs:
            tables = idx.db2_tables(prog)
            callers = idx.who_calls(prog)
            if tables:
                table_summary = ", ".join(
                    f"{t['table']}({t['op']})" for t in tables
                )
            else:
                table_summary = "no DB2 access"
            caller_note = f"  called_by=[{', '.join(callers)}]" if callers else ""
            lines.append(f"  {prog}: {table_summary}{caller_note}")

        return "programs:\n" + "\n".join(lines)
