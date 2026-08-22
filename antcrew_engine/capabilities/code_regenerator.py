"""CodeRegenerator — resets a failing task to pending with failure context.

When BugFixer exhausts its retry budget this capability runs as last resort.
It does NOT generate code itself. Instead it:
  1. Calls the LLM for a "lessons learned" post-mortem on the failure
  2. Resets the implicated task(s) to "pending" with the lesson appended
  3. Deletes the broken source files for those tasks

CodeGenerator then picks up the reset task(s) with better context and
regenerates from scratch. BugFixer recovers its retry budget automatically
because CodeGenerator's run satisfies implementation_exists (newly non-empty
→ EngineLoop clears retry_counts for all capabilities).
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

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
You are a senior engineer doing a post-mortem on a failed implementation attempt.
Given: the project goal, architecture, the failing task description, and pytest output.

Write a concise "lessons learned" note — 2-4 sentences — that will be shown to the
engineer who reimplements this task from scratch.

Focus on:
- What was architecturally wrong with the previous approach
- Specific mistake: wrong module layout, incorrect abstraction, missed edge case, etc.
- What to do differently this time

Do NOT generate any code. Output only the plain-text note, no bullet points, no headers.
"""


class CodeRegenerator(BaseExecutor):
    """Resets failing tasks to 'pending' so CodeGenerator can retry with fresh context.

    Cost 4.5 > BugFixer 3.0 ensures this runs only when BugFixer is blocked by
    its retry_limit. total_limits={"code_regenerator": 2} caps regeneration cycles.
    """

    descriptor = CapabilityDescriptor(
        name        = "code_regenerator",
        description = "Resets a failing task to pending with failure context so CodeGenerator can retry from scratch.",
        needs       = frozenset([
            ConditionId("implementation_exists"),
            ConditionId("tests_exist"),
        ]),
        produces    = frozenset([ConditionId("tests_pass")]),
        emits       = frozenset(["task_graph"]),
        cost        = 4.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        test_report = store.read(ArtifactId("test_report"))
        if not test_report:
            return CapabilityResult(errors=["test_report not found — run TestRunner first"])

        report = test_report.content if isinstance(test_report.content, dict) else {}
        if report.get("passed"):
            return CapabilityResult(errors=["tests already pass — nothing to regenerate"])

        test_output = report.get("output", "")[-3_000:]

        tg_artifact = store.read(ArtifactId("task_graph"))
        if not tg_artifact:
            return CapabilityResult(errors=["task_graph not found"])

        tasks = copy.deepcopy(
            tg_artifact.content.get("tasks", [])
            if isinstance(tg_artifact.content, dict) else []
        )

        # --- identify which tasks produced the failing files ---
        sources = store.list(ArtifactKind.SOURCE)

        # file_path → task_id (written by CodeGenerator into artifact metadata)
        file_to_task: dict[str, str] = {
            art.metadata.get("file_path", ""): art.metadata.get("task_id", "")
            for art in sources
            if art.metadata.get("file_path") and art.metadata.get("task_id")
        }

        failing_files = _extract_failing_files(test_output)
        failing_task_ids: set[str] = set()
        for fp in failing_files:
            for art_fp, task_id in file_to_task.items():
                if art_fp in fp or fp.endswith(art_fp) or Path(art_fp).name in fp:
                    failing_task_ids.add(task_id)

        # Fallback: reset the last "done" task when traceback gives no clue
        if not failing_task_ids:
            done = [t for t in tasks if t.get("status") == "done"]
            if done:
                failing_task_ids.add(done[-1]["id"])

        if not failing_task_ids:
            return CapabilityResult(errors=["could not identify any task to reset"])

        # --- ask LLM for lessons learned ---
        arch      = store.read(ArtifactId("architecture"))
        arch_text = arch.content if arch else "No architecture document."

        failing_task_descs = "\n".join(
            f"- Task {t['id']}: {t.get('description', '(no description)')}"
            for t in tasks if t.get("id") in failing_task_ids
        )
        implicated_srcs = [
            f"--- {art.metadata.get('file_path', str(art.id))} ---\n"
            + (art.content[:800] if isinstance(art.content, str) else "")
            for art in sources
            if art.metadata.get("task_id") in failing_task_ids
        ]

        system = f"{_SYSTEM}\n\n## Project Architecture\n{arch_text}"
        user = (
            f"Goal: {goal.description}\n\n"
            f"Failed task(s):\n{failing_task_descs}\n\n"
            f"Failing test output:\n```\n{test_output}\n```"
            + (
                "\n\nImplicated source files (truncated):\n"
                + "\n\n".join(implicated_srcs[:3])
                if implicated_srcs else ""
            )
        )

        lessons = self._call(system, user).strip()

        # --- reset failing tasks to pending + append lessons ---
        reset_count = 0
        for task in tasks:
            if task.get("id") in failing_task_ids and task.get("status") == "done":
                task["status"] = "pending"
                orig_desc = task.get("description", "")
                task["description"] = (
                    f"{orig_desc}\n\n"
                    f"[Previous attempt failed. Lessons learned:\n{lessons}]"
                )
                reset_count += 1

        if reset_count == 0:
            return CapabilityResult(
                errors=["target tasks were not in 'done' status — nothing reset"]
            )

        # --- delete broken source + test files + stale test_report ---
        # Tests are deleted alongside sources because BugFixer may have changed
        # function signatures — the stale test artifacts would target the old API.
        # test_report deleted so TestRunner runs a full pass (not --lf) on fresh code.
        tests = store.list(ArtifactKind.TEST)
        deleted = (
            tuple(
                art.id for art in sources
                if art.metadata.get("task_id") in failing_task_ids
            )
            + tuple(
                art.id for art in tests
                if art.metadata.get("task_id") in failing_task_ids
            )
            + (ArtifactId("test_report"),)
        )

        updated_tg = Artifact(
            id      = ArtifactId("task_graph"),
            kind    = ArtifactKind.TASK_GRAPH,
            content = {"tasks": tasks},
        )

        return CapabilityResult(delta=ArtifactDelta(
            modified = (updated_tg,),
            deleted  = deleted,
        ))


def _extract_failing_files(test_output: str) -> set[str]:
    """Extract source file paths from a pytest traceback."""
    paths: set[str] = set()
    for m in re.finditer(r'File\s+"([^"]+\.py)"', test_output):
        paths.add(m.group(1))
    for m in re.finditer(r'^(\S[^\s:]+\.py):\d+:', test_output, re.MULTILINE):
        candidate = m.group(1)
        if "/" in candidate or "\\" in candidate:
            paths.add(candidate)
    return paths
