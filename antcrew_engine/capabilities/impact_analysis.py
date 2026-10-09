"""ImpactAnalysisExecutor: runs transitive impact analysis on triage candidates.

Reads the ``triage_analysis`` artifact produced by :class:`IncidentTriageExecutor`
and runs :class:`~antcrew.augment.impact.analyzer.ImpactAnalyzer` against the
candidate programs to compute:
  - Transitive callers (who would also be affected)
  - DB2 tables touched (read and write)
  - Risk level (high / medium / low)

Produces an ``impact_analysis`` artifact.

Output JSON schema::

    {
      "candidate_programs":        ["REHPOL", "VALDAT"],
      "risk_level":                "high",
      "risk_reason":               "DB2 write operations",
      "affected_callers":          [],
      "affected_copybook_users":   ["MOVPOL"],
      "db2_tables_touched":        [{"table": "POLIZAS", "op": "UPDATE"}, ...],
      "out_of_scope_projects":     [],
      "modified_components":       [...]
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


class ImpactAnalysisExecutor(BaseExecutor):
    """Run ImpactAnalyzer on programs identified by incident triage."""

    descriptor = CapabilityDescriptor(
        name        = "impact_analysis",
        description = "Computes transitive impact (callers, DB2 tables, risk level) for incident candidates.",
        needs       = frozenset([ConditionId("incident_triaged")]),
        produces    = frozenset([ConditionId("impact_analyzed")]),
        emits       = frozenset(["impact_analysis"]),
        cost        = 0.5,
    )

    def __init__(self, llm=None, *, cobol_index: "Optional[COBOLIndex]" = None) -> None:
        super().__init__(llm)
        self._cobol_index = cobol_index

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        if self._cobol_index is None:
            return CapabilityResult(errors=["ImpactAnalysisExecutor: no cobol_index provided."])

        triage_art = store.read(ArtifactId("triage_analysis"))
        if triage_art is None:
            return CapabilityResult(errors=["ImpactAnalysisExecutor: triage_analysis artifact not found."])

        try:
            triage = json.loads(triage_art.content)
        except json.JSONDecodeError as exc:
            return CapabilityResult(errors=[f"ImpactAnalysisExecutor: invalid triage_analysis JSON: {exc}"])

        candidates = triage.get("candidate_programs", [])
        if not candidates:
            return CapabilityResult(errors=["ImpactAnalysisExecutor: no candidate_programs in triage_analysis."])

        from antcrew.augment.impact.analyzer import ImpactAnalyzer

        analyzer = ImpactAnalyzer(self._cobol_index)
        fake_paths = [
            f"{prog.upper()}.cbl"
            for prog in candidates
        ]
        analysis = analyzer.analyze(fake_paths, change_ref=triage.get("incident", "")[:80])

        def _comp(c):
            return {
                "name": c.name,
                "type": c.component_type,
                "reason": c.impact_reason,
                "db2_tables": c.db2_tables,
            }

        result = {
            "candidate_programs":       candidates,
            "risk_level":               analysis.risk_level,
            "risk_reason":              analysis.risk_reason,
            "affected_callers":         analysis.affected_callers,
            "affected_copybook_users":  analysis.affected_copybook_users,
            "db2_tables_touched":       analysis.db2_tables_touched,
            "out_of_scope_projects":    analysis.out_of_scope_projects,
            "modified_components":      [_comp(c) for c in analysis.modified_components],
        }

        artifact = Artifact(
            id      = ArtifactId("impact_analysis"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = json.dumps(result, ensure_ascii=False, indent=2),
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))
