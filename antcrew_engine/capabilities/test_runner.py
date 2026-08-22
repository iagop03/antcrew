from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
from antcrew_engine.engine import sandbox as _sandbox

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

        # For MemoryStore+docker mode: requirements won't be on disk yet;
        # pass the content so _run_in_tempdir can write it to the temp dir.
        req_art = store.read(ArtifactId("pip_requirements"))
        requirements_content = (
            req_art.content
            if req_art and isinstance(req_art.content, str)
            else None
        )

        fs_root = store.filesystem_path()
        if fs_root is not None:
            proc = _run_on_filesystem(fs_root, sources, tests, python,
                                      last_failed=last_failed)
        else:
            proc = _run_in_tempdir(sources, tests, python,
                                   requirements_content=requirements_content)

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
    """Run pytest directly against the FilesystemStore root — no temp copy.

    When ANTCREW_SANDBOX=docker and a requirements.txt is present, delegates to
    run_with_install() so pip install and pytest share a single container — no
    packages are installed on the host.
    Falls back to direct subprocess when Docker is absent and mode is 'auto'.
    """
    _ensure_init_files(sources, root)   # source packages only — tests dir stays clean
    _ensure_conftest(root)              # root conftest adds project dir to sys.path
    env  = {**os.environ, "PYTHONPATH": str(root)}
    args = [python, "-m", "pytest", str(root), "--tb=short", "-q",
            "--ignore=.antcrew", "--ignore=venv", "--ignore=.venv"]
    if last_failed:
        args.append("--lf")

    req_file = root / "requirements.txt"
    if req_file.exists() and _sandbox.use_docker():
        return _sandbox.run_with_install(args, cwd=root, requirements=req_file, env=env)
    return _sandbox.run(args, cwd=root, env=env)


def _run_in_tempdir(
    sources, tests, python: str, *, requirements_content: str | None = None
) -> subprocess.CompletedProcess:
    """Write artifacts to a temp dir and run pytest there (MemoryStore path).

    When ANTCREW_SANDBOX=docker and requirements_content is provided, writes
    requirements.txt into the temp dir and delegates to run_with_install() so
    the install step also runs inside the container.
    Falls back to direct subprocess when Docker is absent and mode is 'auto'.
    """
    with tempfile.TemporaryDirectory(prefix="antcrew_run_") as tmp:
        root = Path(tmp)
        _write_artifacts(sources, root)
        _write_artifacts(tests, root)
        _setup_project_structure(sources, root)
        env = {**os.environ, "PYTHONPATH": str(root)}

        req_file = root / "requirements.txt"
        if requirements_content and _sandbox.use_docker():
            req_file.write_text(requirements_content, encoding="utf-8")
            return _sandbox.run_with_install(
                [python, "-m", "pytest", str(root), "--tb=short", "-q"],
                cwd=root, requirements=req_file, env=env,
            )
        return _sandbox.run(
            [python, "-m", "pytest", str(root), "--tb=short", "-q"],
            cwd=root, env=env,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_python(store) -> str:
    config = store.read(ArtifactId("venv_config"))
    if config and isinstance(config.content, dict):
        if config.content.get("docker_mode"):
            return "python"  # Docker image provides its own interpreter
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


def _ensure_conftest(root: Path) -> None:
    """Write a root conftest.py that adds the project dir to sys.path.

    Skipped when one already exists (user-supplied conftest takes precedence).
    """
    conftest = root / "conftest.py"
    if not conftest.exists():
        conftest.write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent))\n",
            encoding="utf-8",
        )


def _setup_project_structure(sources, root: Path) -> None:
    """__init__.py for source package dirs + sys.path conftest (MemoryStore path)."""
    _ensure_init_files(sources, root)
    _ensure_conftest(root)


def _write_artifacts(artifacts, root: Path) -> None:
    for art in artifacts:
        fp = art.metadata.get("file_path")
        if not fp:
            continue
        dest = root / fp
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = art.content if isinstance(art.content, str) else json.dumps(art.content)
        dest.write_text(content, encoding="utf-8")
