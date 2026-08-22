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

from ._utils import parse_json
from .base import BaseExecutor

_SYSTEM_TEMPLATE = """\
You are a software project manager.
Given a technical architecture document, decompose the implementation into
a prioritized list of development tasks.

Output ONLY a valid JSON array — no markdown fences, no prose.

Each task object must have exactly these fields:
{{
  "id": "task_001",
  "title": "short title (max 60 chars)",
  "description": "one paragraph — concrete and actionable",
  "files": ["src/path/file.py"],
  "depends_on": []
}}

Rules:
- id: zero-padded 3-digit sequential number ("task_001", "task_002", ...)
- Order tasks so dependencies come first (dependency ids must be lower)
- Each task is one logical unit: one module, one endpoint group, one model
- Include a setup task (pyproject.toml, config) first if relevant
- No test tasks — tests are handled separately
- Maximum {max_tasks} tasks total
"""

_ARCHITECTURE_MISSING = "No architecture document available."


class TaskPlanner(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "task_planner",
        description = "Decomposes architecture into an ordered task graph.",
        needs       = frozenset([ConditionId("architecture_exists")]),
        produces    = frozenset([ConditionId("task_graph_exists")]),
        emits       = frozenset(["task_graph"]),
        cost        = 1.0,
    )

    def __init__(self, *args, max_tasks: int = 12, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_tasks = max_tasks

    def _run(self, store, goal) -> CapabilityResult:
        arch = store.read(ArtifactId("architecture"))
        architecture_text = arch.content if arch else _ARCHITECTURE_MISSING

        system = (
            _SYSTEM_TEMPLATE.format(max_tasks=self._max_tasks)
            + f"\n\n## Project Architecture\n{architecture_text}"
        )

        user_parts = []
        if goal.constraints.tech_stack:
            user_parts.append(f"Tech stack: {', '.join(goal.constraints.tech_stack)}")

        # Inject HITL rejection feedback so the planner can revise the task graph
        feedback_art = store.read(ArtifactId("task_planner_feedback"))
        if feedback_art and isinstance(feedback_art.content, dict):
            feedback_text = feedback_art.content.get("feedback", "").strip()
            if feedback_text:
                user_parts.append(f"Human review feedback on the previous task graph:\n{feedback_text}")

        user = "\n\n".join(user_parts) if user_parts else "Generate the task graph from the architecture above."
        raw    = self._call_json(system, user)
        tasks  = _safe_parse_tasks(raw)

        content = {"tasks": [dict(t, status="pending") for t in tasks]}
        artifact = Artifact(
            id      = ArtifactId("task_graph"),
            kind    = ArtifactKind.TASK_GRAPH,
            content = content,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))


def _safe_parse_tasks(raw: str) -> list[dict]:
    try:
        result = parse_json(raw)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "tasks" in result:
            return result["tasks"]
        return []
    except Exception:
        return []
