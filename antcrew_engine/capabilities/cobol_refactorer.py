"""COBOLRefactorerExecutor: improve COBOL readability without changing behaviour.

Takes a COBOL source artifact (from the store or from workspace docs) and
applies readability improvements:
  - Rename cryptic data-names to meaningful names
  - Replace GOTO with structured PERFORM
  - Extract complex inline logic into named paragraphs
  - Add 88-level condition names for coded fields
  - Normalise whitespace and add section headers as comments

Behaviour is preserved: the LLM is explicitly instructed to keep all
PIC clauses, VALUE clauses, and computation logic identical.
"""
from __future__ import annotations

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
You are a COBOL refactoring expert. Your task is to improve the readability of the given COBOL
program WITHOUT changing its behaviour.

Allowed changes:
  1. Rename data items from cryptic codes (WS-X1, WS-A99) to descriptive names (WS-EMPLOYEE-ID, WS-GROSS-PAY)
  2. Replace any GOTO statements with equivalent PERFORM paragraph structures
  3. Extract long inline PROCEDURE DIVISION sequences (>20 lines) into named paragraphs
  4. Add 88-level condition names for fields that hold coded values (status codes, flags, type codes)
  5. Normalise indentation: level 01 at col 8, each sub-level indented 4 spaces
  6. Add one-line section comments (asterisk in col 7) before each logical group

NOT allowed:
  - Changing PIC clauses or VALUE clauses
  - Changing computation logic (COMPUTE, ADD, SUBTRACT, MULTIPLY, DIVIDE)
  - Changing CALL/COPY statements
  - Changing file layouts
  - Changing the IDENTIFICATION or ENVIRONMENT DIVISION

Return ONLY the refactored COBOL source code.
After the final STOP RUN / END PROGRAM, add a comment block listing every rename you applied
in format:  * OLD-NAME -> NEW-NAME
"""


class COBOLRefactorerExecutor(BaseExecutor):
    """Refactor existing COBOL for readability without behaviour changes."""

    descriptor = CapabilityDescriptor(
        name        = "cobol_refactorer",
        description = "Improves COBOL readability (renames, GOTO removal, paragraph extraction) without changing behaviour.",
        needs       = frozenset([ConditionId("cobol_generated")]),
        produces    = frozenset([ConditionId("cobol_refactored")]),
        emits       = frozenset(["cobol_source"]),
        cost        = 1.5,
    )

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        # Prefer cobol_source artifact from the store
        source = self._get_artifact(store, "cobol_source")

        if not source:
            # Fall back to first COBOL doc in workspace
            source = self._get_cobol_from_docs(goal.description)

        if not source:
            return CapabilityResult(errors=["No COBOL source found to refactor."])

        # Include COBOL analysis for rename suggestions if available
        analysis = self._get_artifact(store, "cobol_analysis")
        analysis_note = f"\n\nCOBOL analysis:\n{analysis[:1000]}" if analysis else ""

        user = f"Goal: {goal.description}{analysis_note}\n\n## COBOL source to refactor\n\n{source}"
        refactored = self._call(_SYSTEM, user)

        artifact = Artifact(
            id      = ArtifactId("cobol_source"),
            kind    = ArtifactKind.SOURCE,
            content = refactored,
        )
        return CapabilityResult(delta=ArtifactDelta(updated=(artifact,)))

    @staticmethod
    def _get_artifact(store: "ArtifactStore", artifact_id: str) -> str | None:
        try:
            art = store.get(ArtifactId(artifact_id))
            return art.content if art else None
        except Exception:
            return None

    def _get_cobol_from_docs(self, query: str) -> str | None:
        if self._documentation is None:
            return None
        try:
            from antcrew_engine.documentation import DocumentationReader
            reader = DocumentationReader(self._documentation)
            docs = [
                d for d in reader.list_docs()
                if d.doc_type in ("cobol", "cobol_source", "copybook")
                or (d.source_file or "").lower().endswith((".cbl", ".cob", ".cpy"))
            ]
            if not docs:
                return None
            return reader.read_with_header(docs[0].doc_id)
        except Exception:
            return None
