"""COBOL source file parser — fixed-format (.cbl, .cob) and copybooks (.cpy, .copy)."""
from __future__ import annotations

import re
from pathlib import Path

from .base import BaseParser, ParsedDocument

# ── Regex patterns ────────────────────────────────────────────────────────────
_DIVISION_RE   = re.compile(r"^\s*(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION", re.I)
_PROGRAM_ID_RE = re.compile(r"PROGRAM-ID\s*\.\s*([\w-]+)", re.I)
_AUTHOR_RE     = re.compile(r"AUTHOR\s*\.\s*(.+)", re.I)
_DATE_RE       = re.compile(r"DATE-WRITTEN\s*\.\s*(.+)", re.I)
_SECTION_RE    = re.compile(r"^\s*(WORKING-STORAGE|FILE|LINKAGE|LOCAL-STORAGE|SCREEN)\s+SECTION", re.I)
_DATA_ITEM_RE  = re.compile(r"^\s*(\d{1,2})\s+([\w-]+)(.*)", re.I)
_PIC_RE        = re.compile(r"PIC(?:TURE)?(?:\s+IS)?\s+([X9A()\d\-+.,/$V/Z*BPSE]+)", re.I)
_PARA_RE       = re.compile(r"^([A-Z][\w-]{0,29})\.$", re.I)
_COPY_RE       = re.compile(r"\bCOPY\s+([\w-]+)", re.I)
_CALL_RE       = re.compile(r"\bCALL\s+['\"]?([\w-]+)['\"]?", re.I)
_PERFORM_RE    = re.compile(r"\bPERFORM\s+([\w-]+)", re.I)
_MOVE_RE       = re.compile(r"\bMOVE\s+(.+?)\s+TO\s+([\w-]+)", re.I)


def _strip_fixed_format(line: str) -> str:
    """Remove fixed-format columns: sequence (1-6), indicator (7), ident (73+)."""
    if len(line) >= 7:
        indicator = line[6]
        if indicator in ("*", "/"):
            return ""          # full-line comment / page eject
        if indicator == "-":
            return line[7:72]  # continuation
        return line[7:72]
    return line


def _is_comment(line: str, free_format: bool) -> bool:
    stripped = line.strip()
    if free_format:
        return stripped.startswith("*>") or stripped.startswith("//")
    return len(line) >= 7 and line[6] in ("*", "/")


def _detect_format(lines: list[str]) -> bool:
    """Return True if file looks like free-format COBOL."""
    for line in lines[:30]:
        # Free-format files often have content before col 7 that looks like code
        # Fixed-format has sequence numbers (digits) in cols 1-6
        if len(line) >= 6 and line[:6].strip().isdigit():
            return False
        if re.match(r"^(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE|WORKING-STORAGE)", line.strip(), re.I):
            return True
    return False


class CobolParser(BaseParser):
    """Parse COBOL source files into a structured document summary."""

    def parse(self, file_path: str) -> ParsedDocument:
        path = Path(file_path)
        is_copybook = path.suffix.lower() in (".cpy", ".copy")

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ParsedDocument(
                content=f"[COBOL file unreadable: {path.name}]",
                metadata={"parser": "cobol", "source": str(path)},
            )

        raw_lines = raw.splitlines()
        free_format = _detect_format(raw_lines)

        if free_format:
            code_lines = [
                ln for ln in raw_lines
                if not _is_comment(ln, free_format=True)
            ]
        else:
            code_lines = []
            for ln in raw_lines:
                stripped = _strip_fixed_format(ln)
                if stripped:
                    code_lines.append(stripped)

        content, metadata = self._extract(code_lines, is_copybook, path.name)
        metadata.update({"parser": "cobol", "source": str(path), "is_copybook": is_copybook})
        return ParsedDocument(content=content, metadata=metadata)

    # ── Main extractor ────────────────────────────────────────────────────────

    def _extract(
        self, lines: list[str], is_copybook: bool, filename: str
    ) -> tuple[str, dict]:
        program_id = ""
        author = ""
        date_written = ""
        current_division = "IDENTIFICATION" if not is_copybook else "DATA"
        current_section = ""

        data_items: list[dict] = []      # {level, name, pic, section}
        paragraphs: list[str] = []
        copybooks: list[str] = []
        called_programs: list[str] = []
        performed: list[str] = []

        full_text = "\n".join(lines)

        # Extract top-level metadata in one pass over the joined text
        if m := _PROGRAM_ID_RE.search(full_text):
            program_id = m.group(1).rstrip(".")
        if m := _AUTHOR_RE.search(full_text):
            author = m.group(1).strip().rstrip(".")
        if m := _DATE_RE.search(full_text):
            date_written = m.group(1).strip().rstrip(".")

        for copy_m in _COPY_RE.finditer(full_text):
            name = copy_m.group(1).rstrip(".")
            if name not in copybooks:
                copybooks.append(name)

        for call_m in _CALL_RE.finditer(full_text):
            name = call_m.group(1).rstrip(".")
            if name not in called_programs:
                called_programs.append(name)

        # Line-by-line pass for divisions, sections, data items, paragraphs
        in_procedure = False
        for line in lines:
            stripped = line.strip().upper()

            if m := _DIVISION_RE.match(line):
                current_division = m.group(1).upper()
                in_procedure = current_division == "PROCEDURE"
                current_section = ""
                continue

            if m := _SECTION_RE.match(line):
                current_section = m.group(1).upper()
                continue

            if current_division == "DATA":
                if m := _DATA_ITEM_RE.match(line):
                    level = int(m.group(1))
                    name = m.group(2).upper()
                    rest = m.group(3)
                    pic = ""
                    if pm := _PIC_RE.search(rest):
                        pic = pm.group(1).upper()
                    if name not in ("FILLER", "88"):
                        data_items.append({
                            "level": level,
                            "name": name,
                            "pic": pic,
                            "section": current_section,
                        })
                continue

            if in_procedure:
                # Paragraph: a label ending with a period, alone on a line
                candidate = stripped.rstrip(".")
                if (
                    re.match(r"^[A-Z][\w-]{0,29}$", candidate)
                    and stripped.endswith(".")
                    and len(stripped.split()) == 1
                ):
                    if candidate not in ("END-PROGRAM", "STOP", "EXIT"):
                        paragraphs.append(candidate)

        for perf_m in _PERFORM_RE.finditer(full_text):
            name = perf_m.group(1).upper()
            if name not in ("VARYING", "UNTIL", "TIMES", "WITH"):
                if name not in performed:
                    performed.append(name)

        # ── Build human-readable summary ──────────────────────────────────────
        parts: list[str] = []

        if is_copybook:
            parts.append(f"# COBOL Copybook: {filename}\n")
        else:
            header = f"# COBOL Program: {program_id or filename}"
            if author:
                header += f"\nAuthor: {author}"
            if date_written:
                header += f"\nDate written: {date_written}"
            parts.append(header + "\n")

        # Data items grouped by section
        ws_items = [d for d in data_items if d["section"] in ("WORKING-STORAGE", "")]
        file_items = [d for d in data_items if d["section"] == "FILE"]
        link_items = [d for d in data_items if d["section"] == "LINKAGE"]

        def _fmt_items(items: list[dict]) -> str:
            rows = []
            for d in items[:40]:  # cap to keep context manageable
                pic = f"  PIC {d['pic']}" if d['pic'] else ""
                rows.append(f"  {'  ' * max(0, d['level'] - 1)}{d['level']:02d} {d['name']}{pic}")
            if len(items) > 40:
                rows.append(f"  ... ({len(items) - 40} more items)")
            return "\n".join(rows)

        if ws_items:
            parts.append(f"## Working-Storage ({len(ws_items)} items)\n{_fmt_items(ws_items)}")
        if file_items:
            parts.append(f"## File Section ({len(file_items)} items)\n{_fmt_items(file_items)}")
        if link_items:
            parts.append(f"## Linkage Section ({len(link_items)} items)\n{_fmt_items(link_items)}")

        if paragraphs:
            parts.append(f"## Paragraphs ({len(paragraphs)})\n" + "\n".join(f"  - {p}" for p in paragraphs[:30]))

        if copybooks:
            parts.append("## COPY Statements\n" + "\n".join(f"  - {c}" for c in copybooks))

        if called_programs:
            parts.append("## CALL Statements\n" + "\n".join(f"  - {c}" for c in called_programs))

        content = "\n\n".join(parts)

        metadata: dict = {
            "program_id": program_id,
            "author": author,
            "date_written": date_written,
            "data_items_count": len(data_items),
            "paragraphs": paragraphs[:50],
            "copybooks": copybooks,
            "called_programs": called_programs,
            "working_storage_fields": [d["name"] for d in ws_items if d["level"] in (1, 5, 10)],
        }
        return content, metadata
