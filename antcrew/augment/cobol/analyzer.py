"""COBOLAnalyzer — parse a COBOL source file and extract augmentation targets."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DataItem:
    level: int
    name: str
    pic: str
    section: str  # WORKING-STORAGE | LINKAGE | FILE | ""


@dataclass
class ExternalCall:
    kind: str   # CALL | COPY | EXEC CICS | EXEC SQL
    target: str
    line: int = 0


@dataclass
class COBOLAnalysis:
    """Structured result of analysing a COBOL source file."""
    program_id: str
    source_file: str
    author: str = ""
    date_written: str = ""
    paragraphs: list[str] = field(default_factory=list)
    data_items: list[DataItem] = field(default_factory=list)
    external_calls: list[ExternalCall] = field(default_factory=list)
    linkage_fields: list[DataItem] = field(default_factory=list)
    working_storage_fields: list[DataItem] = field(default_factory=list)
    is_copybook: bool = False
    raw_divisions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def input_fields(self) -> list[DataItem]:
        """Linkage-section fields — typical COBOL API boundary."""
        return self.linkage_fields or self.working_storage_fields[:20]

    def summary(self) -> str:
        lines = [
            f"Program  : {self.program_id}",
            f"File     : {self.source_file}",
            f"Author   : {self.author}" if self.author else "",
            f"Data items: {len(self.data_items)} "
            f"({len(self.linkage_fields)} linkage, {len(self.working_storage_fields)} ws)",
            f"Paragraphs: {', '.join(self.paragraphs[:10])}{'...' if len(self.paragraphs) > 10 else ''}",
            f"Calls    : {', '.join(c.target for c in self.external_calls if c.kind == 'CALL')[:5]}",
        ]
        return "\n".join(ln for ln in lines if ln)


class COBOLAnalyzer:
    """Parse a COBOL source file and return a :class:`COBOLAnalysis`.

    Delegates the heavy lifting to the existing ``CobolParser`` in the engine's
    documentation module.  If the engine parser is unavailable it falls back to
    a lightweight regex pass.
    """

    def analyze(self, cobol_file: str) -> COBOLAnalysis:
        path = Path(cobol_file)
        is_copybook = path.suffix.lower() in (".cpy", ".copy")

        # Try the full engine parser first
        try:
            from antcrew_engine.documentation.parsers.cobol import CobolParser
            parsed = CobolParser().parse(cobol_file)
            meta = parsed.metadata
            analysis = COBOLAnalysis(
                program_id=meta.get("program_id", path.stem),
                source_file=cobol_file,
                author=meta.get("author", ""),
                date_written=meta.get("date_written", ""),
                paragraphs=meta.get("paragraphs", []),
                is_copybook=is_copybook,
            )
            # Reconstruct DataItem list from metadata
            for name in meta.get("working_storage_fields", []):
                analysis.working_storage_fields.append(
                    DataItem(level=1, name=name, pic="", section="WORKING-STORAGE")
                )
            for cpy in meta.get("copybooks", []):
                analysis.external_calls.append(ExternalCall(kind="COPY", target=cpy))
            for call in meta.get("called_programs", []):
                analysis.external_calls.append(ExternalCall(kind="CALL", target=call))
            return analysis
        except Exception:
            pass

        # Fallback: lightweight parse
        return self._fallback_analyze(path, is_copybook)

    def _fallback_analyze(self, path: Path, is_copybook: bool) -> COBOLAnalysis:
        import re

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return COBOLAnalysis(program_id=path.stem, source_file=str(path))

        prog_m = re.search(r"PROGRAM-ID\s*\.\s*([\w-]+)", text, re.I)
        program_id = prog_m.group(1).strip() if prog_m else path.stem

        paragraphs = re.findall(r"^([A-Z][\w-]{0,29})\.\s*$", text, re.I | re.M)
        calls = re.findall(r"\bCALL\s+['\"]?([\w-]+)['\"]?", text, re.I)
        copies = re.findall(r"\bCOPY\s+([\w-]+)", text, re.I)

        analysis = COBOLAnalysis(
            program_id=program_id,
            source_file=str(path),
            paragraphs=list(dict.fromkeys(paragraphs))[:50],
            is_copybook=is_copybook,
        )
        for c in calls:
            analysis.external_calls.append(ExternalCall(kind="CALL", target=c))
        for c in copies:
            analysis.external_calls.append(ExternalCall(kind="COPY", target=c))
        return analysis
