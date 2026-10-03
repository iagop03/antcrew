"""ImpactAnalyzer — maps VCS changed files to transitive COBOL impact.

Consumes:
  - a :class:`~antcrew.augment.cobol.index.COBOLIndex` (inverse call + copy + DB2)
  - a list of changed file paths (from VCSAdapter)

Produces: :class:`~antcrew.augment.impact.models.ImpactAnalysis`

Risk heuristics:
  - ``high``   : DB2 table writes (UPDATE/INSERT/DELETE) or > 5 transitive callers
  - ``medium`` : DB2 reads only, or 2-5 callers
  - ``low``    : no DB2 access and ≤ 1 caller

Usage::

    from antcrew.augment.impact.analyzer import ImpactAnalyzer

    analyzer = ImpactAnalyzer(cobol_index)
    analysis = analyzer.analyze(
        changed_files=["src/ACCTUPD.cbl"],
        change_ref="CR-1234",
        program_to_project={"ACCTUPD": "accounts-service"},
    )
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from antcrew.augment.impact.models import ImpactAnalysis, ImpactedComponent

if TYPE_CHECKING:
    from antcrew.augment.cobol.index import COBOLIndex

_COBOL_EXTENSIONS = frozenset({".cbl", ".cob", ".cobol", ".CBL", ".COB"})
_COPYBOOK_EXTENSIONS = frozenset({".cpy", ".CPY", ".copy"})


class ImpactAnalyzer:
    """Analyzes the transitive impact of COBOL file changes."""

    def __init__(
        self,
        cobol_index: "COBOLIndex",
        *,
        program_to_project: Optional[dict[str, str]] = None,
    ) -> None:
        self._index = cobol_index
        self._program_to_project = program_to_project or {}

    def analyze(
        self,
        changed_files: list[str],
        *,
        change_ref: str = "",
    ) -> ImpactAnalysis:
        """Run impact analysis for the given changed file paths."""
        modified_components: list[ImpactedComponent] = []
        all_callers: set[str] = set()
        all_copybook_users: set[str] = set()
        all_db2: dict[tuple[str, str], dict] = {}  # (table, op) → row

        for file_path in changed_files:
            p = Path(file_path)
            name = p.stem.upper()
            ext = p.suffix

            if ext in _COBOL_EXTENSIONS:
                db2 = self._index.db2_tables(name)
                comp = ImpactedComponent(
                    name=name,
                    component_type="program",
                    impact_reason="directly_modified",
                    db2_tables=db2,
                )
                modified_components.append(comp)
                for t in db2:
                    all_db2[(t["table"], t["op"])] = t
                # Collect transitive callers
                callers = self._index.who_calls(name)
                all_callers.update(callers)

            elif ext in _COPYBOOK_EXTENSIONS:
                includers = self._index.who_includes(name)
                comp = ImpactedComponent(
                    name=name,
                    component_type="copybook",
                    impact_reason="directly_modified",
                )
                modified_components.append(comp)
                all_copybook_users.update(includers)
                # For each includer, collect their callers too
                for includer in includers:
                    callers = self._index.who_calls(includer)
                    all_callers.update(callers)
                    db2 = self._index.db2_tables(includer)
                    for t in db2:
                        all_db2[(t["table"], t["op"])] = t

        # Remove directly modified programs from callers list
        modified_names = {c.name for c in modified_components}
        all_callers -= modified_names
        all_copybook_users -= modified_names

        # Detect out-of-scope projects
        out_of_scope: set[str] = set()
        for caller in all_callers:
            proj = self._program_to_project.get(caller)
            if proj:
                out_of_scope.add(proj)

        # Compute risk level
        db2_list = list(all_db2.values())
        has_writes = any(t["op"] in ("INSERT", "UPDATE", "DELETE") for t in db2_list)
        n_callers = len(all_callers) + len(all_copybook_users)

        if has_writes or n_callers > 5:
            risk = "high"
            reason = (
                "DB2 write operations" if has_writes
                else f"{n_callers} transitive callers"
            )
        elif db2_list or 2 <= n_callers <= 5:
            risk = "medium"
            reason = "DB2 read access" if db2_list else f"{n_callers} callers"
        else:
            risk = "low"
            reason = "No DB2 access and ≤ 1 caller"

        return ImpactAnalysis(
            change_ref=change_ref,
            modified_components=modified_components,
            affected_callers=sorted(all_callers),
            affected_copybook_users=sorted(all_copybook_users),
            db2_tables_touched=db2_list,
            out_of_scope_projects=sorted(out_of_scope),
            risk_level=risk,
            risk_reason=reason,
        )
