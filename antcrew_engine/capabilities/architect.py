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
You are a software architect.
Given a goal description and optional constraints, produce a technical architecture document.

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

class Architect(BaseExecutor):
    """Produces architecture from the goal directly — no SpecExtractor needed.

    Writes a stub requirements artifact alongside architecture so the
    requirements_exists condition is satisfied without a separate LLM call.
    """

    descriptor = CapabilityDescriptor(
        name        = "architect",
        description = "Produces a technical architecture document from the goal description.",
        needs       = frozenset(),
        produces    = frozenset([
            ConditionId("requirements_exists"),
            ConditionId("architecture_exists"),
        ]),
        emits       = frozenset(["architecture", "requirements"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        constraints_lines = []
        if goal.constraints.tech_stack:
            constraints_lines.append(f"Tech stack: {', '.join(goal.constraints.tech_stack)}")
        if goal.constraints.excluded:
            constraints_lines.append(f"Excluded: {', '.join(goal.constraints.excluded)}")
        for key, value in goal.constraints.custom.items():
            constraints_lines.append(f"{key}: {value}")

        user = f"Goal: {goal.description}"
        if constraints_lines:
            user += "\n\nConstraints:\n" + "\n".join(constraints_lines)

        # Inject human review feedback from a previous HITL rejection
        feedback_art = store.read(ArtifactId("architecture_feedback"))
        if feedback_art and isinstance(feedback_art.content, dict):
            feedback_text = feedback_art.content.get("feedback", "").strip()
            if feedback_text:
                user += f"\n\nHuman review feedback on the previous architecture:\n{feedback_text}"

        architecture = self._call(_SYSTEM, user)

        # Stub requirements artifact so requirements_exists validator passes
        req_body = f"# Requirements\n\n## Objective\n{goal.description}"
        if constraints_lines:
            req_body += "\n\n## Constraints\n" + "\n".join(constraints_lines)

        return CapabilityResult(delta=ArtifactDelta(created=(
            Artifact(
                id      = ArtifactId("requirements"),
                kind    = ArtifactKind.REQUIREMENTS,
                content = req_body,
            ),
            Artifact(
                id      = ArtifactId("architecture"),
                kind    = ArtifactKind.ARCHITECTURE,
                content = architecture,
            ),
        )))
