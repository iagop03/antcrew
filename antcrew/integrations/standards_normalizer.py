"""Normalize generated COBOL to match company naming and formatting standards."""
from __future__ import annotations

import re
from typing import Dict, Optional


class COBOLNormalizer:
    """Reformat generated COBOL so it conforms to company standards.

    Applies four transformations in sequence:
    1. Rename variables to use the correct prefixes (WS-, WC-, …)
    2. Rename paragraphs to UPPERCASE-WITH-DASHES
    3. Reorganize sections (stub — complex reordering deferred)
    4. Fix indentation and formatting

    Usage::

        from antcrew.integrations.standards_normalizer import COBOLNormalizer

        normalizer = COBOLNormalizer()
        clean_cobol = normalizer.normalize(generated_cobol)

        # Or with custom standards:
        normalizer = COBOLNormalizer(standards={
            "var_prefixes": {"working_storage": "WS-", "constants": "WC-"},
            "paragraph_pattern": "{ACTION}-{OBJECT}",
            "max_nesting": 2,
        })
    """

    def __init__(self, standards: Optional[Dict] = None) -> None:
        self.standards = standards or self._default_standards()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(self, cobol_code: str) -> str:
        """Apply all normalization passes and return the cleaned COBOL."""
        code = cobol_code
        code = self._rename_variables(code)
        code = self._rename_paragraphs(code)
        code = self._reorganize_sections(code)
        code = self._fix_formatting(code)
        return code

    # ------------------------------------------------------------------
    # Pass 1 — variable renaming
    # ------------------------------------------------------------------

    def _rename_variables(self, code: str) -> str:
        """Rename 01-level variables to use the correct prefix.

        Converts camelCase or lowercase names to UPPERCASE-WITH-DASHES and
        prepends WS- if no recognized prefix is already present.

        Example::
            01 orderAmount PIC 9(7)V99.
            → 01 WS-ORDER-AMOUNT PIC 9(7)V99.
        """
        prefixes = self.standards.get("var_prefixes", {})
        ws = prefixes.get("working_storage", "WS").rstrip("-")
        wc = prefixes.get("constants", "WC").rstrip("-")
        lk = prefixes.get("linkage", "LK").rstrip("-")
        fd = prefixes.get("file", "FD").rstrip("-")
        known = {ws, wc, lk, fd}

        var_re = re.compile(r"(01\s+)([a-zA-Z]\w*)(\s+(?:PIC|PICTURE))", re.IGNORECASE)

        def _transform(match: re.Match) -> str:
            level_ws = match.group(1)
            var_name = match.group(2)
            pic_kw = match.group(3)

            upper_dashed = _camel_to_upper_dashed(var_name)

            # Keep existing recognized prefix
            for pfx in known:
                if upper_dashed.startswith(pfx + "-"):
                    return f"{level_ws}{upper_dashed}{pic_kw}"

            # Default to working-storage prefix
            return f"{level_ws}{ws}-{upper_dashed}{pic_kw}"

        return var_re.sub(_transform, code)

    # ------------------------------------------------------------------
    # Pass 2 — paragraph renaming
    # ------------------------------------------------------------------

    def _rename_paragraphs(self, code: str) -> str:
        """Rename paragraph labels to UPPERCASE-WITH-DASHES.

        Matches lines that are a bare identifier followed by a period —
        the standard COBOL paragraph-definition form.

        Example::
            processOrder.
            → PROCESS-ORDER.
        """
        para_re = re.compile(r"^([a-zA-Z]\w*)\.\s*$", re.MULTILINE)

        def _transform(match: re.Match) -> str:
            return _camel_to_upper_dashed(match.group(1)) + "."

        return para_re.sub(_transform, code)

    # ------------------------------------------------------------------
    # Pass 3 — section reorganization (stub)
    # ------------------------------------------------------------------

    def _reorganize_sections(self, code: str) -> str:
        """Move all 01-level declarations to the top of WORKING-STORAGE.

        Full reordering requires an AST pass; this stub returns the code
        unchanged and is here as an extension point for Phase 3.
        """
        return code

    # ------------------------------------------------------------------
    # Pass 4 — formatting
    # ------------------------------------------------------------------

    def _fix_formatting(self, code: str) -> str:
        """Normalize indentation to standard COBOL column layout.

        Rules applied:
        - Division and section headers → column 8 (no leading spaces)
        - 01-level data items → column 8 (7 spaces)
        - 05/10-level data items → column 12 (11 spaces)
        - Paragraph labels → column 8 (no leading spaces)
        - Statements inside paragraphs → column 12 (11 spaces)
        - Trailing whitespace stripped from every line
        """
        lines = code.splitlines()
        result: list[str] = []

        _DIVISION_RE = re.compile(r"^\s*\w.*\s+DIVISION\b", re.IGNORECASE)
        _SECTION_RE = re.compile(r"^\s*\w.*\s+SECTION\b", re.IGNORECASE)
        _LEVEL_01_RE = re.compile(r"^\s*01\s+", re.IGNORECASE)
        _LEVEL_05_RE = re.compile(r"^\s*0[5-9]\s+|^\s*[1-9][0-9]\s+", re.IGNORECASE)
        _PARA_RE = re.compile(r"^([A-Z][A-Z0-9-]{0,28})\.\s*$")

        in_procedure = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append("")
                continue

            if "PROCEDURE DIVISION" in stripped.upper():
                in_procedure = True
                result.append(stripped)
                continue

            if _DIVISION_RE.match(stripped) or _SECTION_RE.match(stripped):
                result.append(stripped)
                continue

            if _LEVEL_01_RE.match(stripped):
                result.append("       " + stripped)
                continue

            if _LEVEL_05_RE.match(stripped):
                result.append("           " + stripped)
                continue

            if in_procedure and _PARA_RE.match(stripped):
                result.append(stripped)
                continue

            if in_procedure:
                result.append("           " + stripped)
                continue

            result.append(stripped)

        return "\n".join(result)

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _default_standards(self) -> Dict:
        return {
            "var_prefixes": {
                "working_storage": "WS",
                "constants": "WC",
                "linkage": "LK",
                "file": "FD",
            },
            "paragraph_pattern": "{ACTION}-{OBJECT}",
            "max_nesting": 2,
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _camel_to_upper_dashed(name: str) -> str:
    """Convert camelCase or PascalCase to UPPERCASE-WITH-DASHES.

    Examples::
        orderAmount   → ORDER-AMOUNT
        OrderProcessor → ORDER-PROCESSOR
        WS_ORDER      → WS-ORDER
        ALREADY-FINE  → ALREADY-FINE
    """
    # Replace underscores with dashes first
    s = name.replace("_", "-")
    # Insert dash before uppercase letters that follow lowercase
    s = re.sub(r"([a-z])([A-Z])", r"\1-\2", s)
    return s.upper()
