from __future__ import annotations

import json

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor
from ._utils import parse_json

_PLAN_SYSTEM = """\
You are a senior software developer.
Given a development task and the project architecture, list ALL files you need
to create or modify to complete this task — no content yet.

Output ONLY a valid JSON array:
[
  {"file_path": "src/models.py", "description": "SQLModel entity classes"},
  ...
]

Rules:
- List ONLY files strictly required for this task
- Paths relative to the project root
- No test files
"""

_FILE_SYSTEM = """\
You are a senior software developer.
Generate the COMPLETE content for exactly ONE file.

Output ONLY a valid JSON object:
{
  "file_path": "src/models.py",
  "content": "...complete file content..."
}

Rules:
- Full implementation — no TODOs, no placeholders
- Clean, idiomatic, production-quality code
- Respect the tech stack from the architecture
"""


class CodeGenerator(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "code_generator",
        description = "Implements the next pending task from the task graph.",
        needs       = frozenset([
            ConditionId("task_graph_exists"),
            ConditionId("architecture_exists"),
        ]),
        produces    = frozenset([ConditionId("implementation_exists")]),
        consumes    = frozenset([ArtifactId("task_graph"), ArtifactId("architecture")]),
        emits       = frozenset(["source"]),
        cost        = 2.0,
    )

    def _run(self, store, goal) -> CapabilityResult:
        import copy
        tg_artifact = store.read(ArtifactId("task_graph"))
        if not tg_artifact:
            return CapabilityResult(errors=["task_graph artifact not found"])

        task_graph = dict(tg_artifact.content)
        tasks      = copy.deepcopy(task_graph.get("tasks", []))
        task       = _next_pending(tasks)

        if task is None:
            return CapabilityResult(errors=["no pending tasks in task_graph"])

        arch      = store.read(ArtifactId("architecture"))
        arch_text = arch.content if arch else ""

        context = (
            f"Task:\n{json.dumps(task, indent=2)}\n\n"
            f"Architecture:\n{arch_text}\n\n"
            f"Goal: {goal.description}"
        )

        # Phase 1 — plan: list files to create
        raw_plan  = self._call(_PLAN_SYSTEM, context)
        file_specs = _safe_parse_list(raw_plan)

        # Phase 2 — generate each file
        created: list[Artifact] = []
        for spec in file_specs:
            file_ctx = f"{context}\n\nFile to generate:\n{json.dumps(spec, indent=2)}"
            raw_file = self._call(_FILE_SYSTEM, file_ctx)
            file_data = _safe_parse_obj(raw_file)
            if file_data.get("content") and file_data.get("file_path"):
                created.append(Artifact(
                    id      = ArtifactId(f"src/{file_data['file_path']}"),
                    kind    = ArtifactKind.SOURCE,
                    content = file_data["content"],
                    metadata= {"file_path": file_data["file_path"], "task_id": task["id"]},
                ))

        # Update task status → done
        for t in tasks:
            if t["id"] == task["id"]:
                t["status"] = "done"
                break
        updated_tg = Artifact(
            id      = ArtifactId("task_graph"),
            kind    = ArtifactKind.TASK_GRAPH,
            content = {"tasks": tasks},
        )

        return CapabilityResult(
            delta=ArtifactDelta(
                created  = tuple(created),
                modified = (updated_tg,),
            )
        )


def _next_pending(tasks: list[dict]) -> dict | None:
    done_ids = {t["id"] for t in tasks if t.get("status") == "done"}
    for task in tasks:
        if task.get("status") != "pending":
            continue
        if all(dep in done_ids for dep in task.get("depends_on", [])):
            return task
    return None


def _safe_parse_list(raw: str) -> list[dict]:
    try:
        result = parse_json(raw)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _safe_parse_obj(raw: str) -> dict:
    try:
        result = parse_json(raw)
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and result:
            return result[0]
        return {}
    except Exception:
        return {}
