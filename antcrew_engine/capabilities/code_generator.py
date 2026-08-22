from __future__ import annotations

import concurrent.futures as _cf
import copy
import json

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
    ConditionId,
)

from ._utils import head as _head
from ._utils import parse_json
from .base import BaseExecutor

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

_DEFAULT_PARALLEL_WORKERS = 5


class CodeGenerator(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name        = "code_generator",
        description = "Implements all pending tasks from the task graph (parallel when multiple are ready).",
        needs       = frozenset([
            ConditionId("task_graph_exists"),
            ConditionId("architecture_exists"),
        ]),
        produces    = frozenset([ConditionId("implementation_exists")]),
        emits       = frozenset(["source"]),
        cost        = 2.0,
    )

    def __init__(self, *args, parallel_workers: int = _DEFAULT_PARALLEL_WORKERS, **kwargs):
        super().__init__(*args, **kwargs)
        self._parallel_workers = parallel_workers

    def _run(self, store, goal) -> CapabilityResult:
        tg_artifact = store.read(ArtifactId("task_graph"))
        if not tg_artifact:
            return CapabilityResult(errors=["task_graph artifact not found"])

        task_graph = dict(tg_artifact.content)
        tasks      = copy.deepcopy(task_graph.get("tasks", []))

        done_ids = {t["id"] for t in tasks if t.get("status") == "done"}
        pending  = [
            t for t in tasks
            if t.get("status") == "pending"
            and all(dep in done_ids for dep in t.get("depends_on", []))
        ]

        if not pending:
            return CapabilityResult(errors=["no pending tasks in task_graph"])

        arch      = store.read(ArtifactId("architecture"))
        arch_text = arch.content if arch else ""

        # Include already-implemented files so the LLM uses correct import paths.
        # Truncated to first 60 lines each — enough for imports and public API signatures.
        existing_sources = store.list(ArtifactKind.SOURCE)
        existing_block = ""
        if existing_sources:
            existing_block = "\n\nExisting source files (first 60 lines — mirror import style):\n"
            existing_block += "\n\n".join(
                f"--- {art.metadata.get('file_path', str(art.id))} ---\n{_head(art.content)}"
                for art in existing_sources
            )

        if len(pending) == 1:
            created, completed_ids = self._implement_tasks(pending, arch_text, existing_block, goal)
        else:
            created, completed_ids = self._implement_tasks_parallel(
                pending, arch_text, existing_block, goal
            )

        for t in tasks:
            if t["id"] in completed_ids:
                t["status"] = "done"

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

    def _implement_task(self, task: dict, arch_text: str, existing_block: str, goal) -> tuple[list[Artifact], str | None]:
        """Call LLM to implement a single task. Returns (artifacts, task_id) on success."""
        system = f"{_SYSTEM}\n\n## Project Architecture\n{arch_text}"
        user = (
            f"Goal: {goal.description}\n\n"
            f"Task to implement:\n{json.dumps(task, indent=2)}"
            + existing_block
        )
        try:
            raw        = self._call_json(system, user)
            parsed     = _safe_parse_response(raw)
            file_specs = parsed.get("files", []) if isinstance(parsed, dict) else []
            artifacts  = [
                Artifact(
                    id       = ArtifactId(f"src/{spec['file_path']}"),
                    kind     = ArtifactKind.SOURCE,
                    content  = spec.get("content", ""),
                    metadata = {"file_path": spec["file_path"], "task_id": task["id"]},
                )
                for spec in file_specs
                if spec.get("file_path") and spec.get("content")
            ]
            return artifacts, task["id"]
        except Exception:
            return [], None

    def _implement_tasks(
        self,
        pending: list[dict],
        arch_text: str,
        existing_block: str,
        goal,
    ) -> tuple[list[Artifact], set[str]]:
        """Serial implementation — used when only one task is ready."""
        arts, tid = self._implement_task(pending[0], arch_text, existing_block, goal)
        return arts, {tid} if tid else set()

    def _implement_tasks_parallel(
        self,
        pending: list[dict],
        arch_text: str,
        existing_block: str,
        goal,
    ) -> tuple[list[Artifact], set[str]]:
        """Parallel implementation — runs all ready tasks concurrently.

        Each worker gets a shallow copy of this executor so that per-token
        streaming state (on_token callback) is not shared across threads.
        Streaming is disabled for workers to avoid LLM instance contention.

        Artifact ID collisions (two tasks generating the same file path) are
        resolved last-write-wins and logged as warnings on the result.
        """
        def _worker(task: dict) -> tuple[list[Artifact], str | None]:
            worker = copy.copy(self)
            worker._event_log = None  # disable streaming in worker threads
            return worker._implement_task(task, arch_text, existing_block, goal)

        workers = min(len(pending), self._parallel_workers)
        with _cf.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_worker, pending))

        all_artifacts = [art for arts, _ in results for art in arts]
        completed_ids = {tid for _, tid in results if tid}

        # Deduplicate by artifact ID — last-write-wins when two tasks generate
        # the same file path (e.g. both create a shared utils module).
        seen: dict[ArtifactId, Artifact] = {}
        for art in all_artifacts:
            seen[art.id] = art
        deduped = list(seen.values())

        return deduped, completed_ids


def _next_pending(tasks: list[dict]) -> dict | None:
    """Return the first pending task whose dependencies are all done."""
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
