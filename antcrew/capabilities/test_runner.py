from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from antcrew.engine import (
    Artifact, ArtifactDelta, ArtifactId, ArtifactKind,
    CapabilityDescriptor, CapabilityResult, ConditionId,
)
from .base import BaseExecutor

_MAX_OUTPUT = 4_000


class TestRunner(BaseExecutor):
    __test__ = False

    """Runs pytest against source and test artifacts, produces a test_report.

    FilesystemStore path (preferred):
        Runs pytest directly against store.root with PYTHONPATH set — traceback
        paths are exact, so BugFixer can do direct lookups without heuristics.

    MemoryStore path (fallback):
        Writes artifacts to a temp dir and runs pytest there.

    Uses the project venv if a 'venv_config' artifact exists (written by
    DependencyInstaller), otherwise falls back to sys.executable.
    """

    descriptor = CapabilityDescriptor(
        name        = "test_runner",
        description = "Runs pytest against source and test artifacts, produces a test report.",
        needs       = frozenset([ConditionId("tests_exist")]),
        produces    = frozenset([ConditionId("tests_pass")]),
        emits       = frozenset(["report"]),
        cost        = 0.5,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        tests   = store.list(ArtifactKind.TEST)

        if not tests:
            return CapabilityResult(errors=["no test artifacts found in store"])

        python = _resolve_python(store)

        # Use --lf (last-failed) when a prior run exists and failed —
        # only meaningful for FilesystemStore where .pytest_cache persists.
        prior = store.read(ArtifactId("test_report"))
        last_failed = (
            prior is not None
            and isinstance(prior.content, dict)
            and not prior.content.get("passed", True)
        )

        fs_root = store.filesystem_path()
        if fs_root is not None:
            proc = _run_on_filesystem(fs_root, sources, tests, python,
                                      last_failed=last_failed)
        else:
            proc = _run_in_tempdir(sources, tests, python)

        output = (proc.stdout + proc.stderr)[-_MAX_OUTPUT:]
        report = {
            "passed":     proc.returncode == 0,
            "returncode": proc.returncode,
            "output":     output,
        }
        return CapabilityResult(delta=ArtifactDelta(created=(
            Artifact(id=ArtifactId("test_report"), kind=ArtifactKind.REPORT, content=report),
        )))


# ---------------------------------------------------------------------------
# Execution strategies
# ---------------------------------------------------------------------------

def _run_on_filesystem(
    root: Path, sources, tests, python: str, *, last_failed: bool = False
) -> subprocess.CompletedProcess:
    """Run pytest directly against the FilesystemStore root — no temp copy."""
    _ensure_init_files(sources, root)
    _ensure_init_files(tests, root)
    env  = {**os.environ, "PYTHONPATH": str(root)}
    args = [python, "-m", "pytest", str(root), "--tb=short", "-q",
            "--ignore=.antcrew", "--ignore=venv", "--ignore=.venv"]
    if last_failed:
        args.append("--lf")
    return subprocess.run(args, capture_output=True, text=True, cwd=str(root), env=env)


def _run_in_tempdir(sources, tests, python: str) -> subprocess.CompletedProcess:
    """Write artifacts to a temp dir and run pytest there (MemoryStore path)."""
    with tempfile.TemporaryDirectory(prefix="antcrew_run_") as tmp:
        root = Path(tmp)
        _write_artifacts(sources, root)
        _write_artifacts(tests, root)
        _setup_project_structure(sources, root)
        return subprocess.run(
            [python, "-m", "pytest", str(root), "--tb=short", "-q"],
            capture_output=True,
            text=True,
            cwd=tmp,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_python(store) -> str:
    config = store.read(ArtifactId("venv_config"))
    if config and isinstance(config.content, dict):
        python_bin = config.content.get("python_bin")
        if python_bin and Path(python_bin).exists():
            return python_bin
    return sys.executable


def _ensure_init_files(artifacts, root: Path) -> None:
    """Create missing __init__.py files for every package dir inside *root*."""
    dirs: set[Path] = set()
    for art in artifacts:
        fp = art.metadata.get("file_path")
        if not fp:
            continue
        dest = root / fp
        for parent in dest.parents:
            if parent == root:
                break
            dirs.add(parent)
    for d in sorted(dirs):
        init = d / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")


def _setup_project_structure(sources, root: Path) -> None:
    """__init__.py for package dirs + sys.path conftest (MemoryStore path only)."""
    _ensure_init_files(sources, root)
    (root / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n",
        encoding="utf-8",
    )


def _write_artifacts(artifacts, root: Path) -> None:
    for art in artifacts:
        fp = art.metadata.get("file_path")
        if not fp:
            continue
        dest = root / fp
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = art.content if isinstance(art.content, str) else json.dumps(art.content)
        dest.write_text(content, encoding="utf-8")
