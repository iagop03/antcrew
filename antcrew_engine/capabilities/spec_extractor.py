from __future__ import annotations

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

_SYSTEM = """\
You are a software requirements analyst.
Given a goal description and optional constraints, write a structured requirements document.

Output a markdown document with exactly these sections:

# Requirements

## Objective
One paragraph — what this system must accomplish and for whom.

## Functional Requirements
Numbered list. Each item: one sentence, starts with "The system MUST/SHOULD/MAY".
Be specific and concrete. No vague statements like "the system should be fast".

## Non-Functional Requirements
Performance, security, scalability, maintainability. Same format as above.

## Constraints
Technical decisions already made (tech stack, excluded technologies, standards).
List only what was explicitly given — do not invent constraints.
Omit this section if no constraints were provided.

## Acceptance Criteria
Numbered list. One measurable check per functional requirement.
Each criterion must be independently verifiable.

Rules:
- Use RFC 2119 keywords: MUST (required), SHOULD (recommended), MAY (optional)
- Describe WHAT the system does, not HOW it is implemented
- Keep each item to one sentence
"""


def _format_constraints(constraints) -> str:
    parts = []
    if constraints.tech_stack:
        parts.append(f"Tech stack: {', '.join(constraints.tech_stack)}")
    if constraints.excluded:
        parts.append(f"Excluded: {', '.join(constraints.excluded)}")
    for key, value in constraints.custom.items():
        parts.append(f"{key}: {value}")
    return "\n".join(parts)


class SpecExtractor(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "spec_extractor",
        description = "Writes a structured requirements document from the goal and constraints.",
        needs       = frozenset(),
        produces    = frozenset([ConditionId("requirements_exists")]),
        emits       = frozenset(["requirements"]),
        cost        = 1.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        user = f"Goal: {goal.description}"
        constraints_text = _format_constraints(goal.constraints)
        if constraints_text:
            user += f"\n\nConstraints:\n{constraints_text}"

        content = self._call(_SYSTEM, user)

        artifact = Artifact(
            id      = ArtifactId("requirements"),
            kind    = ArtifactKind.REQUIREMENTS,
            content = content,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))
