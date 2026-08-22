"""Unit tests for DependencyInstaller helpers (no LLM, no pip)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antcrew_engine.engine import (
    Artifact, ArtifactId, ArtifactKind, MemoryStore, FilesystemStore,
)
from antcrew_engine.capabilities.dependency_installer import (
    _find_existing_requirements,
    _resolve_venv_path,
    _python_bin,
    _pip_bin,
    _ensure_venv,
    _install,
)


# ---------------------------------------------------------------------------
# _find_existing_requirements
# ---------------------------------------------------------------------------

class TestFindExistingRequirements:
    def test_returns_none_when_no_requirements(self):
        store = MemoryStore()
        assert _find_existing_requirements(store) is None

    def test_finds_source_artifact_with_requirements_path(self):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("pip_requirements"),
            kind=ArtifactKind.SOURCE,
            content="fastapi>=0.115\nuvicorn>=0.30\n",
            metadata={"file_path": "requirements.txt"},
        ))
        result = _find_existing_requirements(store)
        assert result is not None
        assert "fastapi" in result

    def test_finds_requirements_in_filesystem_store(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("httpx>=0.27\npytest>=8\n")
        store = FilesystemStore(tmp_path)
        result = _find_existing_requirements(store)
        assert result is not None
        assert "httpx" in result

    def test_skips_empty_requirements_file(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("   ")
        store = FilesystemStore(tmp_path)
        assert _find_existing_requirements(store) is None

    def test_finds_nested_requirements_path(self):
        store = MemoryStore()
        store.write(Artifact(
            id=ArtifactId("reqs"),
            kind=ArtifactKind.SOURCE,
            content="pydantic>=2.0\n",
            metadata={"file_path": "backend/requirements.txt"},
        ))
        result = _find_existing_requirements(store)
        assert result is not None
        assert "pydantic" in result


# ---------------------------------------------------------------------------
# _resolve_venv_path
# ---------------------------------------------------------------------------

class TestResolveVenvPath:
    def test_filesystem_store_places_venv_in_antcrew_dir(self, tmp_path):
        store = FilesystemStore(tmp_path)
        venv = _resolve_venv_path(store)
        assert venv == tmp_path / ".antcrew" / "venv"

    def test_memory_store_returns_temp_dir(self):
        store = MemoryStore()
        venv = _resolve_venv_path(store)
        assert venv.exists()
        assert "antcrew_venv_" in venv.name


# ---------------------------------------------------------------------------
# _python_bin / _pip_bin
# ---------------------------------------------------------------------------

class TestBinPaths:
    def test_python_bin_win32(self, tmp_path):
        with patch("sys.platform", "win32"):
            result = _python_bin(tmp_path)
        assert result == tmp_path / "Scripts" / "python.exe"

    def test_python_bin_posix(self, tmp_path):
        with patch("sys.platform", "linux"):
            result = _python_bin(tmp_path)
        assert result == tmp_path / "bin" / "python"

    def test_pip_bin_win32(self, tmp_path):
        with patch("sys.platform", "win32"):
            result = _pip_bin(tmp_path)
        assert result == tmp_path / "Scripts" / "pip.exe"

    def test_pip_bin_posix(self, tmp_path):
        with patch("sys.platform", "linux"):
            result = _pip_bin(tmp_path)
        assert result == tmp_path / "bin" / "pip"


# ---------------------------------------------------------------------------
# _ensure_venv
# ---------------------------------------------------------------------------

class TestEnsureVenv:
    def test_skips_creation_when_venv_exists(self, tmp_path):
        venv_path = tmp_path / "venv"
        venv_path.mkdir()
        with patch("subprocess.run") as mock_run:
            _ensure_venv(venv_path)
            mock_run.assert_not_called()

    def test_creates_venv_when_missing(self, tmp_path):
        venv_path = tmp_path / "venv"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _ensure_venv(venv_path)
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "-m" in call_args
            assert "venv" in call_args
            assert str(venv_path) in call_args


# ---------------------------------------------------------------------------
# _install
# ---------------------------------------------------------------------------

class TestInstall:
    def test_returns_true_on_success(self, tmp_path):
        venv_path = tmp_path / "venv"
        # Create a fake pip executable so _pip_bin resolves
        scripts = venv_path / ("Scripts" if sys.platform == "win32" else "bin")
        scripts.mkdir(parents=True)
        pip_name = "pip.exe" if sys.platform == "win32" else "pip"
        (scripts / pip_name).write_text("")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Successfully installed\n", stderr=""
            )
            ok, output = _install(venv_path, "fastapi>=0.115\n")
        assert ok is True

    def test_returns_false_on_pip_failure(self, tmp_path):
        venv_path = tmp_path / "venv"
        scripts = venv_path / ("Scripts" if sys.platform == "win32" else "bin")
        scripts.mkdir(parents=True)
        pip_name = "pip.exe" if sys.platform == "win32" else "pip"
        (scripts / pip_name).write_text("")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="ERROR: No matching distribution"
            )
            ok, output = _install(venv_path, "nonexistent-package-xyz>=999\n")
        assert ok is False
        assert "ERROR" in output
