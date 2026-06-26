"""Tests for `antcrew test` CLI command (v0.8.1)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.sandbox.runner import RunResult as SandboxRunResult

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TEST_ARTIFACT = {
    "ticket_id": "T-001",
    "file_path": "tests/test_auth.py",
    "description": "Tests for auth",
    "content": "def test_pass():\n    assert True\n",
    "language": "python",
    "coverage_areas": ["unit"],
}

_CODE_ARTIFACT = {
    "ticket_id": "T-001",
    "file_path": "src/auth.py",
    "description": "Auth module",
    "content": "def login():\n    return True\n",
    "language": "python",
}


def _state_file(tmp_path: Path, *, test_arts=None, code_arts=None, nested=False) -> Path:
    """Write a state JSON file and return its path."""
    state = {
        "request": "Build auth",
        "test_artifacts": test_arts if test_arts is not None else [_TEST_ARTIFACT],
        "code_artifacts": code_arts if code_arts is not None else [_CODE_ARTIFACT],
    }
    if nested:
        data = {"name": "my-project", "state": state}
    else:
        data = state
    p = tmp_path / "state.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _ok_result() -> SandboxRunResult:
    return SandboxRunResult(
        passed=3, failed=0, errors=0, total=3,
        duration_ms=120.0, output="3 passed in 0.12s", returncode=0,
    )


def _fail_result() -> SandboxRunResult:
    return SandboxRunResult(
        passed=1, failed=2, errors=0, total=3,
        duration_ms=200.0, output="1 passed, 2 failed", returncode=1,
    )


# ===========================================================================
# File validation
# ===========================================================================

def test_missing_state_file():
    result = runner.invoke(app, ["test", "nonexistent.json"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "State file" in result.output


def test_no_test_artifacts_exits_0(tmp_path):
    p = _state_file(tmp_path, test_arts=[])
    result = runner.invoke(app, ["test", str(p)])
    assert result.exit_code == 0
    assert "No test artifacts" in result.output


def test_no_test_artifacts_message_mentions_pipeline(tmp_path):
    p = _state_file(tmp_path, test_arts=[])
    result = runner.invoke(app, ["test", str(p)])
    # Should hint at how to get test artifacts
    assert "pipeline" in result.output.lower() or "QA" in result.output


# ===========================================================================
# Successful run
# ===========================================================================

def test_successful_run_exits_0(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p)])
    assert result.exit_code == 0


def test_successful_run_shows_summary(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p)])
    assert "3 passed" in result.output or "passed" in result.output.lower()


def test_successful_run_no_pytest_output_by_default(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p)])
    # Full pytest output panel not shown on success without --verbose
    assert "3 passed in 0.12s" not in result.output


# ===========================================================================
# Failing run
# ===========================================================================

def test_failing_run_exits_1(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_fail_result()):
        result = runner.invoke(app, ["test", str(p)])
    assert result.exit_code == 1


def test_failing_run_shows_pytest_output(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_fail_result()):
        result = runner.invoke(app, ["test", str(p)])
    # On failure, full output panel is shown
    assert "1 passed, 2 failed" in result.output


# ===========================================================================
# --verbose flag
# ===========================================================================

def test_verbose_shows_full_output_on_success(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p), "--verbose"])
    assert "3 passed in 0.12s" in result.output


def test_verbose_flag_short_form(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p), "-v"])
    assert "3 passed in 0.12s" in result.output


# ===========================================================================
# --no-code flag
# ===========================================================================

def test_no_code_excludes_code_artifacts(tmp_path):
    p = _state_file(tmp_path)
    captured_code_arts: list = []

    def _fake_run(test_artifacts, *, code_artifacts=None):
        captured_code_arts.extend(code_artifacts or [])
        return _ok_result()

    with patch("antcrew.sandbox.runner.LocalRunner.run", side_effect=_fake_run):
        result = runner.invoke(app, ["test", str(p), "--no-code"])

    assert result.exit_code == 0
    assert captured_code_arts == []


def test_without_no_code_includes_code_artifacts(tmp_path):
    p = _state_file(tmp_path)
    captured_code_arts: list = []

    def _fake_run(test_artifacts, *, code_artifacts=None):
        captured_code_arts.extend(code_artifacts or [])
        return _ok_result()

    with patch("antcrew.sandbox.runner.LocalRunner.run", side_effect=_fake_run):
        result = runner.invoke(app, ["test", str(p)])

    assert result.exit_code == 0
    assert len(captured_code_arts) == 1


# ===========================================================================
# --keep flag
# ===========================================================================

def test_keep_writes_files_to_directory(tmp_path):
    state_p = _state_file(tmp_path)
    keep_dir = tmp_path / "kept"

    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(state_p), "--keep", str(keep_dir)])

    assert result.exit_code == 0
    assert keep_dir.exists()
    # Test artifact should be written
    written = list(keep_dir.rglob("*.py"))
    assert len(written) >= 1


def test_keep_creates_output_directory(tmp_path):
    state_p = _state_file(tmp_path)
    keep_dir = tmp_path / "nested" / "output"

    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        runner.invoke(app, ["test", str(state_p), "--keep", str(keep_dir)])

    assert keep_dir.exists()


def test_keep_mentions_directory_in_output(tmp_path):
    state_p = _state_file(tmp_path)
    keep_dir = tmp_path / "kept"

    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(state_p), "--keep", str(keep_dir)])

    assert str(keep_dir) in result.output or "Files written" in result.output


# ===========================================================================
# --runner flag
# ===========================================================================

def test_runner_local_uses_local_runner(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()) as mock_run:
        result = runner.invoke(app, ["test", str(p), "--runner", "local"])
    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_runner_docker_uses_docker_runner(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.DockerRunner.run", return_value=_ok_result()) as mock_run:
        result = runner.invoke(app, ["test", str(p), "--runner", "docker"])
    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_runner_invalid_exits_1(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p), "--runner", "k8s"])
    assert result.exit_code == 1
    assert "Unknown runner" in result.output


# ===========================================================================
# Nested state (project file)
# ===========================================================================

def test_nested_project_state_unwrapped(tmp_path):
    p = _state_file(tmp_path, nested=True)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p)])
    assert result.exit_code == 0


def test_nested_state_test_artifacts_found(tmp_path):
    p = _state_file(tmp_path, nested=True)
    captured: list = []

    def _fake_run(test_artifacts, *, code_artifacts=None):
        captured.extend(test_artifacts)
        return _ok_result()

    with patch("antcrew.sandbox.runner.LocalRunner.run", side_effect=_fake_run):
        runner.invoke(app, ["test", str(p)])

    assert len(captured) == 1
    assert captured[0].file_path == "tests/test_auth.py"


# ===========================================================================
# Header display
# ===========================================================================

def test_output_shows_state_file_name(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p)])
    assert p.name in result.output or str(p) in result.output


def test_output_shows_file_counts(tmp_path):
    p = _state_file(tmp_path)
    with patch("antcrew.sandbox.runner.LocalRunner.run", return_value=_ok_result()):
        result = runner.invoke(app, ["test", str(p)])
    assert "1 test file" in result.output or "test file" in result.output
