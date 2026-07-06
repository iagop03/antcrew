from __future__ import annotations

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor

_SYSTEM = """\
You are a software architect.
Given a requirements document and optional constraints, produce a technical architecture document.

Output a markdown document with exactly these sections:

# Architecture

## System Overview
One paragraph — the system's purpose, its main components, and the key design decisions.

## Components
For each component, a subsection:
### <ComponentName>
- **Responsibility**: what it does
- **Exposes**: endpoints, events, or interfaces it provides
- **Consumes**: what it depends on from other components

## Data Models
Key entities, their fields, and relationships. Use a simple table or bullet list per entity.

## API Design
If the system has a public API: list endpoints grouped by resource.
Format: `METHOD /path — description`
Omit this section if not applicable.

## Directory Structure
Proposed file layout. Use an indented tree.

## Component Dependencies
A bullet list: `ComponentA → ComponentB (reason)`.
Keep it flat — one line per dependency.

Rules:
- Be specific: name actual files, classes, and libraries where relevant
- Respect the constraints given (tech stack, exclusions)
- Do not include requirements that were not in the input
- Keep each section focused — no filler text
"""

_REQUIREMENTS_MISSING = "# Requirements\n\n(No requirements artifact found in store.)"


class Architect(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "architect",
        description = "Produces a technical architecture document from requirements.",
        needs       = frozenset([ConditionId("requirements_exists")]),
        produces    = frozenset([ConditionId("architecture_exists")]),
        consumes    = frozenset([ArtifactId("requirements")]),
        emits       = frozenset(["architecture"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        req = store.read(ArtifactId("requirements"))
        requirements_text = req.content if req else _REQUIREMENTS_MISSING

        constraints_lines = []
        if goal.constraints.tech_stack:
            constraints_lines.append(f"Tech stack: {', '.join(goal.constraints.tech_stack)}")
        if goal.constraints.excluded:
            constraints_lines.append(f"Excluded: {', '.join(goal.constraints.excluded)}")
        for key, value in goal.constraints.custom.items():
            constraints_lines.append(f"{key}: {value}")

        user = f"Requirements:\n\n{requirements_text}"
        if constraints_lines:
            user += "\n\nConstraints:\n" + "\n".join(constraints_lines)

        content = self._call(_SYSTEM, user)

        artifact = Artifact(
            id      = ArtifactId("architecture"),
            kind    = ArtifactKind.ARCHITECTURE,
            content = content,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))
