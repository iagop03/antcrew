"""SandboxRunner — execute AI-generated test suites in an isolated environment.

LocalRunner  — writes artifacts to a temp dir and runs pytest in a subprocess
               (same Python process; fast but no isolation).

DockerRunner — creates a fresh Docker container per run, copies artifacts in,
               executes pytest, then removes the container entirely; the host
               filesystem is never written to and the network is blocked by default.

Usage — LocalRunner::

    from antcrew.sandbox import LocalRunner

    runner = LocalRunner()
    result = runner.run(state["test_artifacts"], code_artifacts=state["code_artifacts"])
    print(result.summary())   # "3 passed, 0 failed in 412ms"

Usage — DockerRunner::

    from antcrew.sandbox import DockerRunner
    from antcrew.teams.dev_team import DevTeam

    team = DevTeam(runner=DockerRunner(requirements=["pytest", "requests"]))
    state = team.run("Build auth module")
    print(state["test_results"].summary())
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from antcrew.core.artifacts import CodeArtifact, TestArtifact


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Outcome of a sandbox test run."""

    passed: int
    failed: int
    errors: int        # collection / import errors
    total: int
    duration_ms: float
    output: str        # full pytest stdout + stderr
    returncode: int
    coverage_pct: Optional[float] = None   # line coverage % when with_coverage=True

    @property
    def success(self) -> bool:
        """True only when all tests passed and no collection errors occurred."""
        return self.failed == 0 and self.errors == 0 and self.returncode == 0

    def summary(self) -> str:
        """Short human-readable line, e.g. '3 passed, 1 failed in 210ms'."""
        parts: list[str] = []
        if self.passed:
            parts.append(f"{self.passed} passed")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.errors:
            parts.append(f"{self.errors} error{'s' if self.errors != 1 else ''}")
        if not parts:
            parts.append("no tests ran")
        return f"{', '.join(parts)} in {self.duration_ms:.0f}ms"

    def to_dict(self) -> dict:
        d = {
            "passed":      self.passed,
            "failed":      self.failed,
            "errors":      self.errors,
            "total":       self.total,
            "duration_ms": self.duration_ms,
            "success":     self.success,
            "output":      self.output,
            "returncode":  self.returncode,
        }
        if self.coverage_pct is not None:
            d["coverage_pct"] = self.coverage_pct
        return d


@dataclass
class ExecutionResult:
    """Outcome of an arbitrary code snippet executed via execute_code()."""

    code: str
    stdout: str
    stderr: str
    returncode: int
    duration_ms: float

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict:
        return {
            "code":        self.code,
            "stdout":      self.stdout,
            "stderr":      self.stderr,
            "returncode":  self.returncode,
            "duration_ms": self.duration_ms,
            "success":     self.success,
        }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SandboxRunner(ABC):
    """Abstract base for test sandbox runners."""

    @abstractmethod
    def run(
        self,
        test_artifacts: "list[TestArtifact]",
        *,
        code_artifacts: "Optional[list[CodeArtifact]]" = None,
    ) -> RunResult:
        """Write artifacts to an isolated environment and execute pytest."""


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

_CONFTEST = (
    "import sys, os\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
)

_PYTEST_FLAGS = ["-v", "--tb=short", "--no-header", "--color=no", "-p", "no:cacheprovider"]


def _write_artifacts(
    root: Path,
    test_artifacts: "list[TestArtifact]",
    code_artifacts: "list[CodeArtifact]",
) -> None:
    """Write code + test files and a root conftest.py into *root*."""
    for art in code_artifacts:
        dest = root / art.file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(art.content, encoding="utf-8")

    for art in test_artifacts:
        dest = root / art.file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(art.content, encoding="utf-8")

    conftest = root / "conftest.py"
    if not conftest.exists():
        conftest.write_text(_CONFTEST, encoding="utf-8")


# ---------------------------------------------------------------------------
# LocalRunner
# ---------------------------------------------------------------------------

class LocalRunner(SandboxRunner):
    """Runs pytest in a subprocess inside a temporary directory.

    The temp dir is created fresh for every run and deleted on exit.
    A ``conftest.py`` is placed at the root so that code artifacts can be
    imported by their file path (e.g. ``from src.auth import login``).

    Args:
        python:  Path to the Python interpreter (default: current interpreter).
        timeout: Maximum seconds to wait for pytest before killing it (default 60).
    """

    def __init__(self, *, python: str = sys.executable, timeout: int = 60) -> None:
        self._python = python
        self._timeout = timeout

    def run(
        self,
        test_artifacts: "list[TestArtifact]",
        *,
        code_artifacts: "Optional[list[CodeArtifact]]" = None,
        with_coverage: bool = False,
    ) -> RunResult:
        """Run pytest on *test_artifacts*.

        Args:
            test_artifacts:  Test files to execute.
            code_artifacts:  Source files to write alongside the tests.
            with_coverage:   Add ``--cov --cov-report=term-missing`` flags and
                             parse the TOTAL coverage percentage into
                             ``RunResult.coverage_pct``.  Requires
                             ``pytest-cov`` to be installed in the interpreter.
        """
        if not test_artifacts:
            return RunResult(
                passed=0, failed=0, errors=0, total=0,
                duration_ms=0.0,
                output="No test artifacts provided.",
                returncode=0,
            )

        extra_flags = ["--cov", "--cov-report=term-missing"] if with_coverage else []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_artifacts(root, test_artifacts, code_artifacts or [])

            t0 = time.time()
            try:
                proc = subprocess.run(
                    [self._python, "-m", "pytest", "."] + _PYTEST_FLAGS + extra_flags,
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
                elapsed_ms = (time.time() - t0) * 1000
                output = proc.stdout + (proc.stderr or "")
                passed, failed, errors = _parse_counts(output)
                coverage_pct = _parse_coverage(output) if with_coverage else None
                return RunResult(
                    passed=passed,
                    failed=failed,
                    errors=errors,
                    total=passed + failed + errors,
                    duration_ms=round(elapsed_ms, 1),
                    output=output,
                    returncode=proc.returncode,
                    coverage_pct=coverage_pct,
                )

            except subprocess.TimeoutExpired:
                return RunResult(
                    passed=0, failed=0, errors=1, total=1,
                    duration_ms=float(self._timeout * 1000),
                    output=f"Tests timed out after {self._timeout}s.",
                    returncode=-1,
                )

            except FileNotFoundError as exc:
                return RunResult(
                    passed=0, failed=0, errors=1, total=1,
                    duration_ms=0.0,
                    output=f"Could not start pytest: {exc}",
                    returncode=-1,
                )

    def execute_code(self, code: str, *, timeout: Optional[int] = None) -> ExecutionResult:
        """Execute an arbitrary Python snippet and return stdout/stderr.

        The snippet runs in a temporary file via a subprocess using the same
        interpreter as the runner.  No test framework is involved.

        Args:
            code:    Python source code to execute.
            timeout: Override the runner's default timeout for this call.
        """
        t_limit = timeout if timeout is not None else self._timeout

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "_snippet.py"
            script.write_text(code, encoding="utf-8")

            t0 = time.time()
            try:
                proc = subprocess.run(
                    [self._python, str(script)],
                    capture_output=True,
                    text=True,
                    timeout=t_limit,
                )
                elapsed_ms = round((time.time() - t0) * 1000, 1)
                return ExecutionResult(
                    code=code,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    returncode=proc.returncode,
                    duration_ms=elapsed_ms,
                )
            except subprocess.TimeoutExpired:
                return ExecutionResult(
                    code=code,
                    stdout="",
                    stderr=f"Execution timed out after {t_limit}s.",
                    returncode=-1,
                    duration_ms=float(t_limit * 1000),
                )
            except FileNotFoundError as exc:
                return ExecutionResult(
                    code=code,
                    stdout="",
                    stderr=f"Could not start interpreter: {exc}",
                    returncode=-1,
                    duration_ms=0.0,
                )


# ---------------------------------------------------------------------------
# DockerRunner
# ---------------------------------------------------------------------------

class DockerRunner(SandboxRunner):
    """Runs pytest inside a Docker container for real process and filesystem isolation.

    Each run:
      1. Creates a fresh container (``docker create``)
      2. Copies all artifacts into ``/workspace/`` via a tar stream
      3. Starts the container and captures stdout + stderr (``docker start -a``)
      4. Removes the container regardless of outcome (``docker rm -f``)

    The host filesystem is never written to during the run.  By default the
    container has no network access (``--network none``).

    Requirements:
        Docker must be installed and the Docker daemon must be running.

    Args:
        image:        Docker image (default: ``"python:3.12-slim"``).
                      Use a custom image with pre-installed packages to avoid
                      pip install overhead on every run.
        requirements: Python packages to install before running pytest
                      (default: ``["pytest"]``).  Pass ``[]`` if your image
                      already has everything installed.
        timeout:      Seconds before the container is killed (default: 120).
        network:      Docker network mode (default: ``"none"`` — no internet).
                      Set to ``"bridge"`` if tests need outbound connections.
        memory:       Container memory limit in Docker format (e.g. ``"512m"``,
                      ``"1g"``).  Pass ``None`` to use Docker's default.
        cpus:         CPU quota (e.g. ``"1.0"``).  Pass ``None`` to skip.

    Example::

        from antcrew.sandbox import DockerRunner

        runner = DockerRunner(
            image="python:3.12-slim",
            requirements=["pytest", "requests", "pydantic"],
            network="none",
        )
        result = runner.run(test_artifacts, code_artifacts=code_artifacts)
        print(result.summary())
    """

    def __init__(
        self,
        *,
        image: str = "python:3.12-slim",
        requirements: Optional[list[str]] = None,
        timeout: int = 120,
        network: str = "none",
        memory: Optional[str] = "512m",
        cpus: Optional[str] = "1.0",
    ) -> None:
        self._image        = image
        self._requirements = requirements if requirements is not None else ["pytest"]
        self._timeout      = timeout
        self._network      = network
        self._memory       = memory
        self._cpus         = cpus

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(
        self,
        test_artifacts: "list[TestArtifact]",
        *,
        code_artifacts: "Optional[list[CodeArtifact]]" = None,
    ) -> RunResult:
        if not test_artifacts:
            return RunResult(
                passed=0, failed=0, errors=0, total=0,
                duration_ms=0.0,
                output="No test artifacts provided.",
                returncode=0,
            )

        if not self._is_docker_available():
            return RunResult(
                passed=0, failed=0, errors=1, total=1,
                duration_ms=0.0,
                output=(
                    "Docker is not available. "
                    "Install Docker and ensure the daemon is running."
                ),
                returncode=-1,
            )

        t0 = time.time()
        container_id: Optional[str] = None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_artifacts(root, test_artifacts, code_artifacts or [])

            try:
                container_id = self._create_container()
                self._copy_files(container_id, root)
                return self._start_and_collect(container_id, t0)

            except Exception as exc:
                return RunResult(
                    passed=0, failed=0, errors=1, total=1,
                    duration_ms=round((time.time() - t0) * 1000, 1),
                    output=f"DockerRunner error: {exc}",
                    returncode=-1,
                )
            finally:
                if container_id:
                    self._cleanup(container_id)

    # ------------------------------------------------------------------
    # Docker operations (each maps to one docker CLI call)
    # ------------------------------------------------------------------

    def _is_docker_available(self) -> bool:
        """Return True if the Docker daemon is reachable."""
        try:
            result = subprocess.run(
                ["docker", "version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def _create_container(self) -> str:
        """``docker create`` — returns the container ID."""
        cmd = ["docker", "create", "-w", "/workspace"]
        if self._network:
            cmd += ["--network", self._network]
        if self._memory:
            cmd += ["--memory", self._memory]
        if self._cpus:
            cmd += ["--cpus", self._cpus]
        cmd.append(self._image)

        if self._requirements:
            reqs = " ".join(self._requirements)
            inner = (
                f"pip install -q {reqs} && "
                f"python -m pytest . {' '.join(_PYTEST_FLAGS)}"
            )
        else:
            inner = f"python -m pytest . {' '.join(_PYTEST_FLAGS)}"

        cmd += ["sh", "-c", inner]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker create failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _copy_files(self, container_id: str, source_dir: Path) -> None:
        """Copy all files from *source_dir* into ``/workspace/`` via a tar stream."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for filepath in source_dir.rglob("*"):
                if filepath.is_file():
                    arcname = str(filepath.relative_to(source_dir))
                    tar.add(str(filepath), arcname=arcname)
        buf.seek(0)

        result = subprocess.run(
            ["docker", "cp", "-", f"{container_id}:/workspace/"],
            input=buf.getvalue(),
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker cp failed (exit {result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )

    def _start_and_collect(self, container_id: str, t0: float) -> RunResult:
        """``docker start -a`` — blocks until the container exits."""
        try:
            proc = subprocess.run(
                ["docker", "start", "-a", container_id],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", container_id], capture_output=True)
            return RunResult(
                passed=0, failed=0, errors=1, total=1,
                duration_ms=float(self._timeout * 1000),
                output=f"Container timed out after {self._timeout}s.",
                returncode=-1,
            )

        elapsed_ms = round((time.time() - t0) * 1000, 1)
        output     = proc.stdout + (proc.stderr or "")
        passed, failed, errors = _parse_counts(output)
        return RunResult(
            passed=passed,
            failed=failed,
            errors=errors,
            total=passed + failed + errors,
            duration_ms=elapsed_ms,
            output=output,
            returncode=proc.returncode,
        )

    def _cleanup(self, container_id: str) -> None:
        """``docker rm -f`` — silently ignore failures."""
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_counts(output: str) -> tuple[int, int, int]:
    """Extract (passed, failed, errors) from pytest stdout."""
    passed = failed = errors = 0
    for m in re.finditer(r"(\d+)\s+(passed|failed|error)", output):
        n    = int(m.group(1))
        kind = m.group(2)
        if kind == "passed":
            passed = n
        elif kind == "failed":
            failed = n
        elif kind == "error":
            errors = n
    return passed, failed, errors


def _parse_coverage(output: str) -> Optional[float]:
    """Extract total line-coverage % from pytest-cov output.

    Looks for the ``TOTAL ... NN%`` line emitted by ``--cov-report=term-missing``.
    Returns ``None`` when the line is absent (pytest-cov not installed, no sources).
    """
    m = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", output, re.MULTILINE)
    if m:
        return float(m.group(1))
    return None
