"""COBOLSyntaxValidatorExecutor: compile-check COBOL using GnuCOBOL or static analysis.

Two-tier validation:
  1. GnuCOBOL (`cobc -syntax-only`) if installed — exact syntax check
  2. Static heuristics fallback — catches the most common structural errors
     without any external dependency

Returns a `cobol_validation` REQUIREMENTS artifact with structured results::

    {
      "valid": true/false,
      "method": "gnucobol" | "static",
      "errors": [{"line": 42, "message": "..."}],
      "warnings": [{"line": ..., "message": "..."}],
      "summary": "..."
    }
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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


# Static checks: (regex_pattern, error_message)
_STATIC_CHECKS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*STOP\s+RUN\s*\.\s*$", re.I | re.M),   ""),  # presence check: STOP RUN expected
    (re.compile(r"GOTO", re.I),                               "GOTO detected — use PERFORM instead"),
    (re.compile(r"NEXT\s+SENTENCE", re.I),                    "NEXT SENTENCE is considered harmful; use END-IF"),
]

_DIVISION_ORDER = ["IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE"]


class COBOLSyntaxValidatorExecutor(BaseExecutor):
    """Validate COBOL source code syntax using GnuCOBOL or static heuristics."""

    descriptor = CapabilityDescriptor(
        name        = "cobol_validator",
        description = "Validates COBOL syntax via GnuCOBOL compilation or static heuristics.",
        needs       = frozenset([ConditionId("cobol_generated")]),
        produces    = frozenset([ConditionId("cobol_validated")]),
        emits       = frozenset(["cobol_validation"]),
        cost        = 0.5,
    )

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        source = self._get_artifact(store, "cobol_source")
        if not source:
            return CapabilityResult(errors=["No cobol_source artifact to validate."])

        if shutil.which("cobc"):
            result = self._validate_gnucobol(source)
        else:
            result = self._validate_static(source)

        artifact = Artifact(
            id      = ArtifactId("cobol_validation"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = json.dumps(result, ensure_ascii=False, indent=2),
        )

        errors_found = not result["valid"]
        return CapabilityResult(
            delta=ArtifactDelta(created=(artifact,)),
            errors=[f"COBOL validation failed: {result['summary']}"] if errors_found else [],
        )

    # ------------------------------------------------------------------
    # GnuCOBOL path
    # ------------------------------------------------------------------

    def _validate_gnucobol(self, source: str) -> dict:
        with tempfile.NamedTemporaryFile(
            suffix=".cbl", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(source)
            tmp_path = tmp.name

        try:
            proc = subprocess.run(
                ["cobc", "-syntax-only", "-free", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            output = (proc.stdout + proc.stderr).strip()
            errors = self._parse_cobc_output(output)
            valid = proc.returncode == 0
        except subprocess.TimeoutExpired:
            return {"valid": False, "method": "gnucobol", "errors": [{"line": 0, "message": "compile timeout"}], "warnings": [], "summary": "Compilation timed out."}
        except Exception as exc:
            return {"valid": False, "method": "gnucobol", "errors": [{"line": 0, "message": str(exc)}], "warnings": [], "summary": str(exc)}
        finally:
            os.unlink(tmp_path)

        summary = f"GnuCOBOL: {'OK' if valid else f'{len(errors)} error(s)'}"
        return {"valid": valid, "method": "gnucobol", "errors": errors, "warnings": [], "summary": summary}

    @staticmethod
    def _parse_cobc_output(output: str) -> list[dict]:
        errors: list[dict] = []
        for line in output.splitlines():
            m = re.search(r":(\d+):\s*(?:error|E):\s*(.+)", line, re.I)
            if m:
                errors.append({"line": int(m.group(1)), "message": m.group(2).strip()})
        return errors

    # ------------------------------------------------------------------
    # Static heuristics path
    # ------------------------------------------------------------------

    def _validate_static(self, source: str) -> dict:
        errors: list[dict] = []
        warnings: list[dict] = []
        lines = source.splitlines()

        # Division presence and order
        found_divs: list[str] = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip().upper()
            for div in _DIVISION_ORDER:
                if f"{div} DIVISION" in stripped and div not in found_divs:
                    found_divs.append(div)

        for div in _DIVISION_ORDER[:1]:  # IDENTIFICATION is mandatory
            if div not in found_divs:
                errors.append({"line": 0, "message": f"Missing {div} DIVISION"})

        if found_divs != [d for d in _DIVISION_ORDER if d in found_divs]:
            errors.append({"line": 0, "message": f"DIVISION order is wrong: {found_divs}"})

        # STOP RUN presence
        if not re.search(r"STOP\s+RUN", source, re.I):
            warnings.append({"line": 0, "message": "No STOP RUN found — program may not terminate cleanly"})

        # Paragraph dots check (each paragraph label should end with a period)
        proc_section = False
        for i, line in enumerate(lines, 1):
            if "PROCEDURE DIVISION" in line.upper():
                proc_section = True
                continue
            if proc_section and re.match(r"^[A-Z][\w-]{2,}$", line.strip()):
                warnings.append({"line": i, "message": f"Paragraph '{line.strip()}' missing period"})

        # Style checks
        for check_re, msg in _STATIC_CHECKS:
            if msg and check_re.search(source):
                warnings.append({"line": 0, "message": msg})

        # Unmatched IF/END-IF
        if_count  = len(re.findall(r"\bIF\b", source, re.I))
        end_count = len(re.findall(r"\bEND-IF\b", source, re.I))
        if if_count != end_count:
            errors.append({"line": 0, "message": f"IF/END-IF mismatch: {if_count} IF vs {end_count} END-IF"})

        valid = len(errors) == 0
        summary = f"Static: {'OK' if valid else f'{len(errors)} error(s), {len(warnings)} warning(s)'}"
        return {"valid": valid, "method": "static", "errors": errors, "warnings": warnings, "summary": summary}

    @staticmethod
    def _get_artifact(store: "ArtifactStore", artifact_id: str) -> str | None:
        try:
            art = store.read(ArtifactId(artifact_id))
            return art.content if art else None
        except Exception:
            return None
