from __future__ import annotations

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor

_SYSTEM = """\
You are a senior software developer writing pytest tests.
Given a source file, write a comprehensive test file for it.

Output ONLY the raw Python test file content — no markdown fences, no explanation.

Rules:
- Use pytest (not unittest)
- Test the public interface: functions, classes, endpoints
- Include at least one happy-path test and one edge case per public function
- Use fixtures for shared setup
- Import the module under test using the same file_path (adjust to project layout)
- Do not call external services — mock them with pytest-mock or monkeypatch
"""


class TestGenerator(BaseExecutor):
    __test__ = False  # not a pytest test class
    descriptor = CapabilityDescriptor(
        name        = "test_generator",
        description = "Generates pytest test files for every source artifact.",
        needs       = frozenset([ConditionId("implementation_exists")]),
        produces    = frozenset([ConditionId("tests_exist")]),
        consumes    = frozenset(),
        emits       = frozenset(["test"]),
        cost        = 1.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        if not sources:
            return CapabilityResult(errors=["no source artifacts found in store"])

        created: list[Artifact] = []
        for src in sources:
            file_path = src.metadata.get("file_path", str(src.id))
            user = f"File: {file_path}\n\n{src.content}"
            test_content = self._call(_SYSTEM, user)

            test_path = _to_test_path(file_path)
            created.append(Artifact(
                id       = ArtifactId(f"test/{test_path}"),
                kind     = ArtifactKind.TEST,
                content  = test_content,
                metadata = {"file_path": test_path, "source_id": str(src.id)},
            ))

        return CapabilityResult(delta=ArtifactDelta(created=tuple(created)))


def _to_test_path(file_path: str) -> str:
    """Convert src/foo/bar.py → tests/test_bar.py."""
    import os
    basename  = os.path.basename(file_path)
    stem, ext = os.path.splitext(basename)
    return f"tests/test_{stem}{ext}"
