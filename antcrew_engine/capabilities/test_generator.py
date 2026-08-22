from __future__ import annotations

import os
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

from ._utils import head as _head
from .base import BaseExecutor

_SYSTEM = """\
You are a senior software developer writing pytest tests.
Given a source file (plus project architecture and a list of other files for import context),
write a comprehensive test file for it.

Output ONLY the raw Python test file content — no markdown fences, no explanation.

Rules:
- Use pytest (not unittest)
- Test the public interface: functions, classes, endpoints
- Include at least one happy-path test and one edge case per public function
- Use fixtures for shared setup
- Import the module under test using its file_path relative to the project root
- Do not call external services — mock them with pytest-mock or monkeypatch
"""

_SKIP_BASENAMES = frozenset(["__init__.py", "__main__.py"])


class TestGenerator(BaseExecutor):
    __test__ = False  # not a pytest test class
    descriptor = CapabilityDescriptor(
        name        = "test_generator",
        description = "Generates pytest test files for every source artifact.",
        needs       = frozenset([ConditionId("implementation_exists")]),
        produces    = frozenset([ConditionId("tests_exist")]),
        emits       = frozenset(["test"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        if not sources:
            return CapabilityResult(errors=["no source artifacts found in store"])

        arch = store.read(ArtifactId("architecture"))
        arch_text = arch.content if isinstance(arch, object) and arch else ""
        if arch and not isinstance(arch_text, str):
            arch_text = str(arch_text)

        testable = [
            s for s in sources
            if os.path.basename(s.metadata.get("file_path", "")) not in _SKIP_BASENAMES
            and isinstance(s.content, str)
            and s.content.strip()
        ]
        if not testable:
            return CapabilityResult(errors=["no testable source files found (all are __init__.py or empty)"])

        all_paths = [s.metadata.get("file_path", str(s.id)) for s in testable]

        created: list[Artifact] = []
        for src in testable:
            file_path    = src.metadata.get("file_path", str(src.id))
            other_paths  = [p for p in all_paths if p != file_path]

            import_path = _to_import_path(file_path)
            system = (
                f"{_SYSTEM}\n\n## Project Architecture\n{_head(arch_text, 100)}"
                if arch_text else _SYSTEM
            )
            user = (
                f"Goal: {goal.description}\n\n"
                + (
                    "Other source files in this project (for import context):\n"
                    + "\n".join(other_paths) + "\n\n"
                    if other_paths else ""
                )
                + f"File to test: {file_path}\n"
                + f"Import this module as: `from {import_path} import ...` "
                + "(PYTHONPATH is set to the project root)\n\n"
                + _head(src.content, 150)
            )
            test_content = self._call(system, user)

            test_path = _to_test_path(file_path)
            created.append(Artifact(
                id       = ArtifactId(f"test/{test_path}"),
                kind     = ArtifactKind.TEST,
                content  = test_content,
                metadata = {
                    "file_path": test_path,
                    "source_id": str(src.id),
                    "task_id":   src.metadata.get("task_id", ""),
                },
            ))

        return CapabilityResult(delta=ArtifactDelta(created=tuple(created)))


def _to_import_path(file_path: str) -> str:
    """Convert a file path to its Python import path.

    src/api/models.py → src.api.models
    app/main.py       → app.main
    main.py           → main
    """
    return file_path.replace("\\", "/").removesuffix(".py").replace("/", ".")


def _to_test_path(file_path: str) -> str:
    """Convert source path to test path, preserving sub-directory structure.

    src/models.py          → tests/test_models.py
    src/api/models.py      → tests/api/test_models.py
    app/routers/users.py   → tests/routers/test_users.py
    main.py                → tests/test_main.py
    """
    p     = Path(file_path.replace("\\", "/"))
    parts = p.parts
    if len(parts) == 1:
        return f"tests/test_{p.stem}{p.suffix}"
    # Strip the first component (src / app / lib / etc.) and keep deeper dirs
    sub_dirs = parts[1:-1]
    if sub_dirs:
        mid = Path(*sub_dirs)
        return str(Path("tests") / mid / f"test_{p.stem}{p.suffix}")
    return f"tests/test_{p.stem}{p.suffix}"
