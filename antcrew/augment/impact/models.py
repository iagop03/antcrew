"""Data models for impact analysis results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ImpactedComponent:
    """One program or copybook identified as directly or transitively affected."""

    name: str
    component_type: Literal["program", "copybook"] = "program"
    impact_reason: str = ""     # "directly_modified" | "calls_modified" | "includes_modified"
    db2_tables: list[dict] = field(default_factory=list)   # [{"table": ..., "op": ...}]


@dataclass
class ImpactAnalysis:
    """Complete impact analysis for a set of VCS changes.

    Produced by :class:`~antcrew.augment.impact.analyzer.ImpactAnalyzer` and
    consumed by :class:`~antcrew.packager.change_packager.ChangePackager`.
    """

    change_ref: str
    modified_components: list[ImpactedComponent] = field(default_factory=list)
    affected_callers: list[str] = field(default_factory=list)    # transitive callers
    affected_copybook_users: list[str] = field(default_factory=list)
    db2_tables_touched: list[dict] = field(default_factory=list)  # aggregate across all programs
    out_of_scope_projects: list[str] = field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    risk_reason: str = ""

    def as_markdown(self) -> str:
        lines = [f"## Impact Analysis — {self.change_ref}", ""]
        lines.append(f"**Risk:** {self.risk_level.upper()} — {self.risk_reason}")
        lines.append("")
        if self.modified_components:
            lines.append("### Modified components")
            for c in self.modified_components:
                lines.append(f"- `{c.name}` ({c.component_type})")
        if self.affected_callers:
            lines.append("\n### Affected callers (transitive)")
            for c in self.affected_callers:
                lines.append(f"- `{c}`")
        if self.db2_tables_touched:
            lines.append("\n### DB2 tables touched")
            for t in self.db2_tables_touched:
                lines.append(f"- `{t['table']}` ({t['op']})")
        return "\n".join(lines)
