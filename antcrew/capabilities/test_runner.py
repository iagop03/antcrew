from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor

_MAX_OUTPUT = 4_000  # chars kept in report


class TestRunner(BaseExecutor):
    __test__ = False  # not a pytest test class
    """Writes artifacts to a temp directory and runs pytest.

    No LLM call — pass llm=None (default).
    Returns a test_report artifact with the outcome and truncated output.
    """

    descriptor = CapabilityDescriptor(
        name        = "test_runner",
        description = "Runs pytest against source and test artifacts, produces a test report.",
        needs       = frozenset([ConditionId("tests_exist")]),
        produces    = frozenset([ConditionId("tests_pass")]),
        consumes    = frozenset(),
        emits       = frozenset(["report"]),
        cost        = 0.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        tests   = store.list(ArtifactKind.TEST)

        if not tests:
            return CapabilityResult(errors=["no test artifacts found in store"])

        with tempfile.TemporaryDirectory(prefix="antcrew_run_") as tmp:
            root = Path(tmp)
            _write_artifacts(sources, root)
            _write_artifacts(tests, root)
            (root / "conftest.py").write_text("", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(root), "--tb=short", "-q"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )

        output   = (proc.stdout + proc.stderr)[-_MAX_OUTPUT:]
        passed   = proc.returncode == 0
        report   = {
            "passed":      passed,
            "returncode":  proc.returncode,
            "output":      output,
        }
        artifact = Artifact(
            id       = ArtifactId("test_report"),
            kind     = ArtifactKind.REPORT,
            content  = report,
        )
        return CapabilityResult(delta=ArtifactDelta(created=(artifact,)))


def _write_artifacts(artifacts: list[Artifact], root: Path) -> None:
    for art in artifacts:
        file_path = art.metadata.get("file_path")
        if not file_path:
            continue
        dest = root / file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = art.content if isinstance(art.content, str) else json.dumps(art.content)
        dest.write_text(content, encoding="utf-8")
