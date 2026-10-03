"""COBOLTestGeneratorExecutor: generate test cases and JCL for a COBOL program.

Given a cobol_source artifact and optional cobol_analysis, produces:
  - A test specification document (human-readable, in markdown)
  - A JCL skeleton for batch submission of each test case
  - Input data files (EBCDIC-safe fixed-format records)

Output: a `cobol_tests` REQUIREMENTS artifact containing the test spec as JSON,
and a `cobol_jcl` SOURCE artifact with the JCL skeleton.

Output JSON schema (cobol_tests)::

    {
      "program_id": "PAYROLL",
      "test_cases": [
        {
          "id": "TC-001",
          "description": "...",
          "category": "normal" | "boundary" | "error",
          "input": {"field_name": "value", ...},
          "expected_output": {"field_name": "value", ...},
          "expected_return_code": "00"
        }
      ]
    }
"""
from __future__ import annotations

import json
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


_SYSTEM_TESTS = """\
You are a COBOL QA engineer. Given a COBOL program and its analysis, design a comprehensive
test suite covering normal paths, boundary conditions, and error conditions.

Return a JSON object with this structure:
{
  "program_id": "<PROGRAM-ID from source>",
  "test_cases": [
    {
      "id": "TC-001",
      "description": "<one sentence>",
      "category": "normal" | "boundary" | "error",
      "input": {"<WS-field-name>": "<value>", ...},
      "expected_output": {"<WS-field-name>": "<value>", ...},
      "expected_return_code": "00"
    }
  ]
}

Rules:
- At least 5 test cases: 2 normal, 2 boundary (max values, min values, zero, spaces), 1 error
- Use field names as they appear in the WORKING-STORAGE SECTION
- Numeric values as strings with the correct number of digits
- Return ONLY the JSON. No markdown fences.
"""

_SYSTEM_JCL = """\
You are a z/OS JCL expert. Given a COBOL test specification, write a JCL skeleton
that runs each test case as a batch job step.

Rules:
- One JOB card at the top
- One EXEC PGM=<program_id> step per test case (or one parametrised PROC)
- DD statements for SYSIN (input), SYSOUT (output), SYSABEND
- Use symbolic parameters (&TESTID, &INPUT) where helpful
- Comment each step with the test case ID and description
- Return ONLY the JCL.
"""


class COBOLTestGeneratorExecutor(BaseExecutor):
    """Generate COBOL test cases and JCL from a cobol_source artifact."""

    descriptor = CapabilityDescriptor(
        name        = "cobol_test_generator",
        description = "Generates test cases and JCL skeleton for a COBOL program.",
        needs       = frozenset([ConditionId("cobol_generated")]),
        produces    = frozenset([ConditionId("cobol_tested")]),
        emits       = frozenset(["cobol_tests", "cobol_jcl"]),
        cost        = 1.5,
    )

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        source = self._get_artifact(store, "cobol_source")
        if not source:
            return CapabilityResult(errors=["No cobol_source artifact to generate tests from."])

        analysis = self._get_artifact(store, "cobol_analysis")
        analysis_note = f"\n\nCOBOL analysis:\n{analysis[:1500]}" if analysis else ""

        # Step 1: generate test specification
        user_tests = (
            f"Goal: {goal.description}{analysis_note}\n\n"
            f"## COBOL source\n\n{source[:4000]}"
        )
        raw_tests = self._call(_SYSTEM_TESTS, user_tests)
        test_spec: dict = {}
        try:
            cleaned = raw_tests.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.splitlines()[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            test_spec = json.loads(cleaned)
        except json.JSONDecodeError:
            test_spec = {"raw": raw_tests, "parse_error": True}

        # Step 2: generate JCL
        user_jcl = (
            f"Goal: {goal.description}\n\n"
            f"## Test specification\n\n{json.dumps(test_spec, indent=2)[:3000]}"
        )
        jcl_source = self._call(_SYSTEM_JCL, user_jcl)

        tests_artifact = Artifact(
            id      = ArtifactId("cobol_tests"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = json.dumps(test_spec, ensure_ascii=False, indent=2),
        )
        jcl_artifact = Artifact(
            id      = ArtifactId("cobol_jcl"),
            kind    = ArtifactKind.SOURCE,
            content = jcl_source,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(tests_artifact, jcl_artifact)))

    @staticmethod
    def _get_artifact(store: "ArtifactStore", artifact_id: str) -> str | None:
        try:
            art = store.get(ArtifactId(artifact_id))
            return art.content if art else None
        except Exception:
            return None
