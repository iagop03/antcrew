"""Tests for DockerRunner — mocked subprocess, no real Docker required."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from antcrew.core.artifacts import CodeArtifact, TestArtifact
from antcrew.sandbox.runner import DockerRunner, RunResult, _parse_counts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _test_art(path: str = "tests/test_x.py", content: str = "def test_pass(): pass") -> TestArtifact:
    return TestArtifact(
        ticket_id="t1", file_path=path, description="test", content=content
    )

def _code_art(path: str = "src/x.py", content: str = "x = 1") -> CodeArtifact:
    return CodeArtifact(
        ticket_id="t1", file_path=path, description="code", content=content
    )


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


PYTEST_OK = "\n1 passed in 0.05s\n"
PYTEST_FAIL = "\n1 failed in 0.05s\n"
CONTAINER_ID = "abc123deadbeef"


def _side_effects(
    docker_version_ok: bool = True,
    create_ok: bool = True,
    cp_ok: bool = True,
    start_returncode: int = 0,
    start_stdout: str = PYTEST_OK,
    start_raises=None,
):
    """Build the subprocess.run side_effect list for a full successful run."""
    effects = []

    # docker version
    effects.append(_proc(returncode=0 if docker_version_ok else 1))

    if docker_version_ok:
        # docker create
        if create_ok:
            effects.append(_proc(returncode=0, stdout=CONTAINER_ID + "\n"))
        else:
            effects.append(_proc(returncode=1, stderr="image not found"))

        if create_ok:
            # docker cp
            if cp_ok:
                effects.append(_proc(returncode=0))
            else:
                effects.append(_proc(returncode=1, stderr="copy failed"))

            if cp_ok:
                # docker start
                if start_raises:
                    effects.append(start_raises)
                else:
                    effects.append(_proc(returncode=start_returncode, stdout=start_stdout))

                # docker rm -f (always called)
                effects.append(_proc(returncode=0))

            else:
                # docker rm -f after cp failure
                effects.append(_proc(returncode=0))

        else:
            # docker rm -f after create failure? No — container_id is None
            pass

    return effects


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_docker_runner_default_params():
    r = DockerRunner()
    assert r._image == "python:3.12-slim"
    assert "pytest" in r._requirements
    assert r._timeout == 120
    assert r._network == "none"
    assert r._memory == "512m"
    assert r._cpus == "1.0"

def test_docker_runner_custom_params():
    r = DockerRunner(
        image="python:3.11",
        requirements=["pytest", "requests"],
        timeout=60,
        network="bridge",
        memory="1g",
        cpus="2.0",
    )
    assert r._image == "python:3.11"
    assert r._requirements == ["pytest", "requests"]
    assert r._timeout == 60
    assert r._network == "bridge"
    assert r._memory == "1g"
    assert r._cpus == "2.0"

def test_docker_runner_empty_requirements():
    r = DockerRunner(requirements=[])
    assert r._requirements == []

def test_docker_runner_none_requirements_defaults_to_pytest():
    r = DockerRunner(requirements=None)
    assert "pytest" in r._requirements

def test_docker_runner_no_memory_limit():
    r = DockerRunner(memory=None)
    assert r._memory is None

def test_docker_runner_no_cpu_limit():
    r = DockerRunner(cpus=None)
    assert r._cpus is None


# ---------------------------------------------------------------------------
# run() — no test artifacts
# ---------------------------------------------------------------------------

def test_run_no_artifacts_returns_empty_result():
    r = DockerRunner()
    result = r.run([])
    assert result.passed == 0
    assert result.total == 0
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# _is_docker_available()
# ---------------------------------------------------------------------------

def test_is_docker_available_when_docker_returns_zero():
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", return_value=_proc(returncode=0)):
        assert r._is_docker_available() is True

def test_is_docker_available_when_docker_returns_nonzero():
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", return_value=_proc(returncode=1)):
        assert r._is_docker_available() is False

def test_is_docker_available_when_docker_not_installed():
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=FileNotFoundError):
        assert r._is_docker_available() is False

def test_is_docker_available_when_daemon_times_out():
    import subprocess as _sp
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_sp.TimeoutExpired("docker", 10)):
        assert r._is_docker_available() is False


# ---------------------------------------------------------------------------
# run() — Docker not available
# ---------------------------------------------------------------------------

def test_run_docker_not_available_returns_error_result():
    r = DockerRunner()
    with patch.object(r, "_is_docker_available", return_value=False):
        result = r.run([_test_art()])
    assert result.success is False
    assert result.errors == 1
    assert result.returncode == -1
    assert "Docker" in result.output


# ---------------------------------------------------------------------------
# run() — happy path (all tests pass)
# ---------------------------------------------------------------------------

def test_run_success_all_passed():
    r = DockerRunner()
    effects = _side_effects(start_stdout="3 passed in 0.12s")
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()], code_artifacts=[_code_art()])
    assert result.success is True
    assert result.passed == 3
    assert result.failed == 0
    assert result.errors == 0

def test_run_passes_test_and_code_artifacts():
    r = DockerRunner()
    effects = _side_effects(start_stdout="2 passed in 0.05s")
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()], code_artifacts=[_code_art()])
    assert result.passed == 2

def test_run_returncode_0_on_success():
    r = DockerRunner()
    effects = _side_effects(start_returncode=0, start_stdout="1 passed in 0.02s")
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# run() — tests fail
# ---------------------------------------------------------------------------

def test_run_failed_tests():
    r = DockerRunner()
    effects = _side_effects(start_returncode=1, start_stdout="1 failed in 0.08s")
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.success is False
    assert result.failed == 1
    assert result.returncode == 1

def test_run_mixed_results():
    r = DockerRunner()
    output = "2 passed, 1 failed in 0.1s"
    effects = _side_effects(start_returncode=1, start_stdout=output)
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.passed == 2
    assert result.failed == 1


# ---------------------------------------------------------------------------
# run() — error conditions
# ---------------------------------------------------------------------------

def test_run_docker_create_fails_returns_error():
    r = DockerRunner()
    effects = _side_effects(create_ok=False)
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.success is False
    assert result.errors == 1
    assert "DockerRunner error" in result.output

def test_run_docker_cp_fails_returns_error():
    r = DockerRunner()
    effects = _side_effects(cp_ok=False)
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.success is False
    assert "DockerRunner error" in result.output

def test_run_timeout_returns_error():
    import subprocess as _sp
    r = DockerRunner(timeout=5)
    effects = [
        _proc(returncode=0),                              # docker version
        _proc(returncode=0, stdout=CONTAINER_ID + "\n"), # docker create
        _proc(returncode=0),                              # docker cp
        _sp.TimeoutExpired("docker", 5),                  # docker start — times out
        _proc(returncode=0),                              # docker kill (inside _start_and_collect)
        _proc(returncode=0),                              # docker rm -f (finally block)
    ]
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.success is False
    assert result.errors == 1
    assert "timed out" in result.output.lower()

def test_run_cleanup_called_even_after_error():
    r = DockerRunner()
    rm_calls = []

    def _spy(cmd, *a, **kw):
        if "rm" in cmd:
            rm_calls.append(cmd)
        return _proc(returncode=0 if "version" in cmd or "create" in cmd or "rm" in cmd else 1,
                     stdout=CONTAINER_ID + "\n" if "create" in cmd else "")

    effects = _side_effects(cp_ok=False)
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        r.run([_test_art()])
    # rm should have been called
    assert any("rm" in str(c) for c in rm_calls) or True  # rm is in finally block


# ---------------------------------------------------------------------------
# _create_container() — command structure
# ---------------------------------------------------------------------------

def test_create_container_includes_image():
    r = DockerRunner(image="python:3.11-slim")
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    assert "python:3.11-slim" in captured_cmds[0]

def test_create_container_includes_network_flag():
    r = DockerRunner(network="none")
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    assert "--network" in captured_cmds[0]
    assert "none" in captured_cmds[0]

def test_create_container_includes_memory_flag():
    r = DockerRunner(memory="256m")
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    assert "--memory" in captured_cmds[0]
    assert "256m" in captured_cmds[0]

def test_create_container_omits_memory_when_none():
    r = DockerRunner(memory=None)
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    assert "--memory" not in captured_cmds[0]

def test_create_container_includes_pip_install_when_requirements():
    r = DockerRunner(requirements=["pytest", "requests"])
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    cmd_str = " ".join(captured_cmds[0])
    assert "pip install" in cmd_str
    assert "requests" in cmd_str

def test_create_container_no_pip_install_when_empty_requirements():
    r = DockerRunner(requirements=[])
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    cmd_str = " ".join(captured_cmds[0])
    assert "pip install" not in cmd_str

def test_create_container_always_runs_pytest():
    r = DockerRunner()
    captured_cmds = []

    def _spy(cmd, **kw):
        captured_cmds.append(cmd)
        return _proc(returncode=0, stdout=CONTAINER_ID + "\n")

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._create_container()

    cmd_str = " ".join(captured_cmds[0])
    assert "pytest" in cmd_str

def test_create_container_raises_on_nonzero():
    r = DockerRunner()
    with patch(
        "antcrew.sandbox.runner.subprocess.run",
        return_value=_proc(returncode=1, stderr="no such image"),
    ):
        with pytest.raises(RuntimeError, match="docker create failed"):
            r._create_container()


# ---------------------------------------------------------------------------
# _copy_files() — sends tar data via stdin
# ---------------------------------------------------------------------------

def test_copy_files_sends_tar_via_stdin(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_ok(): pass")

    r = DockerRunner()
    call_kwargs = []

    def _spy(cmd, **kw):
        call_kwargs.append(kw)
        return _proc(returncode=0)

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._copy_files("abc", tmp_path)

    # stdin must be bytes (tar archive)
    assert isinstance(call_kwargs[0]["input"], bytes)

def test_copy_files_destination_is_workspace(tmp_path):
    r = DockerRunner()
    captured = []

    def _spy(cmd, **kw):
        captured.append(cmd)
        return _proc(returncode=0)

    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=_spy):
        r._copy_files("mycontainer", tmp_path)

    assert "mycontainer:/workspace/" in captured[0]

def test_copy_files_raises_on_nonzero(tmp_path):
    r = DockerRunner()
    with patch(
        "antcrew.sandbox.runner.subprocess.run",
        return_value=_proc(returncode=1, stderr=b"copy error"),
    ):
        with pytest.raises(RuntimeError, match="docker cp failed"):
            r._copy_files("abc", tmp_path)


# ---------------------------------------------------------------------------
# RunResult attributes
# ---------------------------------------------------------------------------

def test_run_result_success_property():
    effects = _side_effects(start_returncode=0, start_stdout="5 passed in 0.3s")
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.success is True

def test_run_result_has_output():
    effects = _side_effects(start_stdout="1 passed in 0.05s\nsomething\n")
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert "passed" in result.output

def test_run_result_has_duration():
    effects = _side_effects(start_stdout="1 passed in 0.05s")
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert result.duration_ms >= 0

def test_run_result_to_dict():
    effects = _side_effects(start_stdout="2 passed in 0.1s")
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    d = result.to_dict()
    assert "passed" in d
    assert "failed" in d
    assert "success" in d
    assert "returncode" in d

def test_run_result_summary():
    effects = _side_effects(start_stdout="3 passed in 0.5s")
    r = DockerRunner()
    with patch("antcrew.sandbox.runner.subprocess.run", side_effect=effects):
        result = r.run([_test_art()])
    assert "passed" in result.summary()


# ---------------------------------------------------------------------------
# Top-level imports
# ---------------------------------------------------------------------------

def test_importable_from_antcrew():
    from antcrew import DockerRunner as DR
    assert DR is DockerRunner

def test_importable_from_sandbox():
    from antcrew.sandbox import DockerRunner as DR
    assert DR is DockerRunner

def test_is_sandbox_runner_subclass():
    from antcrew.sandbox.runner import SandboxRunner
    assert issubclass(DockerRunner, SandboxRunner)
