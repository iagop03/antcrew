"""COBOLGeneratorExecutor: generate COBOL source from a functional specification.

Unlike JavaToCOBOLTool (which translates existing code), this capability
generates new COBOL programs from requirements documents:
  1. Reads workspace functional specs via DocumentationReader
  2. Incorporates elicitation_report and requirements artifacts if available
  3. Generates valid COBOL following ANS-85 (fixed-format or free-format)
  4. Returns a SOURCE artifact containing the generated COBOL

Output: a single COBOL program or a summary JSON when multiple programs
are needed (the generator produces one program at a time — run repeatedly
with different sub-goals for a full system).
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
You are a COBOL developer with expertise in IBM mainframe environments and ANS-85 COBOL.

You will receive:
  - A goal describing what the program must do
  - Relevant documentation (functional specs, data dictionaries, existing COBOL interfaces)
  - Optional requirements analysis identifying gaps and clarifications

Generate a complete, compilable COBOL program:

Technical rules:
  - Use fixed-format COBOL (columns 7-72, sequence numbers 1-6 optional)
  - Use IDENTIFICATION, ENVIRONMENT, DATA, and PROCEDURE DIVISIONS
  - WORKING-STORAGE SECTION for all intermediate variables (prefix WS-)
  - LINKAGE SECTION only if the program is a called sub-program
  - Name paragraphs using VERB-NOUN-QUALIFIER format (e.g. CALC-GROSS-PAY)
  - Use PIC clauses exactly (9(n) for numeric, X(n) for alphanumeric, S9(n)V99 for signed decimal)
  - End each sentence with a period; end paragraphs with STOP RUN or EXIT PARAGRAPH
  - Use 88-level condition names for all coded fields
  - No GOTO; use structured PERFORM with explicit paragraph names

Output format:
  1. A comment block at the top (cols 7-72): program purpose, author, date
  2. The full COBOL source code
  3. A trailing comment listing assumptions made

Return ONLY the COBOL source code. No explanatory text before or after.
"""


class COBOLGeneratorExecutor(BaseExecutor):
    """Generate COBOL source code from a functional specification."""

    descriptor = CapabilityDescriptor(
        name        = "cobol_generator",
        description = "Generates new COBOL source from functional specs and requirements documents.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("cobol_generated")]),
        emits       = frozenset(["cobol_source"]),
        cost        = 2.0,
    )

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        user_parts: list[str] = [f"Goal: {goal.description}"]

        # Pull relevant docs from workspace
        doc_ctx = self._doc_context(goal.description, max_chars=5000)
        if doc_ctx:
            user_parts.append(doc_ctx)

        # Incorporate elicitation report if available
        elicitation = self._get_artifact(store, "elicitation_report")
        if elicitation:
            user_parts.append(f"## Requirements analysis\n\n{elicitation[:2000]}")

        # Incorporate requirements if available
        requirements = self._get_artifact(store, "requirements")
        if requirements:
            user_parts.append(f"## Requirements\n\n{requirements[:2000]}")

        # Include COBOL analysis if available
        cobol_analysis = self._get_artifact(store, "cobol_analysis")
        if cobol_analysis:
            user_parts.append(f"## Existing COBOL context\n\n{cobol_analysis[:2000]}")

        user = "\n\n".join(user_parts)
        cobol_source = self._call(_SYSTEM, user)

        artifact = Artifact(
            id      = ArtifactId("cobol_source"),
            kind    = ArtifactKind.SOURCE,
            content = cobol_source,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))

    @staticmethod
    def _get_artifact(store: "ArtifactStore", artifact_id: str) -> str | None:
        try:
            art = store.read(ArtifactId(artifact_id))
            return art.content if art else None
        except Exception:
            return None
