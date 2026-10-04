"""ChangePackager — produces the full change package for a Release.

Outputs:
  1. Excel workbook (openpyxl) — one row per CR, configurable columns
  2. Natural-language summary per CR (Markdown)
  3. Aggregated impact section
  4. Test evidence section (from TraceLog)
  5. Approval email drafts — one per required approver

Excel columns are configurable via ``excel_columns`` in the approvers/packager
YAML config so each client's ServiceNow format can be matched exactly.

Install: ``pip install antcrew[release]`` (adds openpyxl).

Usage::

    from antcrew.packager import ChangePackager
    from antcrew.models.release import Release
    from antcrew.packager.approvers import ApproversConfig

    packager = ChangePackager(
        release,
        approvers_config=ApproversConfig.from_yaml("approvers.yaml"),
        trace_log=tlog,
    )
    result = packager.build(output_dir="./pkg")
    print(result.excel_path)
    print(result.summary_md)
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from antcrew.augment.impact.models import ImpactAnalysis
    from antcrew.models.release import Release
    from antcrew.packager.approvers import ApproversConfig
    from antcrew.trace import TraceLog

_DEFAULT_EXCEL_COLUMNS = [
    "change_ref",
    "summary",
    "risk_level",
    "modified_components",
    "db2_tables",
    "test_runs",
    "approvers",
    "target_date",
]


@dataclass
class PackageResult:
    """Result of ChangePackager.build()."""

    excel_path: Optional[str]
    summary_md: str
    email_drafts: list[dict] = field(default_factory=list)   # [{"to": ..., "subject": ..., "body": ...}]
    output_dir: str = ""


class ChangePackager:
    """Produces a complete change package for a release."""

    def __init__(
        self,
        release: "Release",
        *,
        approvers_config: "Optional[ApproversConfig]" = None,
        trace_log: "Optional[TraceLog]" = None,
        impact_analyses: Optional[dict[str, "ImpactAnalysis"]] = None,   # change_ref → analysis
        excel_columns: Optional[list[str]] = None,
    ) -> None:
        self._release = release
        self._approvers = approvers_config
        self._trace = trace_log
        self._impact = impact_analyses or {}
        self._excel_cols = excel_columns or _DEFAULT_EXCEL_COLUMNS

    # ------------------------------------------------------------------
    # Main build
    # ------------------------------------------------------------------

    def build(self, output_dir: str | Path = ".") -> PackageResult:
        """Build the full change package and write files to *output_dir*."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        rows = self._build_rows()
        summary_md = self._build_summary_md(rows)
        email_drafts = self._build_email_drafts(rows)
        excel_path: Optional[str] = None

        try:
            excel_path = self._write_excel(rows, out)
        except ImportError:
            pass  # openpyxl not installed — skip Excel, the rest still works

        # Write Markdown summary
        md_file = out / f"{self._release.id}_summary.md"
        md_file.write_text(summary_md, encoding="utf-8")

        # Write email drafts as JSON
        if email_drafts:
            drafts_file = out / f"{self._release.id}_email_drafts.json"
            drafts_file.write_text(
                _json.dumps(email_drafts, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        return PackageResult(
            excel_path=excel_path,
            summary_md=summary_md,
            email_drafts=email_drafts,
            output_dir=str(out),
        )

    # ------------------------------------------------------------------
    # Row building
    # ------------------------------------------------------------------

    def _build_rows(self) -> list[dict]:
        rows = []
        for item in self._release.items:
            ref = item.change_ref
            impact = self._impact.get(ref)
            test_runs = self._get_test_runs(item.run_ids)
            row = {
                "change_ref": ref,
                "summary": item.summary or ref,
                "risk_level": (impact.risk_level if impact else item.impact_risk or "unknown"),
                "risk_reason": (impact.risk_reason if impact else ""),
                "modified_components": (
                    ", ".join(c.name for c in impact.modified_components) if impact else ""
                ),
                "db2_tables": (
                    ", ".join(f"{t['table']} ({t['op']})" for t in impact.db2_tables_touched)
                    if impact else ""
                ),
                "affected_callers": (
                    ", ".join(impact.affected_callers) if impact else ""
                ),
                "test_runs": "; ".join(test_runs),
                "origin": item.origin,
                "approvers": self._required_approvers_str(item),
                "target_date": self._release.target_date or "",
            }
            rows.append(row)
        return rows

    def _get_test_runs(self, run_ids: list[str]) -> list[str]:
        if not self._trace or not run_ids:
            return []
        summaries = []
        for run_id in run_ids:
            run = self._trace.get_run(run_id)
            if run:
                status = run.get("status", "?")
                cost = run.get("cost_usd") or 0.0
                summaries.append(f"{run_id[:8]}… ({status}, ${cost:.4f})")
        return summaries

    def _required_approvers_str(self, item) -> str:
        if not self._approvers:
            return ""
        risk = item.impact_risk or "low"
        required = self._approvers.required_for(risk)
        return ", ".join(a.role for a in required)

    # ------------------------------------------------------------------
    # Markdown summary
    # ------------------------------------------------------------------

    def _build_summary_md(self, rows: list[dict]) -> str:
        lines = [
            f"# Change Package — {self._release.name}",
            f"**Release ID:** {self._release.id}  ",
            f"**State:** {self._release.state}  ",
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ",
        ]
        if self._release.target_date:
            lines.append(f"**Target date:** {self._release.target_date}  ")
        lines.append("")

        # Summary table
        lines += ["## Change Summary", ""]
        lines.append("| CR | Summary | Risk | Modified | DB2 Tables |")
        lines.append("|---|---|---|---|---|")
        for r in rows:
            lines.append(
                f"| {r['change_ref']} | {r['summary'][:60]} | {r['risk_level']} "
                f"| {r['modified_components'][:40]} | {r['db2_tables'][:40]} |"
            )

        lines.append("")

        # Per-CR detail
        lines.append("## CR Detail")
        for r in rows:
            lines += [
                "", f"### {r['change_ref']}",
                f"**Summary:** {r['summary']}  ",
                f"**Risk:** {r['risk_level']} — {r['risk_reason']}  ",
            ]
            if r.get("modified_components"):
                lines.append(f"**Modified components:** {r['modified_components']}  ")
            if r.get("affected_callers"):
                lines.append(f"**Affected callers:** {r['affected_callers']}  ")
            if r.get("db2_tables"):
                lines.append(f"**DB2 tables:** {r['db2_tables']}  ")
            if r.get("test_runs"):
                lines.append(f"**Test evidence:** {r['test_runs']}  ")
            if r.get("approvers"):
                lines.append(f"**Required approvers:** {r['approvers']}  ")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Approval email drafts
    # ------------------------------------------------------------------

    def _build_email_drafts(self, rows: list[dict]) -> list[dict]:
        if not self._approvers:
            return []
        emails = []
        for approver in self._approvers.all_approvers():
            relevant_rows = [
                r for r in rows
                if approver.role in (r.get("approvers") or "")
            ]
            if not relevant_rows:
                continue
            cr_list = "\n".join(
                f"  - {r['change_ref']}: {r['summary'][:80]} (risk: {r['risk_level']})"
                for r in relevant_rows
            )
            body = (
                f"Dear {approver.name or approver.role},\n\n"
                f"Please review and approve the following change-requests included in "
                f"release **{self._release.name}** (ID: {self._release.id}) "
                f"scheduled for {self._release.target_date or 'TBD'}:\n\n"
                f"{cr_list}\n\n"
                f"To approve, reply to this email or access the release portal.\n\n"
                f"Thank you,\nAntCrew Release Management\n"
            )
            emails.append({
                "to": approver.email or approver.role,
                "subject": f"[Approval Required] Release {self._release.name} — {len(relevant_rows)} CR(s)",
                "body": body,
                "approver_role": approver.role,
                "change_refs": [r["change_ref"] for r in relevant_rows],
            })
        return emails

    # ------------------------------------------------------------------
    # Excel export (openpyxl)
    # ------------------------------------------------------------------

    def _write_excel(self, rows: list[dict], out: Path) -> str:
        import openpyxl  # type: ignore[import]
        from openpyxl.styles import Font, PatternFill  # type: ignore[import]

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Change Package"

        # Header row
        header_fill = PatternFill("solid", fgColor="1F3864")
        header_font = Font(bold=True, color="FFFFFF")
        headers = [col.replace("_", " ").title() for col in self._excel_cols]
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font

        # Data rows
        for row_idx, row in enumerate(rows, start=2):
            risk = row.get("risk_level", "low")
            row_fill = PatternFill("solid", fgColor={
                "high":   "FFD7D7",
                "medium": "FFF2CC",
                "low":    "E2EFDA",
            }.get(risk, "FFFFFF"))
            for col_idx, col_key in enumerate(self._excel_cols, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=str(row.get(col_key, "")))
                cell.fill = row_fill

        # Auto-width (best-effort)
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

        xlsx_path = out / f"{self._release.id}_change_package.xlsx"
        wb.save(str(xlsx_path))
        return str(xlsx_path)
