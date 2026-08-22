from __future__ import annotations

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

from ._utils import head as _head
from .base import BaseExecutor

_SYSTEM = """\
You are a Python packaging expert.
Given source files and the project architecture, output a minimal requirements.txt.

Output ONLY the raw requirements.txt content — no markdown fences, no comments, no prose.
One package per line with optional version constraints.

Rules:
- Exclude ALL Python standard library modules (os, sys, json, pathlib, re, typing, abc,
  collections, contextlib, dataclasses, datetime, enum, functools, importlib, io,
  itertools, logging, math, operator, threading, time, traceback, warnings, etc.)
- Include ONLY third-party packages imported in the source code
- Use >=X.Y version constraints based on the features and syntax used
- Do NOT include the project package itself
- Always include pytest if test imports are visible

Example:
fastapi>=0.115
uvicorn[standard]>=0.30
sqlmodel>=0.0.21
pydantic>=2.0
httpx>=0.27
pytest>=8
"""

_MAX_OUTPUT = 8_000  # chars of pip output kept in artifact


class DependencyInstaller(BaseExecutor):
    """Generates requirements.txt via LLM and installs packages into a project venv.

    Cost=0.3 ensures it runs before TestRunner (0.5) and TestGenerator (1.5) whenever
    both are candidates — so tests always run inside the correct environment.

    Venv location:
      - FilesystemStore: <store.root>/.antcrew/venv/   (persisted across resume)
      - MemoryStore / anything else: process-temp directory
    """

    descriptor = CapabilityDescriptor(
        name        = "dependency_installer",
        description = "Generates requirements.txt and installs dependencies into a project venv.",
        needs       = frozenset([ConditionId("implementation_exists")]),
        produces    = frozenset([ConditionId("dependencies_installed")]),
        emits       = frozenset(["config"]),
        cost        = 0.3,
    )

    def _run(self, store, goal) -> CapabilityResult:
        sources = store.list(ArtifactKind.SOURCE)
        if not sources:
            return CapabilityResult(errors=["no source artifacts found — run CodeGenerator first"])

        # Reuse an existing requirements.txt rather than burning LLM tokens
        requirements_txt = _find_existing_requirements(store)
        if requirements_txt is None:
            arch = store.read(ArtifactId("architecture"))
            arch_text = arch.content if arch else "No architecture document."

            files_block = "\n\n".join(
                f"### {art.metadata.get('file_path', str(art.id))}\n{_head(art.content, 40)}"
                for art in sources
            )
            system = f"{_SYSTEM}\n\n## Project Architecture\n{_head(arch_text, 100)}"
            user = (
                f"Goal: {goal.description}\n\n"
                f"Source files:\n{files_block}"
            )
            requirements_txt = self._call(system, user).strip()

        req_artifact = Artifact(
            id       = ArtifactId("pip_requirements"),
            kind     = ArtifactKind.CONFIG,
            content  = requirements_txt,
            metadata = {"file_path": "requirements.txt"},
        )

        if _sandbox.use_docker():
            # Docker mode: skip host venv entirely.
            # TestRunner will run pip install + pytest in a single container
            # so malicious setup.py / post-install hooks never touch the host.
            config_artifact = Artifact(
                id       = ArtifactId("venv_config"),
                kind     = ArtifactKind.CONFIG,
                content  = {
                    "docker_mode":    True,
                    "install_ok":     True,
                    "install_output": "(deferred to Docker sandbox)",
                },
                metadata = {"file_path": ".antcrew/venv_config.json"},
            )
            return CapabilityResult(delta=ArtifactDelta(created=(config_artifact, req_artifact)))

        # Host mode (ANTCREW_SANDBOX=none or Docker unavailable): install into a local venv.
        venv_path = _resolve_venv_path(store)
        _ensure_venv(venv_path)
        ok, install_output = _install(venv_path, requirements_txt)

        python_bin = _python_bin(venv_path)

        config_artifact = Artifact(
            id       = ArtifactId("venv_config"),
            kind     = ArtifactKind.CONFIG,
            content  = {
                "docker_mode":    False,
                "venv_path":      str(venv_path),
                "python_bin":     str(python_bin),
                "install_ok":     ok,
                "install_output": install_output[-_MAX_OUTPUT:],
            },
            metadata = {"file_path": ".antcrew/venv_config.json"},
        )

        if not ok:
            return CapabilityResult(
                delta=ArtifactDelta(created=(config_artifact, req_artifact)),
                errors=[f"pip install failed (see venv_config.install_output):\n{install_output[-500:]}"],
            )

        return CapabilityResult(delta=ArtifactDelta(created=(config_artifact, req_artifact)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_existing_requirements(store) -> str | None:
    """Return existing requirements.txt content if found, else None."""
    fs_root = store.filesystem_path()
    if fs_root is not None:
        req_path = Path(fs_root) / "requirements.txt"
        if req_path.exists():
            content = req_path.read_text(encoding="utf-8").strip()
            if content:
                return content
    # Any store: look for a SOURCE artifact whose file_path is requirements.txt
    sources = store.list(ArtifactKind.SOURCE)
    for art in sources:
        fp = art.metadata.get("file_path", "")
        if fp == "requirements.txt" or fp.endswith("/requirements.txt"):
            if isinstance(art.content, str) and art.content.strip():
                return art.content.strip()
    return None


def _resolve_venv_path(store) -> Path:
    fs_root = store.filesystem_path()
    if fs_root is not None:
        return Path(fs_root) / ".antcrew" / "venv"
    return Path(tempfile.mkdtemp(prefix="antcrew_venv_"))


def _ensure_venv(venv_path: Path) -> None:
    if not venv_path.exists():
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
        )


def _python_bin(venv_path: Path) -> Path:
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _pip_bin(venv_path: Path) -> Path:
    if sys.platform == "win32":
        return venv_path / "Scripts" / "pip.exe"
    return venv_path / "bin" / "pip"


def _install(venv_path: Path, requirements_txt: str) -> tuple[bool, str]:
    pip = _pip_bin(venv_path)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(requirements_txt)
        req_file = f.name

    result = subprocess.run(
        [str(pip), "install", "-r", req_file, "--quiet"],
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output
