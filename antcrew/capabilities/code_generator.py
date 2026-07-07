from __future__ import annotations

import json

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor
from ._utils import parse_json, head as _head

_SYSTEM = """\
You are a senior software developer implementing a development task.
Given a task specification, project architecture, and any already-implemented files,
produce ALL files needed to complete this task in a SINGLE response.

Output ONLY valid JSON — no markdown fences, no prose:
{
  "files": [
    {
      "file_path": "src/models.py",
      "content": "...complete file content..."
    }
  ]
}

Rules:
- Output COMPLETE file contents — no TODOs, no placeholders, never truncate
- Clean, idiomatic, production-quality code matching the tech stack in the architecture
- Include ONLY files strictly required for this task (no test files, no docs)
- Use import paths consistent with existing source files (see 'Existing source files' below)
- If the task has no files to create, return {"files": []}
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

        # Include already-implemented files so the LLM uses correct import paths.
        # Truncated to first 60 lines each — enough for imports and public API signatures
        # without sending entire implementations and blowing up the context window.
        existing_sources = store.list(ArtifactKind.SOURCE)
        existing_block = ""
        if existing_sources:
            existing_block = "\n\nExisting source files (first 60 lines — mirror import style):\n"
            existing_block += "\n\n".join(
                f"--- {art.metadata.get('file_path', str(art.id))} ---\n{_head(art.content)}"
                for art in existing_sources
            )

        user = (
            f"Goal: {goal.description}\n\n"
            f"Architecture:\n{arch_text}\n\n"
            f"Task to implement:\n{json.dumps(task, indent=2)}"
            + existing_block
        )

        raw      = self._call_json(_SYSTEM, user)
        parsed   = _safe_parse_response(raw)
        file_specs = parsed.get("files", []) if isinstance(parsed, dict) else []

        created: list[Artifact] = []
        for spec in file_specs:
            fp      = spec.get("file_path", "")
            content = spec.get("content", "")
            if not fp or not content:
                continue
            created.append(Artifact(
                id       = ArtifactId(f"src/{fp}"),
                kind     = ArtifactKind.SOURCE,
                content  = content,
                metadata = {"file_path": fp, "task_id": task["id"]},
            ))

        # Mark task done
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


def _safe_parse_response(raw: str) -> dict:
    try:
        result = parse_json(raw)
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"files": result}
        return {}
    except Exception:
        return {}


